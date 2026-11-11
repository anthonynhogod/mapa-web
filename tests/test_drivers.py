"""Verificacao/atualizacao automatica do chromedriver e separacao 'driver quebrado' x 'credencial invalida'."""
import copy

import pytest
from selenium.common.exceptions import NoSuchDriverException, SessionNotCreatedException

from app.runner import drivers


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch, tmp_path):
    original = copy.deepcopy(drivers._estado)
    drivers._estado.update(ok=None, driver_path=None, chrome=None, driver=None, origem=None,
                           mensagem="Ainda nao verificado.", verificado_em=None, tentativas=[])
    monkeypatch.setattr(drivers, "DRIVERS_DIR", str(tmp_path))     # sem driver embutido real
    monkeypatch.delenv("CHROMEDRIVER_PATH", raising=False)
    monkeypatch.setattr(drivers, "versao_do_chrome", lambda: None)     # nada de ler o Chrome/rede reais nos testes
    yield
    drivers._estado.clear()
    drivers._estado.update(original)


def _arquivo(tmp_path, nome="chromedriver"):
    p = tmp_path / nome
    p.write_text("x")
    return str(p)


def test_driver_compativel_e_usado_sem_chamar_o_selenium_manager(monkeypatch, tmp_path):
    caminho = _arquivo(tmp_path)
    monkeypatch.setattr(drivers, "_abrir_para_teste", lambda p: (True, {"chrome": "153.0", "driver": "153.0"}))
    monkeypatch.setattr(drivers, "_selenium_manager", lambda ignorar, **kw: pytest.fail("nao deveria baixar nada"))
    st = drivers.garantir(explicito=caminho)
    assert st["ok"] and st["driver_path"] == caminho and drivers.driver_verificado() == caminho


def test_driver_velho_e_substituido_pelo_do_selenium_manager(monkeypatch, tmp_path):
    velho = _arquivo(tmp_path)
    novo = str(tmp_path / "baixado")
    chamadas = []

    def abrir(path):
        return (True, {"chrome": "153.0", "driver": "153.0"}) if path == novo else (
            False, "o chromedriver é da versão 147 e não serve para o Chrome instalado (153.0)")

    def sm(ignorar, **kw):
        chamadas.append(ignorar)
        return novo

    monkeypatch.setattr(drivers, "_abrir_para_teste", abrir)
    monkeypatch.setattr(drivers, "_selenium_manager", sm)
    st = drivers.garantir(explicito=velho)
    assert st["ok"] and st["driver_path"] == novo and "baixado/atualizado" in st["origem"]
    assert chamadas == [True]                                         # 1a tentativa ignora drivers soltos no PATH
    assert [t["ok"] for t in st["tentativas"]] == [False, True] and "147" in st["tentativas"][0]["detalhe"]


def test_se_ignorar_path_falha_tenta_aceitando_o_path(monkeypatch):
    def sm(ignorar, **kw):
        if ignorar:
            raise RuntimeError("sem rede")
        return "/usr/bin/chromedriver"

    monkeypatch.setattr(drivers, "_selenium_manager", sm)
    monkeypatch.setattr(drivers, "_abrir_para_teste", lambda p: (True, {"chrome": "1", "driver": "1"}))
    assert drivers.garantir()["driver_path"] == "/usr/bin/chromedriver"


def test_sem_nenhuma_opcao_explica_o_que_fazer(monkeypatch):
    def sm(ignorar, **kw):
        raise RuntimeError("error sending request for url (https://googlechromelabs.github.io/...)")

    monkeypatch.setattr(drivers, "_selenium_manager", sm)
    st = drivers.garantir()
    assert st["ok"] is False and drivers.driver_verificado() is None
    assert "googlechromelabs.github.io" in st["mensagem"] and "SE_PROXY" in st["mensagem"]
    assert len(st["tentativas"]) == 3          # 2 do Selenium Manager + o fallback em Python (sem versao do Chrome)
    assert "Alternativa manual" in st["mensagem"]


def test_resultado_ok_fica_em_cache_e_forcar_refaz(monkeypatch, tmp_path):
    n = []
    monkeypatch.setattr(drivers, "_abrir_para_teste", lambda p: (n.append(p) or True, {"chrome": "1", "driver": "1"}))
    caminho = _arquivo(tmp_path)
    drivers.garantir(explicito=caminho)
    drivers.garantir(explicito=caminho)
    assert len(n) == 1                      # 2a chamada usou o cache
    drivers.garantir(explicito=caminho, forcar=True)
    assert len(n) == 2


