"""Conferencia/complemento no portal: worker com um portal FALSO que guarda estado (nao toca em nada real)."""
import json
import re
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.executor import worker as worker_mod
from app.extensions import db
from app.models import ExecJob, MapaCredencial, Registro, Role, Usuario, Verificacao
from app.runner.exceptions import FalhaNoJavascript
from test_worker_comercializacao import _rodar_ate_terminar

RX_ESTADO = re.compile(r'incluirEstadoVenda\("(\w+)"')
RX_PROD = re.compile(r'incluirProdutoVenda\("(\w+)", (-?\d+), ([\d.]+), "([^"]*)", (\d+)')


class NavPortal:
    """Simula o registro aberto para alteracao: transacoes por UF e linhas de produto (sem agrupar)."""
    WORKER_ATTEMPTS = 1
    estado = None            # {"transacoes": {uf: id}, "linhas": [(uf, nome, qtd)], "perde": set((uf, qtd)), "sem_registro": bool}
    instancias = []

    def __init__(self, mode="HIDE", speed_profile=None, tap_ui=None, module_script=None):
        self.cmds, self.login_kw = [], None
        NavPortal.instancias.append(self)

    def login(self, *args, **kw):
        self.login_kw = kw
        if NavPortal.estado.get("sem_registro"):
            raise FalhaNoJavascript(dia="x", erro="Erro: [REGISTRO_INEXISTENTE] nao ha registro neste periodo (nao cria)")

    def injetar_scripts(self, *a):
        pass

    def _collect_console_errors(self):
        return []

    def quit(self):
        pass

    def executar_comando(self, cmd, ctx=""):
        e = NavPortal.estado
        self.cmds.append(cmd)
        if cmd.startswith("lerRegistroComercializacao"):
            ids = e["transacoes"]
            return "OK: " + json.dumps({
                "transacoes": [{"id": str(i), "uf": uf, "tipo_codigo": "V", "tipo": "Expedição"} for uf, i in ids.items()],
                "produtos": [{"transacao": str(ids[uf]), "produto": nome, "quantidade": q} for uf, nome, q in e["linhas"]],
                "tabela_produtos": True, "paginas": {"transacoes": 1, "produtos": 1}, "lido_em": "2026-11-09T12:00:00Z"})
        m = RX_ESTADO.match(cmd)
        if m:
            e["transacoes"].setdefault(m.group(1), 500 + len(e["transacoes"]))
            return "OK: Estado incluído."
        m = RX_PROD.match(cmd)
        if m:
            uf, _, q, nome, _id = m.groups()
            if (uf, float(q)) not in e.get("perde", set()):          # "perde" = o falso positivo: diz OK e nao grava
                e["linhas"].append((uf, nome.upper(), float(q)))
            return f"OK: Produto {_id} incluído em {uf}."
        return "OK: feito"


@pytest.fixture()
def cenario(app, monkeypatch):
    NavPortal.instancias = []
    NavPortal.estado = {"transacoes": {"MT": 100, "TO": 101}, "linhas": [], "perde": set()}
    monkeypatch.setattr(worker_mod, "NavegadorComercializacao", NavPortal)
    monkeypatch.setattr(worker_mod, "Navegador", NavPortal)
    monkeypatch.setattr(worker_mod, "ensure_valid_credential", lambda cred, dia: (True, None))
    app.config["EXEC_BACKEND"] = "browser"

    u = Usuario(nome="op", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.USER)
    db.session.add(u)
    db.session.commit()
    c = MapaCredencial(owner_user_id=u.id, usuario_app="usr", numero_sif="167", especie="suino")
    c.senha = "s"
    db.session.add(c)
    reg = Registro(data=date(2026, 10, 1), periodo_ini=date(2026, 10, 1), periodo_fim=date(2026, 10, 31),
                   tipo="comercializacao", lancamento="venda", especie="suino", user_id=u.id, status="FZ")
    db.session.add(reg)
    db.session.flush()
    plano = [
        {"uf": "MT", "index": 9, "itens": [
            {"produto": "APRESUNTADO RESFRIADO", "descricao": "Apresuntado", "id": 17178, "quantidade": 4688.7},
            {"produto": "BACON", "descricao": "Bacon", "id": 17189, "quantidade": 1586.99}]},
        {"uf": "TO", "index": 23, "itens": [
            {"produto": "BACON", "descricao": "Bacon", "id": 17189, "quantidade": 1499.21},
            {"produto": "BACON FATIADO", "descricao": "Bacon", "id": 17186, "quantidade": 88.33},
            {"produto": "CARNE CONGELADA SEM OSSO", "descricao": "Carne congelada de suino sem osso", "id": 17347, "quantidade": 889.98}]},
    ]
    db.session.add(ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="venda", commands=["x()"], status="SUCESSO",
                           errors=[], meta={"modulo": "comercializacao", "plano": plano}))
    db.session.commit()
    # o portal tem tudo, MENOS "Bacon MT 1586.99" e "Carne congelada sem osso TO 889.98" (o caso real)
    NavPortal.estado["linhas"] = [("MT", "APRESUNTADO", 4688.7), ("TO", "BACON", 1499.21), ("TO", "BACON", 88.33)]
    return app.test_client(), u, reg


