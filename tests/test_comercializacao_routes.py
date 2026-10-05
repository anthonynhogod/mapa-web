import io

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import ExecJob, MapaCredencial, ProdutoVenda, Registro, Role, Usuario, VendasTmp
from helpers import LINHAS_OK, xlsx_vendas


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
    r = client.post("/auth/login", data={"nome": nome, "senha": "pw"})
    assert r.status_code == 302


def _upload(client, buf, nome="vendas.xlsx", **extra):
    data = {"file": (buf, nome), "lancamento": "venda", **extra}
    return client.post("/comercializacao/novo", data=data, content_type="multipart/form-data")


def _cred(user):
    c = MapaCredencial(owner_user_id=user.id, usuario_app="u", numero_sif="167", especie="suino")
    c.senha = "segredo"
    db.session.add(c)
    db.session.commit()


def test_rotas_exigem_login(client):
    assert client.get("/comercializacao/").status_code == 302
    assert client.post("/comercializacao/novo").status_code == 302


def test_upload_cria_registro_e_preview(app, client):
    _usuario()
    _login(client)
    assert client.get("/comercializacao/novo").status_code == 200

    r = _upload(client, xlsx_vendas(LINHAS_OK))
    assert r.status_code == 302 and "/preview" in r.headers["Location"]

    reg = Registro.query.one()
    assert reg.tipo == "comercializacao" and reg.status == "AT"
    assert (reg.periodo_ini.isoformat(), reg.periodo_fim.isoformat()) == ("2026-03-01", "2026-03-31")
    assert reg.periodo_label == "01/03/2026 a 31/03/2026"
    tmp = VendasTmp.query.one()
    assert tmp.payload["meta"]["counts"]["output_records"] == 5

    p = client.get(r.headers["Location"])
    html = p.get_data(as_text=True)
    assert p.status_code == 200
    assert "Apresuntado" in html and "incluirEstadoVenda" in html and "17189" in html
    assert "Rio Grande" not in html  # nada inventado


def test_periodo_manual_quando_planilha_sem_titulo(app, client):
    _usuario()
    _login(client)
    r = _upload(client, xlsx_vendas(LINHAS_OK, titulo=False))
    assert r.status_code == 400 and Registro.query.count() == 0
    r = _upload(client, xlsx_vendas(LINHAS_OK, titulo=False), periodo_ini="01/02/2026", periodo_fim="28/02/2026")
    assert r.status_code == 302
    assert Registro.query.one().periodo_ini.isoformat() == "2026-02-01"


def test_planilha_com_erro_nao_cria_nada(app, client):
    _usuario()
    _login(client)
    r = _upload(client, xlsx_vendas([("BACON", "1", "RS", "abc")]))
    assert r.status_code == 400
    assert Registro.query.count() == 0 and VendasTmp.query.count() == 0
    r = _upload(client, io.BytesIO(b"x"), nome="vendas.txt")
    assert r.status_code == 400


def test_reenvio_em_registro_aberto_substitui_e_finalizado_bloqueia(app, client):
    _usuario()
    _login(client)
    _upload(client, xlsx_vendas(LINHAS_OK))
    r = _upload(client, xlsx_vendas([("BACON", "1", "SC", 9)]))
    assert r.status_code == 302
    assert Registro.query.count() == 1
    assert VendasTmp.query.one().payload["meta"]["counts"]["output_records"] == 1

    reg = Registro.query.one()
    reg.status = "FZ"
    db.session.commit()
    r = _upload(client, xlsx_vendas(LINHAS_OK))
    assert r.status_code == 409 and Registro.query.count() == 1


def test_pendencia_bloqueia_preview_e_finalizar(app, client):
    u = _usuario()
    _login(client)
    _cred(u)
    _upload(client, xlsx_vendas([("PRODUTO NOVO XYZ", "9", "RS", 1), ("BACON", "1", "RS", 1)]))
    reg = Registro.query.one()
    html = client.get(f"/comercializacao/{reg.id}/preview").get_data(as_text=True)
    assert "Validação bloqueada" in html and "PRODUTO NOVO XYZ" in html and "Produto (vendas)" in html
    assert "incluirEstadoVenda" not in html

    r = client.post(f"/comercializacao/{reg.id}/finalizar")
    assert r.status_code == 302 and "/preview" in r.headers["Location"]
    assert ExecJob.query.count() == 0 and db.session.get(Registro, reg.id).status == "AT"


