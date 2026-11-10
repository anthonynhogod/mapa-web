"""Mapa de producao: De->Para, plano/comandos, rotas (upload -> preview -> job), retry e worker."""
import threading
import time
from datetime import date
from html import unescape

import pytest
from werkzeug.security import generate_password_hash

from app.executor import worker as worker_mod
from app.extensions import db
from app.logic.constantes import ColetorPendencias, ConstanteNaoMapeada, invalidar_cache
from app.logic.producao import build_commands, build_plano, totais
from app.models import (
    ExecJob, MapaCredencial, ProdutoProducao, ProdutoProducaoAlias, Registro, Role, Usuario, VendasTmp,
)
from helpers import LINHAS_PRODUCAO, xlsx_producao
from app.utils.format import normalize_str


@pytest.fixture(autouse=True)
def _modo_navegador(app):
    app.config["EXEC_BACKEND"] = "api"      # a producao nao pode ir pela API, mesmo com o sistema neste modo


def _produto(nome, descricao, id_mapa, *aliases):
    p = ProdutoProducao(nome=nome, descricao_busca=descricao, id_mapa=id_mapa)
    db.session.add(p)
    db.session.flush()
    for a in (nome, *aliases):
        db.session.add(ProdutoProducaoAlias(produto_id=p.id, alias=a, alias_norm=normalize_str(a)))
    db.session.commit()
    invalidar_cache("produto_producao")
    return p


def _vinculos():
    _produto("APRESUNTADO RESFRIADO", "APRESUNTADO", 17176)
    _produto("BACON", "BACON", 17189)
    _produto("BACON RESFRIADO FATIADO", "BACON FATIADO", 17186)


RECORDS = [
    {"produto": "APRESUNTADO RESFRIADO", "codigo": "1", "quantidade": 300.5},
    {"produto": "BACON", "codigo": "2", "quantidade": 15.26},
]


# ---------------------------------------------------------------- plano
def test_plano_resolve_de_para_e_gera_comandos(app):
    _vinculos()
    plano = build_plano(RECORDS)
    assert [(i["id"], i["descricao"], i["quantidade"]) for i in plano] == [
        (17176, "APRESUNTADO", 300.5), (17189, "BACON", 15.26)]
    cmds = build_commands(plano, competencia="09/2026", numero_sif="167")
    assert cmds == [
        'abrirInclusaoProducao("09/2026", "167")',
        'incluirProdutoProducao("APRESUNTADO", 17176, 300.5)',
        'incluirProdutoProducao("BACON", 17189, 15.26)',
        'finalizarRegistroProducao("09/2026", 2)',
    ]
    assert totais(plano) == {"itens": 2, "quantidade": 315.76}


def test_produtos_com_o_mesmo_id_do_portal_continuam_lancamentos_separados(app):
    """O Ministerio exige um lancamento por produto, mesmo com a mesma descricao e o mesmo id."""
    _produto("MIUDOS SALGADOS", "MIUDOS SALGADOS", 18321)
    _produto("MIUDOS SALGADOS (MASCARA)", "MIUDOS SALGADOS", 18321)
    plano = build_plano([{"produto": "MIUDOS SALGADOS (MASCARA)", "quantidade": 0.25}, {"produto": "MIUDOS SALGADOS", "quantidade": 10.1}])
    assert [(i["produto"], i["id"], i["quantidade"]) for i in plano] == [
        ("MIUDOS SALGADOS", 18321, 10.1), ("MIUDOS SALGADOS (MASCARA)", 18321, 0.25)]
    cmds = build_commands(plano, competencia="09/2026", numero_sif="167")
    assert cmds[1:3] == ['incluirProdutoProducao("MIUDOS SALGADOS", 18321, 10.1)',
                         'incluirProdutoProducao("MIUDOS SALGADOS", 18321, 0.25)']
    assert cmds[-1] == 'finalizarRegistroProducao("09/2026", 2)'
    assert totais(plano) == {"itens": 2, "quantidade": 10.35}


def test_sem_vinculo_vira_pendencia_e_sem_coletor_levanta(app):
    _vinculos()
    coletor = ColetorPendencias()
    plano = build_plano(RECORDS + [{"produto": "PRODUTO NOVO", "quantidade": 3}], coletor)
    assert len(plano) == 2
    pend = coletor.listar()
    assert [(p["tipo"], p["valor"]) for p in pend] == [("produto_producao", "PRODUTO NOVO")]
    assert "Produtos (produção)" in pend[0]["como"]
    with pytest.raises(ConstanteNaoMapeada):
        build_plano(RECORDS + [{"produto": "PRODUTO NOVO", "quantidade": 3}])


