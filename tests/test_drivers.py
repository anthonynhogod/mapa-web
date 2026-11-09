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
    monkeypatch.setattr(drivers, "_selenium_manager", lambda ignorar: pytest.fail("nao deveria baixar nada"))
    st = drivers.garantir(explicito=caminho)
    assert st["ok"] and st["driver_path"] == caminho and drivers.driver_verificado() == caminho


def test_driver_velho_e_substituido_pelo_do_selenium_manager(monkeypatch, tmp_path):
    velho = _arquivo(tmp_path)
    novo = str(tmp_path / "baixado")
    chamadas = []

    def abrir(path):
        return (True, {"chrome": "153.0", "driver": "153.0"}) if path == novo else (
            False, "o chromedriver é da versão 147 e não serve para o Chrome instalado (153.0)")

    def sm(ignorar):
        chamadas.append(ignorar)
        return novo

    monkeypatch.setattr(drivers, "_abrir_para_teste", abrir)
    monkeypatch.setattr(drivers, "_selenium_manager", sm)
    st = drivers.garantir(explicito=velho)
    assert st["ok"] and st["driver_path"] == novo and "baixado/atualizado" in st["origem"]
    assert chamadas == [True]                                         # 1a tentativa ignora drivers soltos no PATH
    assert [t["ok"] for t in st["tentativas"]] == [False, True] and "147" in st["tentativas"][0]["detalhe"]


def test_se_ignorar_path_falha_tenta_aceitando_o_path(monkeypatch):
    def sm(ignorar):
        if ignorar:
            raise RuntimeError("sem rede")
        return "/usr/bin/chromedriver"

    monkeypatch.setattr(drivers, "_selenium_manager", sm)
    monkeypatch.setattr(drivers, "_abrir_para_teste", lambda p: (True, {"chrome": "1", "driver": "1"}))
    assert drivers.garantir()["driver_path"] == "/usr/bin/chromedriver"


def test_sem_nenhuma_opcao_explica_o_que_fazer(monkeypatch):
    def sm(ignorar):
        raise RuntimeError("error sending request for url (https://googlechromelabs.github.io/...)")

    monkeypatch.setattr(drivers, "_selenium_manager", sm)
    st = drivers.garantir()
    assert st["ok"] is False and drivers.driver_verificado() is None
    assert "googlechromelabs.github.io" in st["mensagem"] and "SE_PROXY" in st["mensagem"]
    assert len(st["tentativas"]) == 2


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
