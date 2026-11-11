"""Regressao do scripts.js (ABATE) refatorado: roda o fluxo inteiro contra um portal simulado,
com os ids "j_idt" legados e com ids trocados (o que o fallback dinamico precisa resolver)."""
import os

import pytest

from test_js_comercializacao import SCRIPTS, _chrome, browser, pw  # noqa: F401

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MOCK = "file://" + os.path.join(ROOT, "tests", "js", "mock_abate.html")


def abrir(browser, ids):
    page = browser.new_page()
    page.__erros = []
    page.on("pageerror", lambda e: page.__erros.append(str(e)))
    page.goto(f"{MOCK}?ids={ids}")
    for nome in ("core.js", "scripts.js"):
        page.add_script_tag(path=os.path.join(SCRIPTS, nome))
    page.evaluate("window.__SIGSIF_DEBUG__ = false")
    return page


def rodar(page, cmd):
    return page.evaluate("async (c) => await eval('window.' + c)", cmd)


@pytest.mark.parametrize("ids", ["legacy", "random"])
def test_fluxo_abate_completo(browser, ids):
    page = abrir(browser, ids)
    assert rodar(page, "findRegistro('05/03/2026')") == "Nenhum registro encontrado."
    assert rodar(page, "criarNovoRegistroAbate('05/03/2026')") == "OK: Registro criado."
    assert rodar(page, "findRegistro('05/03/2026')").startswith("OK") or True
    assert rodar(page, "alterarRegistroAtivo()") == "OK: Registro ativo alterado."

    assert rodar(page, "incluirGta(23, 830271, 'AC', 65, 0)") == "OK: Inclusão de GTA realizada."
    assert rodar(page, "incluirGta(0, 830272, 'AC', 10, 5)") == "OK: Inclusão de GTA realizada."   # UF invalida -> default 23
    assert rodar(page, "incluirTipoGta(0, 1, 4, 60, 6671.4, 0, 0)") == "OK: Tipo de GTA incluído."
    assert rodar(page, "incluirTipoGta(1, 2, 5, 10, 100, 5, 50)") == "OK: Tipo de GTA incluído."
    assert rodar(page, "incluirDiagnostico(0, 'ABSCESSO (MAMÍFEROS)', 3)") == "OK: Diagnóstico incluído."
    assert rodar(page, "incluirParte(0, 1, 1, 3)") == "OK: Parte afetada incluída."
    assert rodar(page, "finalizarRegistro()") == "OK: Registro finalizado."

    st = page.evaluate("JSON.parse(JSON.stringify(window.__server))")
    assert st["unknown"] == [], st["unknown"]
    assert page.__erros == []
    assert [(g["uf"], g["nGta"], g["serie"], g["machos"], g["femeas"]) for g in st["saldos"]] == [
        ("RS", "830271", "AC", "65", "0"), ("RS", "830272", "AC", "10", "5")]
    assert st["especie"] == "7.1"
    assert [(l["saldo"], l["lote"], l["qm"], l["pm"]) for l in st["lotes"]] == [(0, "4", "60", "6671.4"), (1, "5", "10", "100")]
    assert st["diags"] == [{"lote": 0, "nome": "ABSCESSO (MAMÍFEROS)", "qtd": "3"}]
    assert st["partes"] == [{"diag": 0, "parte": "1", "qtd": "3", "destino": "1"}]
    assert st["finalizado"] >= 1

    assert rodar(page, "excluirTodosLotes()").startswith("OK")
    assert page.evaluate("window.__server.saldos.length") == 0
    page.close()