def test_vinculo_sem_id_do_portal_e_pendencia(app):
    _produto("BACON", "BACON", None)
    coletor = ColetorPendencias()
    assert build_plano([{"produto": "BACON", "quantidade": 1}], coletor) == []
    assert not coletor.vazio
    with pytest.raises(ConstanteNaoMapeada):
        build_plano([{"produto": "BACON", "quantidade": 1}])


def test_vinculo_inativo_nao_resolve(app):
    p = _produto("BACON", "BACON", 17189)
    p.ativo = False
    db.session.commit()
    invalidar_cache("produto_producao")
    coletor = ColetorPendencias()
    assert build_plano([{"produto": "BACON", "quantidade": 1}], coletor) == [] and not coletor.vazio


# ---------------------------------------------------------------- rotas
@pytest.fixture()
def client(app):
    return app.test_client()


def _usuario(nome="op", admin=False):
    u = Usuario(nome=nome, senha=generate_password_hash("pw", method="pbkdf2:sha256"),
                role=Role.ADMIN if admin else Role.USER)
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, nome="op"):
    assert client.post("/auth/login", data={"nome": nome, "senha": "pw"}).status_code == 302


def _cred(user):
    c = MapaCredencial(owner_user_id=user.id, usuario_app="usr", numero_sif="167", especie="suino")
    c.senha = "segredo"
    db.session.add(c)
    db.session.commit()


def _upload(client, buf=None, **extra):
    data = {"file": (buf or xlsx_producao(LINHAS_PRODUCAO), "producao.xlsx"), **extra}
    return client.post("/producao/novo", data=data, content_type="multipart/form-data")


def test_rotas_exigem_login(client):
    assert client.get("/producao/").status_code == 302
    assert client.post("/producao/novo").status_code == 302


def test_upload_cria_registro_mensal_e_preview(app, client):
    u = _usuario(); _cred(u); _vinculos(); _login(client)
    assert client.get("/producao/novo").status_code == 200
    r = _upload(client)
    assert r.status_code == 302 and "/preview" in r.headers["Location"]

    reg = Registro.query.one()
    assert reg.tipo == "producao" and reg.status == "AT" and reg.lancamento == "producao"
    assert (reg.periodo_ini.isoformat(), reg.periodo_fim.isoformat()) == ("2026-09-01", "2026-09-30")
    assert reg.periodo_label == "09/2026" and reg.tipo_label == "Produção"
    assert VendasTmp.query.one().model == "producao"

    html = unescape(client.get(r.headers["Location"]).get_data(as_text=True))
    assert 'abrirInclusaoProducao("09/2026", "167")' in html and "17176" in html
    assert 'finalizarRegistroProducao("09/2026", 3)' in html
    assert client.get("/producao/").status_code == 200
    assert "09/2026" in client.get("/producao/").get_data(as_text=True)


def test_upload_com_erro_nao_cria_registro(app, client):
    _usuario(); _login(client)
    r = _upload(client, xlsx_producao(LINHAS_PRODUCAO, dias=("30/09/2026", "01/10/2026", "02/10/2026")))
    assert r.status_code == 400 and Registro.query.count() == 0


def test_upload_sem_mes_exige_competencia_manual(app, client):
    _usuario(); _login(client)
    sem_dias = xlsx_producao([("BACON", "1", [], 10)], dias=())
    assert _upload(client, sem_dias).status_code == 400 and Registro.query.count() == 0
    r = _upload(client, xlsx_producao([("BACON", "1", [], 10)], dias=()), competencia="10/2026")
    assert r.status_code == 302 and Registro.query.one().periodo_label == "10/2026"


def test_reenviar_planilha_do_mesmo_mes_reaproveita_o_registro_em_andamento(app, client):
    _usuario(); _login(client)
    _upload(client); _upload(client)
    assert Registro.query.count() == 1 and VendasTmp.query.count() == 1


def test_mes_ja_finalizado_e_recusado(app, client):
    _usuario(); _login(client)
    _upload(client)
    reg = Registro.query.one(); reg.status = "FZ"; db.session.commit()
    assert _upload(client).status_code == 409


def test_preview_com_pendencia_lista_e_nao_gera_comandos(app, client):
    _usuario(); _login(client)
    r = _upload(client)
    html = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Produto (produção)" in html and "APRESUNTADO RESFRIADO" in html and "abrirInclusaoProducao" not in html