def test_classificacao_de_erros_de_driver():
    assert drivers.eh_erro_de_driver(NoSuchDriverException("Unable to obtain driver for chrome"))
    assert drivers.eh_erro_de_driver(SessionNotCreatedException("This version of ChromeDriver only supports Chrome version 147"))
    assert not drivers.eh_erro_de_driver(ValueError("senha incorreta"))
    msg = "session not created: This version of ChromeDriver only supports Chrome version 147\nCurrent browser version is 153.0.1 with binary path x"
    resumo = drivers._resumir(SessionNotCreatedException(msg))
    assert "147" in resumo and "153.0.1" in resumo
    assert "não é problema das credenciais" in drivers.explicar(NoSuchDriverException("Unable to obtain driver"))


def test_driver_quebrado_nao_marca_a_credencial_como_invalida(app, monkeypatch):
    from app.executor import credentials
    from app.extensions import db
    from app.models import MapaCredencial, Role, Usuario

    u = Usuario(nome="op", senha="x", role=Role.USER)
    db.session.add(u)
    db.session.commit()
    c = MapaCredencial(owner_user_id=u.id, usuario_app="u", numero_sif="167", especie="suino")
    c.senha = "s"
    db.session.add(c)
    db.session.commit()

    def navegador_quebrado(*a, **k):
        raise NoSuchDriverException("Message: Unable to obtain driver for chrome; For documentation on this error...")

    monkeypatch.setattr(credentials, "Navegador", navegador_quebrado)
    with pytest.raises(drivers.NavegadorIndisponivel) as e:
        credentials.ensure_valid_credential(c, "01/03/2026")
    assert "não é problema das credenciais" in str(e.value)
    db.session.refresh(c)
    assert c.validado is False and c.last_error is None            # nada gravado como "credencial invalida"


def test_falha_do_selenium_manager_vira_mensagem_de_rede(monkeypatch):
    from selenium.common.exceptions import WebDriverException
    from app.runner import drivers as d

    class SM:
        def binary_paths(self, args):
            raise WebDriverException("Unsuccessful command executed: x; code: 65\n{'code': 65, 'message': 'Unsuccessful response (404 Not Found) for URL https://x'}\n")

    import selenium.webdriver.common.selenium_manager as m
    monkeypatch.setattr(m, "SeleniumManager", SM)
    with pytest.raises(RuntimeError) as e:
        d._selenium_manager(True)
    assert "404 Not Found" in str(e.value) and "baixar" in str(e.value)


# ---------------- rede instavel (proxy que bloqueia de vez em quando) ----------------
def test_selenium_manager_repete_quando_o_proxy_falha_de_vez_em_quando(monkeypatch):
    n, esperas = [], []

    def uma_vez(ignorar):
        n.append(1)
        if len(n) < 3:
            raise RuntimeError("error sending request")
        return "/driver"

    monkeypatch.setattr(drivers, "_selenium_manager_uma_vez", uma_vez)
    assert drivers._selenium_manager(True, tentativas=3, dormir=esperas.append) == "/driver"
    assert len(n) == 3 and len(esperas) == 2


def test_http_get_repete_em_403_e_pagina_de_bloqueio(monkeypatch):
    import requests

    class R:
        def __init__(self, code, corpo=None, erro=False):
            self.status_code, self._c, self._e = code, corpo, erro
            self.content = b"zip"

        def json(self):
            if self._e:
                raise ValueError("html")
            return self._c

    respostas = iter([R(403), R(200, erro=True), R(200, {"ok": 1})])
    monkeypatch.setattr(requests, "get", lambda url, timeout: next(respostas))
    esperas = []
    assert drivers._http_get("u", json_=True, dormir=esperas.append) == {"ok": 1}
    assert len(esperas) == 2

    monkeypatch.setattr(requests, "get", lambda url, timeout: R(403))
    with pytest.raises(RuntimeError) as e:
        drivers._http_get("u", json_=True, tentativas=3, dormir=lambda s: None)
    assert "HTTP 403" in str(e.value) and "3 tentativas" in str(e.value)


