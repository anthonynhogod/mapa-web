"""Testa app/runner/scripts/{core,producao}.js no Chromium contra um portal SIMULADO (tests/js/mock_producao.html).
O mock usa os onclick reais do mapa de producao. Cobre ids legados e ids "j_idt" trocados."""
import glob
import os

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "app", "runner", "scripts")
MOCK = "file://" + os.path.join(ROOT, "tests", "js", "mock_producao.html")


def _chrome():
    for padrao in ("/opt/pw-browsers/chromium-*/chrome-linux64/chrome", "/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        for cand in sorted(glob.glob(padrao)):
            return cand
    return None


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=_chrome(), args=["--no-sandbox"])
        except Exception as e:
            pytest.skip(f"Chromium indisponivel: {e}")
        yield b
        b.close()


def abrir(browser, query="ids=legacy", cfg=None):
    page = browser.new_page()
    erros = []
    page.on("pageerror", lambda e: erros.append(str(e)))
    page.goto(f"{MOCK}?{query}")
    if cfg:
        page.evaluate("c => { window.SIGSIF_CFG = c; }", cfg)
    for nome in ("core.js", "producao.js"):
        page.add_script_tag(path=os.path.join(SCRIPTS, nome))
    page.evaluate("window.__SIGSIF_DEBUG__ = false")
    page.__erros = erros
    return page


def rodar(page, cmd):
    return page.evaluate("async (c) => await eval('window.' + c)", cmd)


def estado(page):
    return page.evaluate("JSON.parse(JSON.stringify(window.__server))")


def abrir_inclusao(page, comp="09/2026", sif="167"):
    return rodar(page, f'abrirInclusaoProducao("{comp}", "{sif}")')


IDS = ["ids=legacy", "ids=random"]


@pytest.mark.parametrize("q", IDS)
def test_fluxo_completo_cinco_produtos_em_varias_paginas(browser, q):
    """5 produtos (mapa com 3 linhas por pagina => 2 paginas); busca com 2 resultados por pagina; id exato."""
    page = abrir(browser, q)
    r = abrir_inclusao(page)
    assert r.startswith("OK"), r
    assert "167" in r and "EMPRESA 167 LTDA" in r
    st = estado(page)
    # a pesquisa do estabelecimento re-renderiza o formulario: periodo e Producao tem de estar de pe depois dela
    assert (st["periodo"], st["producao"], st["estab"]) == ("09/2026", "S", "167")

    itens = [("APRESUNTADO", 17176, 300.5), ("APRESUNTADO COZIDO", 17178, 10.25), ("BACON", 17189, 15.26),
             ("BACON FATIADO", 17186, 7), ("LINGUICA FRESCAL", 18152, 1234567.89)]
    for desc, pid, q_ in itens:
        r = rodar(page, f'incluirProdutoProducao("{desc}", {pid}, {q_})')
        assert r.startswith("OK"), (desc, r)
    st = estado(page)
    assert [(p["id"], p["qtd"]) for p in st["produtos"]] == [(i, q_) for _, i, q_ in itens]
    assert st["unknown"] == []

    r = rodar(page, 'finalizarRegistroProducao("09/2026", 5)')
    assert r.startswith("OK") and "5 produto(s)" in r, r
    st = estado(page)
    assert st["saved"]["periodo"] == "09/2026" and st["saved"]["estab"] == "167" and len(st["saved"]["produtos"]) == 5
    assert st["saved"]["produtos"][0] == {"id": 17176, "qtd": 300.5}
    assert page.__erros == []


@pytest.mark.parametrize("q", IDS)
def test_id_exato_nao_confunde_prefixo(browser, q):
    """A busca devolve 1717, 17176, 17178, 17175: pedir 1717 nao pode selecionar 17176."""
    page = abrir(browser, q)
    assert abrir_inclusao(page).startswith("OK")
    assert rodar(page, 'incluirProdutoProducao("APRESUNTADO", 1717, 4)').startswith("OK")
    assert estado(page)["produtos"] == [{"id": 1717, "qtd": 4}]


def test_termo_digitado_vai_para_o_dialogo_de_busca_nao_para_o_campo_do_dialogo_de_inclusao(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)')
    # o campo "produto" do dialogo de inclusao e somente-leitura e so recebe o nome pelo "Incluir" da busca
    assert page.evaluate("document.getElementById('j_idt491:frmDialog:produto:produto').value") == "BACON"
    assert page.evaluate("document.getElementById('j_idt504:frmDialog:produto:produto').value") == "BACON"


def test_produto_nao_encontrado_fecha_dialogos_e_o_proximo_funciona(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    r = rodar(page, 'incluirProdutoProducao("APRESUNTADO", 99999, 4)')
    assert r.startswith("Erro: [PRODUTO_NAO_ENCONTRADO]") and "id=99999" in r and "4 resultado" not in r
    assert "ids " in r                                   # diagnostico: o que a busca realmente devolveu
    assert page.evaluate("document.querySelectorAll('.ui-dialog.open').length") == 0
    assert estado(page)["produtos"] == []
    assert rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)').startswith("OK")
    assert estado(page)["produtos"] == [{"id": 17189, "qtd": 2}]


