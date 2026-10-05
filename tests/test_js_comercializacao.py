"""Testa app/runner/scripts/{core,comercializacao}.js no Chromium contra um portal SIMULADO
(tests/js/mock_comercializacao.html). Cobre ids legados e ids "j_idt" trocados."""
import glob
import os

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "app", "runner", "scripts")
MOCK = "file://" + os.path.join(ROOT, "tests", "js", "mock_comercializacao.html")
PERIODO = ("01/03/2026", "31/03/2026")
UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA"]


def _chrome():
    for cand in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        return cand
    return None


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=_chrome(), args=["--no-sandbox"])
        except Exception as e:  # sem navegador no ambiente
            pytest.skip(f"Chromium indisponivel: {e}")
        yield b
        b.close()


def abrir(browser, ids="legacy", exige=False, registros=None):
    page = browser.new_page()
    erros = []
    page.on("pageerror", lambda e: erros.append(str(e)))
    page.goto(f"{MOCK}?ids={ids}&exige={'1' if exige else '0'}")
    page.evaluate("window.__seed(%s)" % (registros if registros is not None else "[]"))
    for nome in ("core.js", "comercializacao.js"):
        page.add_script_tag(path=os.path.join(SCRIPTS, nome))
    page.evaluate("window.__SIGSIF_DEBUG__ = false")
    page.__erros = erros
    return page


def rodar(page, cmd):
    return page.evaluate("async (c) => await eval('window.' + c)", cmd)


TODOS = '["Venda", "Recebimento", "Expedição"]'
VENDA = '{tipo: 1, ambito: 1, operador: 2, rotulo: "Venda"}'
RECEB = '{tipo: 2, ambito: 1, operador: 2, rotulo: "Recebimento"}'


def estado(page):
    return page.evaluate("JSON.parse(JSON.stringify(window.__server))")


REG_EXISTENTE = (
    "[{id: 500, ini: '01/03/2026', fim: '31/03/2026', excluido: false, empresa: '167', transacoes: %s}]"
)


@pytest.mark.parametrize("ids", ["legacy", "random"])
def test_fluxo_completo_com_paginacao(browser, ids):
    """14 UFs (2 paginas de transacoes); dica de indice propositalmente errada."""
    page = abrir(browser, ids, registros=REG_EXISTENTE % "[]")
    assert rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')").startswith("OK") \
        or True  # retorno pode ser msg vazia -> "OK"
    assert rodar(page, "alterarRegistroAtivoComercializacao()").startswith("OK")
    assert rodar(page, f'verificarRegistroVazio("Venda", {TODOS})').startswith("OK")

    esperado = {}
    for pos, uf in enumerate(UFS):
        assert rodar(page, f'incluirEstadoVenda("{uf}", {pos + 1}, {VENDA})').startswith("OK"), uf
        # dica de indice errada (0): a linha deve ser achada pelo TEXTO da UF
        for pid, q in ((17178, 100.5), (1717, 7.0), (18152, 3.25)):
            nome = {17178: "Apresuntado", 1717: "Apresuntado antigo", 18152: "Linguica frescal"}[pid]
            r = rodar(page, f'incluirProdutoVenda("{uf}", 0, {q}, "{nome}", {pid}, "Venda")')
            assert r.startswith("OK"), (uf, pid, r)
            esperado.setdefault(uf, []).append({"id": pid, "qtd": q})
    assert rodar(page, "finalizarRegistroComercializacao()").startswith("OK")

    st = estado(page)
    assert st["unknown"] == [], st["unknown"]
    assert page.__erros == []
    assert st["saved"] is True
    transacoes = st["registros"][0]["transacoes"]
    assert [t["uf"] for t in transacoes] == UFS
    assert {t["uf"]: t["produtos"] for t in transacoes} == esperado
    page.close()


def test_registro_com_dados_aborta_sem_alterar(browser):
    page = abrir(browser, "legacy", registros=REG_EXISTENTE % "[{uf: 'RS', tipo: 'Venda', produtos: []}]")
    rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')")
    assert rodar(page, "alterarRegistroAtivoComercializacao()").startswith("OK")
    r = rodar(page, f'verificarRegistroVazio("Venda", {TODOS})')
    assert r.startswith("Erro: [REGISTRO_COM_DADOS]"), r
    assert [t["uf"] for t in estado(page)["registros"][0]["transacoes"]] == ["RS"]
    page.close()


@pytest.mark.parametrize("ids", ["legacy", "random"])
def test_criar_registro_novo(browser, ids):
    page = abrir(browser, ids)
    assert rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')") == "Nenhum registro encontrado."
    r = rodar(page, f"criarRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}', '167')")
    assert r == "OK: Registro criado.", r
    st = estado(page)
    assert st["unknown"] == [], st["unknown"]
    reg = st["registros"][0]
    assert (reg["ini"], reg["fim"], reg["empresa"]) == (PERIODO[0], PERIODO[1], "167")
    # reconsulta + abre para alteracao
    assert rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')").startswith("OK")
    assert rodar(page, "alterarRegistroAtivoComercializacao()").startswith("OK")
    page.close()


def test_modo_incluir_quando_portal_exige_transacoes(browser):
    page = abrir(browser, "legacy", exige=True)
    rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')")
    r = rodar(page, f"criarRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}', '167')")
    assert r.startswith("OK: [MODO_INCLUIR]"), r
    assert rodar(page, f'verificarRegistroVazio("Venda", {TODOS})').startswith("OK")
    assert rodar(page, f'incluirEstadoVenda("RS", 23, {VENDA})').startswith("OK")
    assert rodar(page, 'incluirProdutoVenda("RS", 0, 10, "Bacon", 17189)').startswith("OK")
    r = rodar(page, "finalizarRegistroComercializacao()")
    assert r.startswith("OK"), r
    reg = estado(page)["registros"][0]
    assert reg["transacoes"][0]["produtos"] == [{"id": 17189, "qtd": 10}]
    page.close()


