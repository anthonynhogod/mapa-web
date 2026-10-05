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