def test_busca_sem_resultado(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    r = rodar(page, 'incluirProdutoProducao("INEXISTENTE", 1, 4)')
    assert r.startswith("Erro: [PRODUTO_NAO_ENCONTRADO]") and "0 resultado(s)" in r


def test_erro_do_portal_ao_adicionar_vira_erro_com_o_texto(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    r = rodar(page, 'incluirProdutoProducao("BACON", 17189, 0)')
    assert r.startswith("Erro: [INCLUIR_PRODUTO]") and "Quantidade inválida" in r
    assert page.evaluate("document.querySelectorAll('.ui-dialog.open').length") == 0
    assert estado(page)["produtos"] == []


def test_timeout_no_adicionar_vira_add_incerto_sem_marca_transitoria(browser):
    """O Python repete sozinho erros com '[TIMEOUT]': depois do clique em Adicionar isso duplicaria a linha."""
    page = abrir(browser, "ids=legacy&add=timeout", cfg={"pfRequestTimeout": 400})
    abrir_inclusao(page)
    r = rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)')
    assert r.startswith("Erro: [ADD_INCERTO]"), r
    assert "[TIMEOUT]" not in r and "reexecute o job" in r


def test_botao_incluir_re_renderizado_apos_a_selecao_e_re_resolvido(browser):
    """O ajax do radio substitui o botao 'Incluir' (o no antigo fica desabilitado para sempre): o script nao pode guardar o no."""
    page = abrir(browser)
    assert abrir_inclusao(page).startswith("OK")
    assert rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)').startswith("OK")
    assert estado(page)["produtos"] == [{"id": 17189, "qtd": 2}]


def test_botao_incluir_que_nunca_habilita_cai_no_ajax_direto(browser):
    page = abrir(browser, "ids=legacy&bind=nunca")
    assert abrir_inclusao(page).startswith("OK")
    assert estado(page)["estab"] == "167"
    assert rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)').startswith("OK")
    assert estado(page)["produtos"] == [{"id": 17189, "qtd": 2}]


def test_estabelecimento_nao_encontrado(browser):
    page = abrir(browser)
    r = abrir_inclusao(page, sif="000")
    assert r.startswith("Erro: [ESTABELECIMENTO_NAO_ENCONTRADO]"), r
    assert page.evaluate("document.querySelectorAll('.ui-dialog.open').length") == 0


def test_competencia_invalida(browser):
    page = abrir(browser)
    assert rodar(page, 'abrirInclusaoProducao("2026-09", "167")').startswith("Erro: [COMPETENCIA_INVALIDA]")


def test_select_producao_cai_no_fallback_quando_o_item_do_menu_nao_responde(browser):
    page = abrir(browser, "ids=legacy&menu=0")
    assert abrir_inclusao(page).startswith("OK")
    assert estado(page)["producao"] == "S"


def test_abrir_de_novo_com_a_inclusao_ja_aberta_nao_quebra(browser):
    """Nova tentativa do mesmo comando (transitorio) reaproveita o formulario aberto."""
    page = abrir(browser)
    assert abrir_inclusao(page).startswith("OK")
    assert abrir_inclusao(page).startswith("OK")
    assert estado(page)["periodo"] == "09/2026"


def test_sem_formulario_de_producao(browser):
    page = abrir(browser)
    page.evaluate("document.getElementById('outContent').innerHTML = ''")
    assert rodar(page, 'abrirInclusaoProducao("09/2026", "167")').startswith("Erro: [SEM_FORMULARIO]")
    assert rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)').startswith("Erro: [SEM_FORMULARIO]")
    assert rodar(page, 'finalizarRegistroProducao("09/2026", 1)').startswith("Erro: [SEM_FORMULARIO]")


def test_finalizar_nao_salva_se_o_numero_de_linhas_diverge(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)')
    r = rodar(page, 'finalizarRegistroProducao("09/2026", 2)')
    assert r.startswith("Erro: [LINHAS_DIVERGENTES]") and "1 linha" in r
    assert estado(page)["saved"] is None
    # com o numero certo salva
    assert rodar(page, 'finalizarRegistroProducao("09/2026", 1)').startswith("OK")


def test_finalizar_nao_salva_se_o_cabecalho_mudou(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)')
    r = rodar(page, 'finalizarRegistroProducao("10/2026", 1)')           # competencia diferente da digitada
    assert r.startswith("Erro: [CABECALHO_INCOMPLETO]") and "10/2026" in r
    assert estado(page)["saved"] is None


def test_finalizar_repoe_periodo_que_sumiu(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)')
    page.evaluate("document.getElementById('formIncluirMapaProducao:periodoProducaoMascara').value = ''")
    r = rodar(page, 'finalizarRegistroProducao("09/2026", 1)')
    assert r.startswith("OK"), r
    assert estado(page)["saved"]["periodo"] == "09/2026"


def test_portal_recusa_mes_duplicado(browser):
    page = abrir(browser)
    page.evaluate("window.__seed([{estab: '167', periodo: '09/2026', producao: 'S', produtos: []}])")
    abrir_inclusao(page)
    rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)')
    r = rodar(page, 'finalizarRegistroProducao("09/2026", 1)')
    assert r.startswith("Erro: [FINALIZAR]") and "Já existe mapa de produção" in r
    assert len(estado(page)["registros"]) == 1


def test_finalizar_sem_produtos_recusado_pelo_portal(browser):
    page = abrir(browser)
    abrir_inclusao(page)
    r = rodar(page, 'finalizarRegistroProducao("09/2026", 0)')
    assert r.startswith("Erro: [FINALIZAR]") and "ao menos um produto" in r


def test_so_usa_funcoes_do_core_e_nao_deixa_erro_de_js(browser):
    page = abrir(browser, "ids=random")
    abrir_inclusao(page)
    rodar(page, 'incluirProdutoProducao("BACON", 17189, 2)')
    rodar(page, 'finalizarRegistroProducao("09/2026", 1)')
    assert page.__erros == []
