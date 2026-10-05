"""Modo webservice: preview (JSON), pendencias, job e execucao pelo worker (cliente falso)."""
import json
import threading
import time

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.logic import constantes
from app.mapa_api import execucao, montagem, payloads
from app.mapa_api.client import ApiError
from app.executor import worker as worker_mod
from app.models import ExecJob, MapaCredencial, ProdutoVenda, Registro, Role, Usuario
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
    assert client.post("/auth/login", data={"nome": nome, "senha": "pw"}).status_code == 302


def _cred(u, completa=True):
    c = MapaCredencial(owner_user_id=u.id, usuario_app="usr", numero_sif="167", especie="suino")
    c.senha = "s3gredo"
    if completa:
        c.cpf_cnpj, c.ambito, c.cod_uf, c.cod_municipio_ibge = "12345678000199", "SIF", "RS", "4314902"
    db.session.add(c)
    db.session.commit()
    return c


def _cods(**por_nome):
    for nome, cod in por_nome.items():
        p = ProdutoVenda.query.filter_by(nome=nome.replace("_", " ")).one()
        p.cod_api = cod
    db.session.commit()
    constantes.invalidar_cache()


COD_OK = {"BACON": 101, "APRESUNTADO_RESFRIADO": 102, "LINGUICA_TOSCANA_CONGELADA": 103, "LINGUICA_FRESCAL_CONGELADA": 103}


def _upload(client, linhas=LINHAS_OK):
    return client.post("/comercializacao/novo", data={
        "file": (xlsx_vendas(linhas), "v.xlsx"), "lancamento": "venda"}, content_type="multipart/form-data")


def test_preview_api_mostra_json_e_bloqueia_pendencias(app, client):
    u = _usuario()
    _login(client)
    _upload(client)
    reg = Registro.query.one()

    # sem credencial e sem cod_produto: pendencias (e nada de JSON)
    html = client.get(f"/comercializacao/{reg.id}/preview").get_data(as_text=True)
    assert "Validação bloqueada" in html
    assert "Produto sem cod_produto da API" in html and "Credenciais MAPA incompletas" in html

    _cred(u, completa=False)
    _cods(**COD_OK)
    html = client.get(f"/comercializacao/{reg.id}/preview").get_data(as_text=True)
    assert "Credenciais MAPA incompletas" in html and "CPF/CNPJ" in html and "código IBGE" in html

    c = MapaCredencial.query.one()
    c.cpf_cnpj, c.ambito, c.cod_uf, c.cod_municipio_ibge = "12.345.678/0001-99", "SIF", "RS", "4314902"
    db.session.commit()
    import html as _h
    html = _h.unescape(client.get(f"/comercializacao/{reg.id}/preview").get_data(as_text=True))
    assert "POST /comercializacao" in html and "cod_produto" in html and '"tipo_operador": "UF"' in html
    assert "12345678000199" in html


def test_payload_comercializacao_exato(app):
    u = _usuario()
    cred = _cred(u)
    _cods(**COD_OK)
    from app.logic.comercializacao import build_plano
    recs = [
        {"produto": "BACON", "uf": "RS", "quantidade": 100.0},
        {"produto": "LINGUICA TOSCANA CONGELADA", "uf": "AL", "quantidade": 10.5},
        {"produto": "LINGUICA FRESCAL CONGELADA", "uf": "AL", "quantidade": 4.25},   # mesmo cod: lancados separados
    ]
    from app.models import TipoLancamento
    t = TipoLancamento.query.filter_by(codigo="venda").one()
    cfg = {"api_tipo": t.api_tipo, "api_nacional": t.api_nacional, "api_tipo_operador": t.api_tipo_operador,
           "api_produto_tipo": t.api_produto_tipo}
    from datetime import date
    est = payloads.estabelecimento(cred)
    body = payloads.montar_comercializacao(est, date(2026, 3, 1), date(2026, 3, 31), build_plano(recs), cfg)
    assert body == {
        "data_inicio": "2026-03-01", "data_fim": "2026-03-31",
        "estabelecimento": {"numero": "167", "ambito": "SIF", "cpf_cnpj": "12345678000199",
                            "cod_uf": "RS", "cod_municipio_ibge": "4314902"},
        "transacoes": [
            {"tipo": "VENDA", "nacional": True, "tipo_operador": "UF", "cod_uf": "AL", "produtos": [
                {"cod_produto": 103, "quantidade": 4.25}, {"cod_produto": 103, "quantidade": 10.5}]},
            {"tipo": "VENDA", "nacional": True, "tipo_operador": "UF", "cod_uf": "RS",
             "produtos": [{"cod_produto": 101, "quantidade": 100.0}]},
        ],
    }
    # formato de data BR e tipo de produto opcional
    cfg["api_produto_tipo"] = "PROPRIA"
    b2 = payloads.montar_comercializacao(est, date(2026, 3, 1), date(2026, 3, 31), build_plano(recs[:1]), cfg, "br")
    assert b2["data_inicio"] == "01/03/2026" and b2["transacoes"][0]["produtos"][0]["tipo"] == "PROPRIA"
    cfg["api_tipo_operador"] = "RECEBIMENTO_AUTORIZADO"
    with pytest.raises(NotImplementedError):
        payloads.montar_comercializacao(est, date(2026, 3, 1), date(2026, 3, 31), [], cfg)


def test_estabelecimento_validacoes(app):
    u = _usuario()
    c = _cred(u, completa=False)
    with pytest.raises(payloads.DadosIncompletos) as ei:
        payloads.estabelecimento(c)
    assert len(ei.value.faltando) == 4
    c.cpf_cnpj, c.ambito, c.cod_uf, c.cod_municipio_ibge = "123", "XX", "ZZ", "123"
    with pytest.raises(payloads.DadosIncompletos):
        payloads.estabelecimento(c)