def test_finalizar_cria_job_com_comandos(app, client):
    u = _usuario()
    _login(client)
    _upload(client, xlsx_vendas(LINHAS_OK))
    reg = Registro.query.one()

    # sem credencial: nao cria job
    r = client.post(f"/comercializacao/{reg.id}/finalizar")
    assert "credenciais" in r.headers["Location"] and ExecJob.query.count() == 0

    _cred(u)
    r = client.post(f"/comercializacao/{reg.id}/finalizar")
    assert r.status_code == 302
    job = ExecJob.query.one()
    assert job.status == "ESPERA" and job.gta_source == "venda"
    assert job.commands[0] == 'verificarRegistroVazio("Venda", ["Venda"])' and job.commands[-1] == "finalizarRegistroComercializacao()"
    assert sum(c.startswith("incluirEstadoVenda") for c in job.commands) == 2
    assert sum(c.startswith("incluirProdutoVenda") for c in job.commands) == 5
    assert job.meta["modulo"] == "comercializacao" and job.meta["numero_sif"] == "167"
    assert job.meta["lancamento"] == "venda" and job.gta_source == "venda"
    assert job.meta["periodo"] == {"ini": "01/03/2026", "fim": "31/03/2026"}
    assert db.session.get(Registro, reg.id).status == "PT"

    # idempotencia: segundo POST nao duplica
    client.post(f"/comercializacao/{reg.id}/finalizar")
    assert ExecJob.query.count() == 1


def test_isolamento_entre_usuarios(app, client):
    _usuario("a")
    _usuario("b")
    _login(client, "a")
    _upload(client, xlsx_vendas(LINHAS_OK))
    reg = Registro.query.one()
    client.get("/auth/logout")
    _login(client, "b")
    assert client.get(f"/comercializacao/{reg.id}/preview").status_code == 404
    assert client.post(f"/comercializacao/{reg.id}/finalizar").status_code == 404


def test_listas_separadas_por_modulo(app, client):
    u = _usuario()
    _login(client)
    _upload(client, xlsx_vendas(LINHAS_OK))
    from datetime import date
    db.session.add(Registro(data=date(2026, 3, 5), especie="suino", user_id=u.id))   # abate
    db.session.commit()
    prontos = client.get("/registros/prontos").get_data(as_text=True)
    assert "05/03/2026" in prontos and "01/03/2026 a 31/03/2026" not in prontos
    lista = client.get("/comercializacao/").get_data(as_text=True)
    assert "01/03/2026 a 31/03/2026" in lista
    assert "Comercialização" in client.get("/main").get_data(as_text=True)


# ---------------- admin: De -> Para ----------------
def test_admin_crud_produto_venda(app, client):
    _usuario("adm", admin=True)
    _login(client, "adm")
    r = client.get("/admin/constantes/produtos-venda")
    assert r.status_code == 200 and "BACON RESFRIADO FATIADO" in r.get_data(as_text=True)
    assert client.get("/admin/constantes/estados-venda").status_code == 200

    # cria
    r = client.post("/admin/constantes/produtos-venda/novo", data={
        "nome": "SALAME ITALIANO", "descricao_busca": "Salame", "id_mapa": "18543", "ativo": "1"})
    assert r.status_code == 302
    novo = ProdutoVenda.query.filter_by(nome="SALAME ITALIANO").one()
    assert (novo.descricao_busca, novo.id_mapa) == ("Salame", 18543)

    # resolve na hora (cache invalidado)
    from app.logic.comercializacao import build_plano
    assert build_plano([{"produto": "Salame Italiano", "uf": "RS", "quantidade": 1}])[0]["itens"][0]["id"] == 18543

    # campos obrigatorios
    r = client.post("/admin/constantes/produtos-venda/novo", data={"nome": "X", "id_mapa": "1", "ativo": "1"})
    assert ProdutoVenda.query.filter_by(nome="X").count() == 0
    r = client.post("/admin/constantes/produtos-venda/novo", data={
        "nome": "Y", "descricao_busca": "y", "id_mapa": "0", "ativo": "1"})
    assert ProdutoVenda.query.filter_by(nome="Y").count() == 0

    # edita id e apelido
    client.post(f"/admin/constantes/produtos-venda/{novo.id}/editar", data={
        "nome": "SALAME ITALIANO", "descricao_busca": "Salame", "id_mapa": "99999", "ativo": "1"})
    client.post(f"/admin/constantes/produtos-venda/{novo.id}/alias", data={"alias": "Salame It."})
    assert build_plano([{"produto": "SALAME IT", "uf": "RS", "quantidade": 1}])[0]["itens"][0]["id"] == 99999