def _login(c):
    c.post("/auth/login", data={"nome": "op", "senha": "pw"})


def _pedir(c, reg, url="/verificacao/portal", **dados):
    return c.post(url, data={"registro": str(reg.id), **dados})


def _job_novo():
    return ExecJob.query.order_by(ExecJob.id.desc()).first()


def test_conferencia_le_sem_alterar_nada_e_aponta_as_linhas_que_faltam(app, cenario):
    c, u, reg = cenario
    _login(c)
    assert _pedir(c, reg).status_code == 302
    job = _job_novo()
    assert job.meta["tipo_job"] == "conferencia" and job.commands == ["lerRegistroComercializacao()"]
    assert _rodar_ate_terminar(app, job.id) == "SUCESSO"

    nav = NavPortal.instancias[0]
    assert nav.login_kw == {"criar_se_faltar": False}                     # nunca cria registro
    assert nav.cmds == ["lerRegistroComercializacao()"]                   # so leu
    assert db.session.get(Registro, reg.id).status == "FZ" and db.session.get(Registro, reg.id).data_fim is None
    v = db.session.get(Verificacao, db.session.get(ExecJob, job.id).meta["verificacao_id"])
    assert v.status == "DIVERGENTE" and v.resumo["fonte"] == "portal"
    falt = v.resultado["linhas_diff"]["faltando"]
    assert [(f["uf"], f["quantidade"], f["id"]) for f in falt] == [("MT", 1586.99, 17189), ("TO", 889.98, 17347)]
    assert len(NavPortal.estado["linhas"]) == 3                           # portal intacto
    html = c.get(f"/verificacao/{v.id}").get_data(as_text=True)
    assert "Faltam no portal (2)" in html and "Complementar o que falta (2 linha(s))" in html


def test_complemento_inclui_so_o_que_falta_salva_e_confere_depois(app, cenario):
    c, u, reg = cenario
    _login(c)
    _pedir(c, reg)
    _rodar_ate_terminar(app, _job_novo().id)
    v1 = Verificacao.query.one()

    r = c.post(f"/verificacao/{v1.id}/complementar")
    assert r.status_code == 302
    job = _job_novo()
    assert job.meta["tipo_job"] == "complemento"
    NavPortal.instancias = []
    assert _rodar_ate_terminar(app, job.id) == "SUCESSO"

    cmds = NavPortal.instancias[0].cmds
    inclui = [x for x in cmds if x.startswith("incluirProdutoVenda")]
    assert cmds[0] == "lerRegistroComercializacao()" and cmds[-1] == "lerRegistroComercializacao()"
    assert "finalizarRegistroComercializacao()" in cmds and cmds.index("finalizarRegistroComercializacao()") > cmds.index(inclui[-1])
    assert len(inclui) == 2 and 'incluirProdutoVenda("MT", -1, 1586.99, "Bacon", 17189' in inclui[0]
    assert not any(x.startswith(("incluirEstadoVenda", "limpar", "verificarRegistroVazio")) for x in cmds)   # so inclui produtos
    assert len(NavPortal.estado["linhas"]) == 5
    v2 = db.session.get(Verificacao, db.session.get(ExecJob, job.id).meta["verificacao_id"])
    assert v2.status == "OK" and v2.resumo["tipo_job"] == "complemento" and v2.resumo["complemento"]["faltando"] == 2
    assert db.session.get(Registro, reg.id).status == "FZ"                # status do lancamento intacto