def test_finalizar_cria_job_do_navegador(app, client):
    u = _usuario(); _cred(u); _vinculos(); _login(client)
    r = _upload(client)
    reg = Registro.query.one()
    r = client.post(f"/producao/{reg.id}/finalizar")
    assert r.status_code == 302
    db.session.expire_all()
    job = ExecJob.query.one()
    assert job.status == "ESPERA" and Registro.query.one().status == "PT"
    assert job.commands[0] == 'abrirInclusaoProducao("09/2026", "167")'
    assert job.commands[-1] == 'finalizarRegistroProducao("09/2026", 3)'
    assert len(job.commands) == 5                       # abrir + 3 produtos + finalizar
    assert (job.meta or {}).get("backend") != "api" and job.meta["modulo"] == "producao"
    assert job.meta["totais"]["itens"] == 3 and len(job.meta["plano"]) == 3
    # segundo clique: ja ha job ativo
    assert client.post(f"/producao/{reg.id}/finalizar").status_code == 302
    assert ExecJob.query.count() == 1


def test_finalizar_vetado_sem_credencial_ou_com_pendencia(app, client):
    u = _usuario(); _login(client)
    _upload(client)
    reg = Registro.query.one()
    assert client.post(f"/producao/{reg.id}/finalizar").status_code == 302      # sem credencial
    assert ExecJob.query.count() == 0
    _cred(u)
    client.post(f"/producao/{reg.id}/finalizar")                               # com credencial, sem De->Para
    assert ExecJob.query.count() == 0 and Registro.query.one().status == "AT"


def test_registro_de_outro_usuario_da_404(app, client):
    _usuario("a"); _usuario("b"); _login(client, "a")
    _upload(client)
    reg = Registro.query.one()
    c2 = app.test_client(); _login(c2, "b")
    assert c2.get(f"/producao/{reg.id}/preview").status_code == 404
    assert c2.post(f"/producao/{reg.id}/finalizar").status_code == 404


# ---------------------------------------------------------------- admin / constantes
def test_admin_cadastra_produto_de_producao(app, client):
    _usuario("adm", admin=True); _login(client, "adm")
    assert client.get("/admin/constantes/produtos-producao").status_code == 200
    r = client.post("/admin/constantes/produtos-producao/novo", data={
        "nome": "BACON", "ativo": "1", "id_mapa": "17189", "descricao_busca": "BACON FATIADO"})
    assert r.status_code == 302
    p = ProdutoProducao.query.one()
    assert (p.nome, p.id_mapa, p.descricao_busca) == ("BACON", 17189, "BACON FATIADO")
    assert [a.alias_norm for a in p.aliases] == ["bacon"]
    assert build_plano([{"produto": "Bacon", "quantidade": 1}])[0]["id"] == 17189   # cache invalidado
    assert "Produção" in client.get("/admin/constantes/produtos-producao").get_data(as_text=True)


# ---------------------------------------------------------------- retry
def _registro_com_job(app, status="FALHOU"):
    u = _usuario()
    _cred(u)
    _vinculos()
    reg = Registro(data=date(2026, 9, 1), periodo_ini=date(2026, 9, 1), periodo_fim=date(2026, 9, 30),
                   tipo="producao", lancamento="producao", especie="suino", user_id=u.id, status="ER")
    db.session.add(reg)
    db.session.flush()
    db.session.add(VendasTmp(registro_id=reg.id, uploaded_by=u.id, filename="p.xlsx", model="producao",
                             payload={"records": RECORDS, "meta": {}}))
    job = ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="producao", commands=["velho()"],
                  meta={"modulo": "producao", "numero_sif": "167"}, status=status, errors=["x"])
    db.session.add(job)
    db.session.commit()
    return reg.id, job.id


def test_retry_reconstroi_comandos_com_o_de_para_atual(app):
    reg_id, job_id = _registro_com_job(app)
    adm = _usuario("adm", admin=True)
    c = app.test_client(); _login(c, "adm")
    # o vinculo mudou depois do job original: o retry usa o id ATUAL
    p = ProdutoProducao.query.filter_by(nome="BACON").one(); p.id_mapa = 99999; db.session.commit()
    invalidar_cache("produto_producao")
    assert c.post(f"/admin/retry-job/{job_id}").status_code == 302
    db.session.expire_all()
    job = db.session.get(ExecJob, job_id)
    assert job.status == "ESPERA" and db.session.get(Registro, reg_id).status == "PT"
    assert job.commands == [
        'abrirInclusaoProducao("09/2026", "167")',
        'incluirProdutoProducao("APRESUNTADO", 17176, 300.5)',
        'incluirProdutoProducao("BACON", 99999, 15.26)',
        'finalizarRegistroProducao("09/2026", 2)',
    ]
    assert job.meta["totais"]["itens"] == 2