def test_admin_exige_papel_admin(app, client):
    _usuario("comum")
    _login(client, "comum")
    assert client.get("/admin/constantes/produtos-venda").status_code == 403
    assert client.post("/admin/constantes/produtos-venda/novo", data={"nome": "A"}).status_code == 403


def test_excluir_registro_comercializacao_e_conflito_com_abate(app, client):
    u = _usuario()
    _login(client)
    _upload(client, xlsx_vendas(LINHAS_OK))                      # data = 2026-03-01 (inicio do periodo)
    reg = Registro.query.one()
    # um dia de abate em 01/03/2026 NAO conflita com o registro de comercializacao
    r = client.post("/registros/novo", data={"dia": "2026-03-01", "especie": "suino", "obs": ""})
    assert r.status_code == 302 and Registro.query.filter_by(tipo="abate").count() == 1
    r = client.post(f"/registros/{reg.id}/excluir", data={"confirm": "yes"})
    assert r.status_code == 302 and "/comercializacao/" in r.headers["Location"]
    assert Registro.query.filter_by(tipo="comercializacao").count() == 0 and VendasTmp.query.count() == 0


def test_tipo_sem_parser_e_conflito_por_tipo(app, client):
    from app.models import TipoLancamento
    _usuario()
    _login(client)
    db.session.add(TipoLancamento(codigo="recebimento", nome="Recebimento", rotulo_portal="Recebimento",
                                  tipo_transacao_idx=2, ambito_idx=1, operador_idx=2))
    db.session.commit()
    html = client.get("/comercializacao/novo").get_data(as_text=True)
    assert "Venda" in html and "Recebimento (layout ainda não suportado)" in html

    r = _upload(client, xlsx_vendas(LINHAS_OK), lancamento="recebimento")
    assert r.status_code == 400 and Registro.query.count() == 0

    assert _upload(client, xlsx_vendas(LINHAS_OK)).status_code == 302
    assert Registro.query.one().lancamento == "venda"
    assert "venda" in client.get("/comercializacao/").get_data(as_text=True)


def test_admin_tipos_lancamento(app, client):
    from app.models import TipoLancamento
    _usuario("adm", admin=True)
    _login(client, "adm")
    assert "venda" in client.get("/admin/constantes/tipos-lancamento").get_data(as_text=True)
    ok = {"codigo": "Expedição", "nome": "Expedição", "rotulo_portal": "Expedição",
          "tipo_transacao_idx": "3", "ambito_idx": "1", "operador_idx": "2", "ativo": "1"}
    assert client.post("/admin/constantes/tipos-lancamento/novo", data=ok).status_code == 302
    t = TipoLancamento.query.filter_by(codigo="expedicao").one()
    assert (t.tipo_transacao_idx, t.rotulo_portal) == (3, "Expedição")
    # indice invalido nao grava; codigo nao muda na edicao
    assert client.post("/admin/constantes/tipos-lancamento/novo", data={**ok, "codigo": "x1", "ambito_idx": "0"}).status_code == 302
    assert TipoLancamento.query.filter_by(codigo="x1").count() == 0
    client.post(f"/admin/constantes/tipos-lancamento/{t.id}/editar", data={**ok, "codigo": "outro", "nome": "Saída"})
    t = db.session.get(TipoLancamento, t.id)
    assert (t.codigo, t.nome) == ("expedicao", "Saída")