def test_complemento_e_idempotente_se_nada_falta_nao_inclui_nem_salva(app, cenario):
    c, u, reg = cenario
    NavPortal.estado["linhas"] += [("MT", "BACON", 1586.99), ("TO", "CARNE CONGELADA DE SUINO SEM OSSO", 889.98)]
    _login(c)
    _pedir(c, reg)
    _rodar_ate_terminar(app, _job_novo().id)
    assert Verificacao.query.one().status == "OK"
    # alguem pede complemento mesmo assim (conferencia antiga): como nao falta nada, so le
    v = Verificacao.query.one()
    v.resultado = {**v.resultado, "linhas_diff": {**v.resultado["linhas_diff"], "faltando": [{"uf": "MT", "descricao": "Bacon", "id": 17189, "quantidade": 1586.99}]}}
    db.session.commit()
    NavPortal.instancias = []
    c.post(f"/verificacao/{v.id}/complementar")
    assert _rodar_ate_terminar(app, _job_novo().id) == "SUCESSO"
    assert NavPortal.instancias[0].cmds == ["lerRegistroComercializacao()", "lerRegistroComercializacao()"]
    assert len(NavPortal.estado["linhas"]) == 5                           # nada duplicado


def test_complemento_cria_a_transacao_da_uf_quando_ela_nao_existe_no_portal(app, cenario):
    c, u, reg = cenario
    job_base = ExecJob.query.first()
    plano = job_base.meta["plano"] + [{"uf": "SC", "index": 20, "itens": [
        {"produto": "BACON", "descricao": "Bacon", "id": 17189, "quantidade": 2832.06}]}]
    job_base.meta = {**job_base.meta, "plano": plano}
    db.session.commit()
    _login(c)
    _pedir(c, reg)
    _rodar_ate_terminar(app, _job_novo().id)
    NavPortal.instancias = []
    c.post(f"/verificacao/{Verificacao.query.one().id}/complementar")
    assert _rodar_ate_terminar(app, _job_novo().id) == "SUCESSO"
    cmds = NavPortal.instancias[0].cmds
    estado_cmd = [x for x in cmds if x.startswith("incluirEstadoVenda")]
    assert len(estado_cmd) == 1 and estado_cmd[0].startswith('incluirEstadoVenda("SC", 24,')   # indice vem do De->Para (SC=24)
    assert cmds.index(estado_cmd[0]) < cmds.index([x for x in cmds if '"SC"' in x and x.startswith("incluirProduto")][0])
    assert Verificacao.query.order_by(Verificacao.id.desc()).first().status == "OK"


def test_complemento_que_nao_resolve_falha_o_job_mesmo_com_todos_os_comandos_ok(app, cenario):
    """O falso positivo: o portal responde OK mas nao grava a linha. A leitura final pega."""
    c, u, reg = cenario
    NavPortal.estado["perde"] = {("MT", 1586.99)}
    _login(c)
    _pedir(c, reg)
    _rodar_ate_terminar(app, _job_novo().id)
    c.post(f"/verificacao/{Verificacao.query.one().id}/complementar")
    job_id = _job_novo().id
    assert _rodar_ate_terminar(app, job_id) == "FALHOU"
    job = db.session.get(ExecJob, job_id)
    assert "ainda faltam 1 linha" in job.errors[0] and "retornaram OK" in job.errors[0]
    assert Verificacao.query.order_by(Verificacao.id.desc()).first().status == "DIVERGENTE"
    assert db.session.get(Registro, reg.id).status == "FZ"


def test_registro_inexistente_no_portal_falha_com_mensagem_clara_e_sem_criar(app, cenario):
    c, u, reg = cenario
    NavPortal.estado["sem_registro"] = True
    _login(c)
    _pedir(c, reg)
    job_id = _job_novo().id
    assert _rodar_ate_terminar(app, job_id) == "FALHOU"
    assert "[REGISTRO_INEXISTENTE]" in db.session.get(ExecJob, job_id).errors[0] and "Traceback" not in db.session.get(ExecJob, job_id).errors[0]
    assert db.session.get(Registro, reg.id).status == "FZ" and Verificacao.query.count() == 0