def test_finalizar_cria_job_api_e_worker_envia(app, client, monkeypatch):
    u = _usuario()
    _login(client)
    _cred(u)
    _cods(**COD_OK)
    _upload(client)
    reg = Registro.query.one()
    assert client.post(f"/comercializacao/{reg.id}/finalizar").status_code == 302

    job = ExecJob.query.one()
    assert job.status == "ESPERA" and job.meta["backend"] == "api" and job.meta["servico"] == "comercializacao"
    assert job.commands == ["POST /comercializacao"] and job.meta["lancamento"] == "venda"
    assert [t["cod_uf"] for t in job.meta["payload"]["transacoes"]] == ["AL", "RS"]
    assert Registro.query.one().status == "PT"

    enviados = []

    class Falso:
        ambiente = "homologacao"

        def enviar(self, servico, corpo, api_id=None):
            enviados.append((servico, api_id, corpo))
            return [{"id": 77}]

    monkeypatch.setattr(execucao, "cliente", lambda cred: Falso())
    monkeypatch.setattr(worker_mod, "ensure_valid_credential",
                        lambda *a: (_ for _ in ()).throw(AssertionError("nao deve abrir navegador")))
    assert _rodar(app, job.id) == "SUCESSO"
    db.session.expire_all()
    reg = Registro.query.one()
    assert (reg.status, reg.api_id) == ("FZ", 77)
    job = db.session.get(ExecJob, job.id)
    assert job.progress == 1 and job.meta["metodo"] == "POST" and job.meta["ambiente"] == "homologacao"
    assert enviados[0][0] == "comercializacao" and enviados[0][1] is None

    # reenvio: como ja tem api_id, usa PUT
    reg.status = "PT"
    job.status, job.errors, job.finished_at = "ESPERA", [], None
    db.session.commit()
    assert _rodar(app, job.id) == "SUCESSO"
    assert enviados[1][1] == 77


def test_erro_da_api_marca_falha_e_timeout_de_post_e_incerto(app, client, monkeypatch):
    u = _usuario()
    _login(client)
    _cred(u)
    _cods(**COD_OK)
    _upload(client)
    reg = Registro.query.one()
    client.post(f"/comercializacao/{reg.id}/finalizar")
    job = ExecJob.query.one()

    respostas = [ApiError("Erro na estrutura dos dados (400): campo x", status=400, corpo={"m": "campo x"}),
                 ApiError("Falha de rede em POST comercializacao: Timeout")]

    class Falso:
        ambiente = "homologacao"

        def enviar(self, *a, **k):
            raise respostas.pop(0)

    monkeypatch.setattr(execucao, "cliente", lambda cred: Falso())
    assert _rodar(app, job.id) == "FALHOU"
    db.session.expire_all()
    job = db.session.get(ExecJob, job.id)
    assert "400" in job.errors[0] and job.meta["incerto"] is False
    assert db.session.get(Registro, reg.id).status == "ER"

    job.status, job.errors, job.finished_at = "ESPERA", [], None
    db.session.commit()
    assert _rodar(app, job.id) == "FALHOU"
    db.session.expire_all()
    job = db.session.get(ExecJob, job.id)
    assert job.meta["incerto"] is True and "DESCONHECIDO" in job.errors[0]


def _rodar(app, job_id, timeout=15):
    stop = threading.Event()
    w = worker_mod.Worker(app, name="w-api", profile="prod", stop_event=stop)
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    fim, status = time.time() + timeout, None
    while time.time() < fim:
        db.session.expire_all()
        status = db.session.get(ExecJob, job_id).status
        if status in ("SUCESSO", "FALHOU"):
            break
        time.sleep(0.1)
    stop.set()
    t.join(5)
    db.session.expire_all()
    return status


def test_credenciais_exigem_dados_do_estabelecimento_e_testar_conexao(app, client, monkeypatch):
    _usuario()
    _login(client)
    base = {"usuario_app": "u", "senha": "x", "numero_sif": "167", "especie": "1"}
    r = client.post("/credenciais-mapa", data=base)
    assert MapaCredencial.query.count() == 0                       # faltam CNPJ/ambito/UF/IBGE
    r = client.post("/credenciais-mapa", data={**base, "cpf_cnpj": "12.345.678/0001-99", "ambito": "SIF",
                                               "cod_uf": "RS", "cod_municipio_ibge": "4314902"})
    c = MapaCredencial.query.one()
    assert (c.cpf_cnpj, c.ambito, c.cod_uf, c.cod_municipio_ibge) == ("12345678000199", "SIF", "RS", "4314902")
    assert "Testar conexão" in client.get("/credenciais-mapa").get_data(as_text=True)

    chamadas = []

    class Falso:
        ambiente = "homologacao"

        def validar_credenciais(self):
            chamadas.append(1)

    monkeypatch.setattr(montagem, "cliente", lambda cred: Falso())
    import app.blueprints.public.routes  # noqa: F401  (a rota importa `cliente` dentro da funcao)
    client.post("/credenciais-mapa/testar")
    db.session.expire_all()
    assert chamadas and MapaCredencial.query.one().validado is True

    class Negado:
        ambiente = "homologacao"

        def validar_credenciais(self):
            raise ApiError("Acesso negado (403)", status=403)

    monkeypatch.setattr(montagem, "cliente", lambda cred: Negado())
    client.post("/credenciais-mapa/testar")
    db.session.expire_all()
    c = MapaCredencial.query.one()
    assert c.validado is False and "403" in c.last_error