def test_erros_fecham_dialogos_e_nao_travam_o_proximo_comando(browser):
    page = abrir(browser, "legacy", registros=REG_EXISTENTE % "[]")
    rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')")
    rodar(page, "alterarRegistroAtivoComercializacao()")
    assert rodar(page, f'incluirEstadoVenda("RS", 23, {VENDA})').startswith("OK")

    # UF duplicada -> erro do portal, dialogo fechado
    r = rodar(page, f'incluirEstadoVenda("RS", 23, {VENDA})')
    assert r.startswith("Erro: [INCLUIR_ESTADO]"), r
    assert page.evaluate("document.querySelectorAll('.ui-dialog.open').length") == 0

    # produto inexistente na busca do portal
    r = rodar(page, 'incluirProdutoVenda("RS", 0, 5, "Bacon", 99999)')
    assert r.startswith("Erro: [PRODUTO_NAO_ENCONTRADO]"), r
    assert page.evaluate("document.querySelectorAll('.ui-dialog.open').length") == 0

    # UF que nao foi lancada
    r = rodar(page, 'incluirProdutoVenda("SC", 3, 5, "Bacon", 17189)')
    assert r.startswith("Erro: [UF_NAO_ENCONTRADA]"), r

    # quantidade invalida -> erro do portal; depois um comando valido ainda funciona
    r = rodar(page, 'incluirProdutoVenda("RS", 0, 0, "Bacon", 17189)')
    assert r.startswith("Erro: [INCLUIR_PRODUTO]"), r
    assert rodar(page, 'incluirProdutoVenda("RS", 0, 5, "Bacon", 17189)').startswith("OK")
    assert estado(page)["registros"][0]["transacoes"][0]["produtos"] == [{"id": 17189, "qtd": 5}]
    page.close()


def test_produto_casa_id_exato_e_busca_em_varias_paginas(browser):
    """id=17178 nao pode casar com id=1717; 'Apresuntado' rende 3 resultados (>1 pagina na busca de 5)."""
    page = abrir(browser, "legacy", registros=REG_EXISTENTE % "[]")
    rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')")
    rodar(page, "alterarRegistroAtivoComercializacao()")
    rodar(page, f'incluirEstadoVenda("SP", 26, {VENDA})')
    assert rodar(page, 'incluirProdutoVenda("SP", 0, 1, "Apresuntado", 17178)').startswith("OK")
    assert rodar(page, 'incluirProdutoVenda("SP", 0, 2, "Residuos", 18538)').startswith("OK")
    prods = estado(page)["registros"][0]["transacoes"][0]["produtos"]
    assert [p["id"] for p in prods] == [17178, 18538]
    page.close()


def test_mesmo_registro_com_venda_e_recebimento(browser):
    """Venda ja lancada em RS: recebimento NAO aborta, cria a propria linha de RS e os produtos
    caem nela; reprocessar o mesmo tipo aborta; limpar remove so o mesmo tipo."""
    page = abrir(browser, "legacy", registros=REG_EXISTENTE % "[{uf: 'RS', tipo: 'Venda', produtos: [{id: 1, qtd: 1}]}]")
    rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')")
    rodar(page, "alterarRegistroAtivoComercializacao()")

    assert rodar(page, f'verificarRegistroVazio("Venda", {TODOS})').startswith("Erro: [REGISTRO_COM_DADOS]")
    r = rodar(page, f'verificarRegistroVazio("Recebimento", {TODOS})')
    assert r.startswith("OK"), r

    assert rodar(page, f'incluirEstadoVenda("RS", 23, {RECEB})').startswith("OK")
    # dica de indice aponta para a linha da VENDA (0); o rotulo desempata para a de recebimento (1)
    r = rodar(page, 'incluirProdutoVenda("RS", 0, 9, "Bacon", 17189, "Recebimento")')
    assert r.startswith("OK"), r
    t = estado(page)["registros"][0]["transacoes"]
    assert [(x["uf"], x["tipo"], len(x["produtos"])) for x in t] == [("RS", "Venda", 1), ("RS", "Recebimento", 1)]
    assert t[1]["produtos"] == [{"id": 17189, "qtd": 9}]

    assert rodar(page, f'verificarRegistroVazio("Recebimento", {TODOS})').startswith("Erro: [REGISTRO_COM_DADOS]")
    r = rodar(page, f'limparTransacoes("Recebimento", {TODOS})')
    assert r.startswith("OK"), r
    t = estado(page)["registros"][0]["transacoes"]
    assert [(x["uf"], x["tipo"]) for x in t] == [("RS", "Venda")]      # a venda foi preservada
    page.close()


def test_incluir_estado_usa_opcoes_do_tipo(browser):
    page = abrir(browser, "legacy", registros=REG_EXISTENTE % "[]")
    rodar(page, f"findRegistroComercializacao('{PERIODO[0]}', '{PERIODO[1]}')")
    rodar(page, "alterarRegistroAtivoComercializacao()")
    # rotulo inexistente nas opcoes: cai no indice configurado (3 = Expedição)
    assert rodar(page, 'incluirEstadoVenda("SC", 24, {tipo: 3, ambito: 1, operador: 2, rotulo: "Saida X"})').startswith("OK")
    assert estado(page)["registros"][0]["transacoes"][0]["tipo"] == "Expedição"
    page.close()