# ------------------------------------------------------------------ vetos e telas
def test_vetos_ao_criar_job_de_portal(app, cenario):
    c, u, reg = cenario
    _login(c)
    app.config["EXEC_BACKEND"] = "api"
    assert ExecJob.query.count() == 1 and "modo navegador" in c.post("/verificacao/portal", data={"registro": str(reg.id)}, follow_redirects=True).get_data(as_text=True)
    app.config["EXEC_BACKEND"] = "browser"
    ativo = ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="x", commands=["x()"], status="ESPERA", errors=[], meta={})
    db.session.add(ativo)
    db.session.commit()
    assert "job ativo" in _pedir(c, reg).headers.get("Location", "") or "job ativo" in c.post("/verificacao/portal", data={"registro": str(reg.id)}, follow_redirects=True).get_data(as_text=True)
    ativo.status = "SUCESSO"
    MapaCredencial.query.delete()
    db.session.commit()
    assert "Credenciais do MAPA" in c.post("/verificacao/portal", data={"registro": str(reg.id)}, follow_redirects=True).get_data(as_text=True)
    assert ExecJob.query.count() == 2                                       # nenhum job novo foi criado


def test_complementar_exige_conferencia_do_portal_com_linhas_faltando(app, cenario):
    c, u, reg = cenario
    _login(c)
    v = Verificacao(user_id=u.id, registro_id=reg.id, origem_base="job_plano", arquivo_mapa="r.xlsx", status="DIVERGENTE",
                    resumo={"totais": {"nosso": 1.0, "mapa": 0.0, "diff": -1.0, "celulas": 1, "divergentes": 1}, "avisos": [], "linhas_relatorio": 0, "itens_enviados": 1}, resultado={"celulas": [], "por_uf": [], "por_produto": []})
    db.session.add(v)
    db.session.commit()
    r = c.post(f"/verificacao/{v.id}/complementar", follow_redirects=True)
    assert "não tem linhas a complementar" in r.get_data(as_text=True) and ExecJob.query.count() == 1


def test_threads_mostra_a_etapa_e_nao_oferece_retry(app, cenario):
    c, u, reg = cenario
    u.role = Role.ADMIN
    db.session.commit()
    _login(c)
    _pedir(c, reg)
    job = _job_novo()
    job.status, job.errors = "FALHOU", ["x"]
    db.session.commit()
    html = c.get("/admin/threads").get_data(as_text=True)
    assert "Leitura do portal" in html and "Retry" not in html.split("Leitura do portal", 1)[1].split("</tr>")[0]
    r = c.post(f"/admin/retry-job/{job.id}", follow_redirects=True)
    assert "não tem retry" in r.get_data(as_text=True) and db.session.get(ExecJob, job.id).status == "FALHOU"


def _lancamento(reg, u, plano=True):
    meta = {"modulo": "comercializacao", "lancamento": "venda", "numero_sif": "167",
            "periodo": {"ini": "01/10/2026", "fim": "31/10/2026"}}
    if plano:
        meta["plano"] = [{"uf": "MT", "index": 9, "itens": [{"produto": "BACON", "descricao": "Bacon", "id": 17189, "quantidade": 1586.99}]}]
    j = ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="venda", status="ESPERA", errors=[], meta=meta,
                commands=["verificarRegistroVazio(\"Venda\", [\"Venda\"])", 'incluirProdutoVenda("MT", 9, 1586.99, "Bacon", 17189, "Venda")',
                          "finalizarRegistroComercializacao()"])
    db.session.add(j)
    db.session.commit()
    return j


def test_lancamento_concluido_enfileira_conferencia_automatica_somente_leitura(app, cenario):
    c, u, reg = cenario
    reg.status = "PT"
    db.session.commit()
    job = _lancamento(reg, u)
    assert _rodar_ate_terminar(app, job.id) == "SUCESSO"
    auto = [j for j in ExecJob.query.all() if (j.meta or {}).get("automatica")]
    assert len(auto) == 1
    j = auto[0]
    assert j.meta["tipo_job"] == "conferencia" and j.meta["job_base"] == job.id and j.commands == ["lerRegistroComercializacao()"]
    assert j.meta["plano_base"][0]["descricao"] == "Bacon" and j.meta["status_original"] == "FZ"


def test_conferencia_automatica_nao_roda_sem_plano_nem_quando_desligada_nem_depois_de_outra_conferencia(app, cenario):
    from app.executor.conferencia import encadear_conferencia
    c, u, reg = cenario
    j_velho = _lancamento(reg, u, plano=False)
    assert encadear_conferencia(j_velho, reg, app.config) is None                       # job antigo, sem plano
    j = _lancamento(reg, u)
    app.config["AUTO_CONFERIR"] = False
    assert encadear_conferencia(j, reg, app.config) is None                             # desligada
    app.config["AUTO_CONFERIR"] = True
    j.meta = {**j.meta, "tipo_job": "conferencia"}
    assert encadear_conferencia(j, reg, app.config) is None                             # nao encadeia conferencia em conferencia