def test_retry_vetado_quando_o_vinculo_sumiu(app):
    _, job_id = _registro_com_job(app)
    _usuario("adm", admin=True)
    c = app.test_client(); _login(c, "adm")
    ProdutoProducao.query.filter_by(nome="BACON").delete()
    db.session.commit()
    invalidar_cache("produto_producao")
    c.post(f"/admin/retry-job/{job_id}")
    db.session.expire_all()
    job = db.session.get(ExecJob, job_id)
    assert job.status == "FALHOU" and job.commands == ["velho()"]


# ---------------------------------------------------------------- worker
class NavFalso:
    instancias = []
    WORKER_ATTEMPTS = 1
    falhar_em = None

    def __init__(self, mode="HIDE", speed_profile=None, tap_ui=None, module_script=None):
        self.cmds, self.login_args = [], None
        NavFalso.instancias.append(self)

    def login(self, *args, **kw):
        self.login_args = args

    def executar_comando(self, cmd, ctx=""):
        self.cmds.append(cmd)
        return "Erro: [ADD_INCERTO] x" if NavFalso.falhar_em == cmd else "OK: feito"

    def _collect_console_errors(self):
        return []

    def quit(self):
        pass


class NavErrado(NavFalso):
    def __init__(self, *a, **k):
        raise AssertionError("job de producao nao pode abrir o navegador de abate/comercializacao")


def _rodar(app, job_id, timeout=15):
    stop = threading.Event()
    w = worker_mod.Worker(app, name="w-prod", profile="prod", stop_event=stop)
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    fim, status = time.time() + timeout, None
    while time.time() < fim:
        db.session.expire_all()
        status = db.session.get(ExecJob, job_id).status
        if status in ("SUCESSO", "FALHOU"):
            break
        time.sleep(0.1)
    stop.set(); t.join(5)
    db.session.expire_all()
    return status


@pytest.fixture()
def worker_cenario(app, monkeypatch):
    NavFalso.instancias, NavFalso.falhar_em = [], None
    monkeypatch.setattr(worker_mod, "NavegadorProducao", NavFalso)
    monkeypatch.setattr(worker_mod, "NavegadorComercializacao", NavErrado)
    monkeypatch.setattr(worker_mod, "Navegador", NavErrado)
    monkeypatch.setattr(worker_mod, "ensure_valid_credential", lambda cred, dia: (True, None))
    reg_id, job_id = _registro_com_job(app, status="ESPERA")
    job = db.session.get(ExecJob, job_id)
    job.commands = build_commands(build_plano(RECORDS), competencia="09/2026", numero_sif="167")
    job.status = "ESPERA"; job.errors = []
    db.session.get(Registro, reg_id).status = "PT"
    db.session.commit()
    return reg_id, job_id


def test_worker_usa_o_navegador_de_producao(app, worker_cenario):
    reg_id, job_id = worker_cenario
    assert _rodar(app, job_id) == "SUCESSO"
    nav = NavFalso.instancias[0]
    assert nav.login_args == ("09/2026", "usr", "segredo", "167")      # competencia, usuario, senha, sif
    job = db.session.get(ExecJob, job_id)
    assert nav.cmds == job.commands and job.progress == len(job.commands)
    assert db.session.get(Registro, reg_id).status == "FZ"


def test_worker_para_no_primeiro_erro_sem_repetir(app, worker_cenario):
    reg_id, job_id = worker_cenario
    NavFalso.falhar_em = db.session.get(ExecJob, job_id).commands[1]
    assert _rodar(app, job_id) == "FALHOU"
    job = db.session.get(ExecJob, job_id)
    assert len(NavFalso.instancias[0].cmds) == 2 and "[ADD_INCERTO]" in job.errors[0]
    assert db.session.get(Registro, reg_id).status == "ER"


# ---------------------------------------------------------------- migration
def test_migration_cria_e_remove_as_tabelas_de_para_da_producao(app):
    """Cobre o caminho `flask db upgrade` (o resto da suite usa create_all)."""
    from flask_migrate import downgrade, stamp, upgrade
    from sqlalchemy import inspect
    db.metadata.drop_all(bind=db.engine, tables=[db.metadata.tables["produto_producao_alias"],
                                                  db.metadata.tables["produto_producao"]])
    assert "produto_producao" not in inspect(db.engine).get_table_names()
    stamp(revision="f7a1c9d3b5e2")
    upgrade(revision="a9d3e5f7b1c2")
    insp = inspect(db.engine)
    assert {"nome", "descricao_busca", "id_mapa", "ativo"} <= {c["name"] for c in insp.get_columns("produto_producao")}
    assert {"produto_id", "alias", "alias_norm"} <= {c["name"] for c in insp.get_columns("produto_producao_alias")}
    downgrade(revision="f7a1c9d3b5e2")
    assert "produto_producao" not in inspect(db.engine).get_table_names()
    upgrade(revision="a9d3e5f7b1c2")          # deixa o schema como o teardown espera
