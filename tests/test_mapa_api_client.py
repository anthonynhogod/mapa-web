import base64
import hashlib

import pytest
import requests

from app.mapa_api.client import (
    ApiError, MapaApiClient, RAIZ, basic_auth, extrair_id, resolver_raiz,
)


class Resp:
    def __init__(self, status=200, corpo=None, texto=""):
        self.status_code, self._c, self.text = status, corpo, texto

    def json(self):
        if self._c is None:
            raise ValueError("sem json")
        return self._c


class Sessao:
    """requests.Session falsa: devolve respostas/erros em sequencia e grava as chamadas."""

    def __init__(self, *seq):
        self.seq, self.chamadas = list(seq), []

    def request(self, metodo, url, headers=None, timeout=None, data=None):
        self.chamadas.append({"metodo": metodo, "url": url, "headers": headers, "data": data})
        r = self.seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def cli(*seq, **kw):
    s = Sessao(*seq)
    return MapaApiClient("joao", "segredo", session=s, sleep=lambda _: None, **kw), s


def test_basic_auth_com_md5_e_crua():
    esperado = base64.b64encode(f"joao:{hashlib.md5(b'segredo').hexdigest()}".encode()).decode()
    assert basic_auth("joao", "segredo") == "Basic " + esperado
    assert basic_auth("joao", "segredo", md5=False) == "Basic " + base64.b64encode(b"joao:segredo").decode()


def test_ambiente_padrao_e_homologacao():
    assert resolver_raiz(None) == RAIZ["homologacao"]
    assert resolver_raiz("producao") == RAIZ["producao"]
    assert resolver_raiz("qualquer-coisa") == RAIZ["homologacao"]       # nunca cai em producao por engano
    assert resolver_raiz("x", "http://local/servicos/") == "http://local/servicos"
    assert cli(Resp(200, []))[0].ambiente == "homologacao"
    assert cli(Resp(200, []), ambiente="producao")[0].ambiente == "producao"


def test_get_e_cabecalhos():
    c, s = cli(Resp(200, [{"Id": 1}]))
    assert c.catalogo("especies") == [{"Id": 1}]
    ch = s.chamadas[0]
    assert ch["url"] == RAIZ["homologacao"] + "/especies" and ch["metodo"] == "GET"
    assert ch["headers"]["Authorization"].startswith("Basic ") and ch["headers"]["Accept"] == "application/json"
    c.catalogo("especies")                       # cache: sem nova chamada
    assert len(s.chamadas) == 1


def test_erros_400_403_500_com_corpo_e_sem_vazar_senha():
    for status, trecho in ((400, "estrutura"), (403, "Acesso negado"), (500, "processar")):
        c, _ = cli(Resp(status, {"mensagem": "campo x invalido"}))
        with pytest.raises(ApiError) as ei:
            c.post("comercializacao", {"a": 1})
        e = ei.value
        assert e.status == status and trecho in str(e) and "campo x invalido" in str(e)
        assert "segredo" not in str(e) and hashlib.md5(b"segredo").hexdigest() not in str(e)
    assert ApiError("x", status=403).acesso_negado


def test_get_repete_em_5xx_e_rede_mas_post_nunca():
    c, s = cli(Resp(500, "boom"), requests.ConnectionError("x"), Resp(200, [1]))
    assert c.get("produtos") == [1] and len(s.chamadas) == 3

    c, s = cli(Resp(500, "boom"), Resp(500, "boom"), Resp(500, "boom"))
    with pytest.raises(ApiError):
        c.get("produtos")
    assert len(s.chamadas) == 3                  # 3 tentativas e para

    c, s = cli(Resp(403, "no"))
    with pytest.raises(ApiError):
        c.get("produtos")
    assert len(s.chamadas) == 1                  # 403 nao repete

    c, s = cli(requests.Timeout("lento"), Resp(200, {"id": 1}))
    with pytest.raises(ApiError) as ei:
        c.post("abate", [{"a": 1}])
    assert ei.value.status == 0 and ei.value.transitorio and len(s.chamadas) == 1   # POST: sem retry


def test_enviar_post_e_put_com_id():
    c, s = cli(Resp(200, {"id": 9}), Resp(200, {"id": 9}), Resp(200, []))
    c.enviar("comercializacao", {"data_inicio": "2026-03-01"})
    c.enviar("comercializacao", {"data_inicio": "2026-03-01"}, api_id=9)
    c.enviar("abate", [{"nr_gta": 1}, {"nr_gta": 2}], api_id=5)
    assert [x["metodo"] for x in s.chamadas] == ["POST", "PUT", "PUT"]
    import json
    assert json.loads(s.chamadas[1]["data"]) == {"id": 9, "data_inicio": "2026-03-01"}
    assert [l["id"] for l in json.loads(s.chamadas[2]["data"])] == [5, 5]
    assert s.chamadas[0]["headers"]["Content-Type"] == "application/json"


def test_extrair_id():
    assert extrair_id({"id": 12}) == 12
    assert extrair_id([{"x": 1}, {"Id": "34"}]) == 34
    assert extrair_id({"mensagem": "ok"}) is None and extrair_id("ok") is None


XML_ERRO = (
    "<response><idTransacao>498</idTransacao><status>0</status><listaErros><erro><codigo>500</codigo>"
    "<descricao>null for uri: http://x/pga_sigsif/servicos/especie</descricao><tipo>N</tipo></erro></listaErros>"
    "<stackTrace>com.sun.jersey.server.impl...</stackTrace></response>"
)


def test_erro_em_xml_do_mapa_vira_mensagem_legivel_sem_stacktrace():
    from app.mapa_api.client import parse_resposta_xml
    d = parse_resposta_xml(XML_ERRO)
    assert d["idTransacao"] == "498" and d["erros"][0]["codigo"] == "500" and "stackTrace" not in str(d)
    assert parse_resposta_xml("nao e xml") is None and parse_resposta_xml("<outra/>") is None

    c, _ = cli(Resp(500, None, XML_ERRO))
    with pytest.raises(ApiError) as ei:
        c.post("abate", [{"a": 1}])
    msg = str(ei.value)
    assert "[500] null for uri" in msg and "transação 498" in msg and "jersey" not in msg
    assert ei.value.corpo["erros"][0]["descricao"].startswith("null for uri")