def test_erros_de_rede_sao_explicados():
    import requests
    assert "certificado" in drivers._classificar_rede(requests.exceptions.SSLError())
    assert "proxy" in drivers._classificar_rede(requests.exceptions.ProxyError())
    assert "tempo esgotado" in drivers._classificar_rede(requests.exceptions.ConnectTimeout())


def test_proxy_do_sistema(monkeypatch):
    monkeypatch.delenv("SE_PROXY", raising=False)
    monkeypatch.setattr(drivers, "getproxies", lambda: {"https": "proxy.empresa:8080"})
    assert drivers.proxy_do_sistema() == "http://proxy.empresa:8080"
    monkeypatch.setattr(drivers, "getproxies", lambda: {})
    assert drivers.proxy_do_sistema() is None
    monkeypatch.setenv("SE_PROXY", "http://x:1")
    monkeypatch.setattr(drivers, "getproxies", lambda: {"https": "outro:9"})
    assert drivers.proxy_do_sistema() is None            # o usuario ja definiu SE_PROXY


def _zip_com_driver(nome):
    import io, zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(f"chromedriver-linux64/{nome}", "binario")
        z.writestr("chromedriver-linux64/LICENSE", "x")
    return b.getvalue()


def test_download_em_python_baixa_o_driver_do_major_certo(monkeypatch, tmp_path):
    import sys
    nome = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
    plat = drivers._plataforma_cft()
    indice = {"milestones": {"143": {"downloads": {"chromedriver": [
        {"platform": "outra", "url": "u-outra"}, {"platform": plat, "url": "u-certa"}]}}}}

    def get(url, json_, **kw):
        return indice if url == drivers.CFT_MARCOS else (_zip_com_driver(nome) if url == "u-certa" else pytest.fail(url))

    monkeypatch.setattr(drivers, "_http_get", get)
    caminho = drivers.baixar_driver_em_python("143.0.7499.40")
    assert caminho == str(tmp_path / "auto" / "143" / nome)
    with open(caminho) as f:
        assert f.read() == "binario"
    with pytest.raises(RuntimeError, match="não consta"):
        drivers.baixar_driver_em_python("99.0.0.0")


def test_garantir_usa_o_download_em_python_quando_o_selenium_manager_falha(monkeypatch, tmp_path):
    monkeypatch.setattr(drivers, "_selenium_manager", lambda ignorar, **kw: (_ for _ in ()).throw(RuntimeError("error sending request")))
    monkeypatch.setattr(drivers, "versao_do_chrome", lambda: "143.0.7499.40")
    baixado = str(tmp_path / "auto" / "143" / "chromedriver")
    monkeypatch.setattr(drivers, "baixar_driver_em_python", lambda v: baixado)
    monkeypatch.setattr(drivers, "_abrir_para_teste", lambda p: (p == baixado, {"chrome": "143", "driver": "143"} if p == baixado else "x"))
    st = drivers.garantir()
    assert st["ok"] and st["driver_path"] == baixado and st["origem"] == "baixado pelo sistema"


def test_driver_baixado_antes_e_reaproveitado(monkeypatch, tmp_path):
    d = tmp_path / "auto" / "143"
    d.mkdir(parents=True)
    (d / "chromedriver").write_text("x")
    monkeypatch.setattr(drivers, "_abrir_para_teste", lambda p: (True, {"chrome": "143", "driver": "143"}))
    monkeypatch.setattr(drivers, "_selenium_manager", lambda ignorar, **kw: pytest.fail("nao precisa baixar de novo"))
    assert drivers.garantir()["driver_path"] == str(d / "chromedriver")


def test_falha_total_informa_versao_e_diagnostico_de_rede(monkeypatch):
    monkeypatch.setattr(drivers, "_selenium_manager", lambda ignorar, **kw: (_ for _ in ()).throw(RuntimeError("error sending request")))
    monkeypatch.setattr(drivers, "versao_do_chrome", lambda: "143.0.7499.40")
    monkeypatch.setattr(drivers, "baixar_driver_em_python", lambda v: (_ for _ in ()).throw(RuntimeError("HTTP 403 (após 6 tentativas)")))
    st = drivers.garantir()
    assert st["ok"] is False and st["chrome"] == "143.0.7499.40"
    assert any("HTTP 403" in t["detalhe"] for t in st["tentativas"])
    assert "do Chrome 143" in st["mensagem"]
