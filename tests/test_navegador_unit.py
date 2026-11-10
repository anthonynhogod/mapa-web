"""Partes do Navegador que nao precisam de um Chrome de verdade."""
import os

from selenium.webdriver.chrome.service import Service

from app.runner.web.navegador import (
    ABATE_HOME_URL, COMERCIALIZACAO_HOME_URL, Navegador, NavegadorComercializacao,
)


def test_perfis_de_modulo():
    assert Navegador.HOME_URL == ABATE_HOME_URL
    assert Navegador.FORM_MARKER == "formConsultarMapaAbate" and Navegador.MODULE_SCRIPT == "scripts.js"
    assert NavegadorComercializacao.HOME_URL == COMERCIALIZACAO_HOME_URL
    assert NavegadorComercializacao.MODULE_SCRIPT == "comercializacao.js"
    assert NavegadorComercializacao.RETRY_ONLY_TRANSIENT and not Navegador.RETRY_ONLY_TRANSIENT
    assert not NavegadorComercializacao.TAP_ON_ERROR and Navegador.TAP_ON_ERROR


def test_scripts_do_modulo_existem_e_core_vem_antes():
    from app.runner.web.navegador import SCRIPTS_DIR
    for n in ("core.js", "scripts.js", "comercializacao.js"):
        assert os.path.isfile(os.path.join(SCRIPTS_DIR, n)), n

    class Fake(NavegadorComercializacao):
        def __init__(self):  # sem abrir Chrome
            self._module_script = os.path.join(SCRIPTS_DIR, self.MODULE_SCRIPT)

    core, modulo = Fake()._scripts_padrao()
    assert core.endswith("core.js") and modulo.endswith("comercializacao.js")


def test_erro_transitorio_so_para_timeout_e_helper_ausente():
    t = Navegador._erro_transitorio
    assert t("Erro: [EXC_X] [TIMEOUT] elemento nao satisfeita")
    assert t("Erro: foo is not defined")
    assert t("Erro: window.incluirProdutoVenda is not a function")
    assert not t("Erro: [REGISTRO_COM_DADOS] ja possui estados")
    assert not t("Erro: [INCLUIR_PRODUTO] RS/Bacon: Quantidade invalida")
    assert not t(None)


def test_resolver_service_cai_para_selenium_manager(tmp_path, monkeypatch):
    monkeypatch.delenv("CHROMEDRIVER_PATH", raising=False)
    # caminho inexistente (ex.: chromedriver.exe do Windows no .env) nao derruba
    s = Navegador._resolver_service(str(tmp_path / "nao-existe.exe"))
    assert isinstance(s, Service)

    exe = tmp_path / "chromedriver"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert Navegador._resolver_service(str(exe)).path == str(exe)
    monkeypatch.setenv("CHROMEDRIVER_PATH", str(exe))
    assert Navegador._resolver_service(None).path == str(exe)


def test_perfil_navegador_producao():
    from app.runner.web.navegador import NavegadorProducao, PRODUCAO_HOME_URL, SCRIPTS_DIR
    assert NavegadorProducao.HOME_URL == PRODUCAO_HOME_URL and PRODUCAO_HOME_URL.endswith("/mapaproducao/indexMapaProducao.xhtml")
    # hint proprio do modulo: com o generico "/pga_sigsif/" o login nunca navegaria ate a producao
    assert NavegadorProducao.URL_HINT in PRODUCAO_HOME_URL and NavegadorProducao.URL_HINT != Navegador.URL_HINT
    assert NavegadorProducao.FORM_MARKER == "formConsultarMapaProducao" and NavegadorProducao.MODULE_SCRIPT == "producao.js"
    assert NavegadorProducao.RETRY_ONLY_TRANSIENT and not NavegadorProducao.TAP_ON_ERROR
    assert os.path.isfile(os.path.join(SCRIPTS_DIR, "producao.js"))
    # os helpers exigidos existem como funcoes globais do script
    js = open(os.path.join(SCRIPTS_DIR, "producao.js"), encoding="utf-8").read()
    for fn in NavegadorProducao.HELPER_FUNCS:
        assert fn in js or fn in open(os.path.join(SCRIPTS_DIR, "core.js"), encoding="utf-8").read(), fn
    # erro de negocio/incerto NUNCA e repetido sozinho; timeout e
    t = NavegadorProducao._erro_transitorio
    assert t("Erro: [EXC_INCLUIR_PRODUTO] [TIMEOUT] campo x")
    assert not t("Erro: [ADD_INCERTO] nao consegui confirmar ... tempo esgotado ... reexecute o job")
    assert not t("Erro: [EXC_FINALIZAR] tempo esgotado. O mapa pode ou nao ter sido salvo")
    assert not t("Erro: [LINHAS_DIVERGENTES] a tabela de produtos tem 3 linha(s), esperado 4")


def test_login_producao_so_autentica_navega_e_injeta():
    from app.runner.web.navegador import NavegadorProducao, PRODUCAO_HOME_URL
    passos = []

    class Fake(NavegadorProducao):
        def __init__(self):
            pass

        def _autenticar_com_retry(self, usuario, senha, tentativas=3):
            passos.append(("auth", usuario, senha))

        def get(self, url):
            passos.append(("get", url))

        def _ensure_on_abate_home(self, *, timeout=15):
            passos.append(("home", timeout))
            return True, None

        def injetar_scripts(self, *a):
            passos.append(("inject",))

        def _pf_flush(self, timeout_ms=None):
            passos.append(("flush",))

    nav = Fake()
    nav.login("09/2026", "usr", "pw", "167", criar_se_faltar=False)      # kwargs de outros perfis sao ignorados
    assert [p[0] for p in passos] == ["auth", "get", "home", "inject", "flush"]
    assert passos[1] == ("get", PRODUCAO_HOME_URL) and nav.numero_sif == "167" and nav.data == "09/2026"

    class SemTela(Fake):
        def _ensure_on_abate_home(self, *, timeout=15):
            return False, "sem formulario"

    import pytest
    from app.runner.exceptions import FalhaNoJavascript
    with pytest.raises(FalhaNoJavascript):
        SemTela().login("09/2026", "usr", "pw")
