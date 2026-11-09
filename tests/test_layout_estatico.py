"""Regressoes de layout verificaveis sem navegador (o conferido visualmente foi a 420px e 1366px, claro/escuro)."""
import glob
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEMPLATES = glob.glob(os.path.join(ROOT, "app", "templates", "**", "*.html"), recursive=True)


def _ler(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_toda_tabela_de_registros_tem_wrapper_de_rolagem():
    """.tabela-registros tem min-width: sem .table-scroll a pagina inteira rola de lado no celular."""
    sem = []
    for p in TEMPLATES:
        s = _ler(p)
        for m in re.finditer(r'<table class="tabela-registros', s):
            if not s[:m.start()].rstrip().endswith('table-scroll">'):
                sem.append(os.path.relpath(p, ROOT))
    assert not sem, f"tabela sem .table-scroll em: {sem}"


def test_sem_bordas_e_estilos_inline_de_layout_nas_tabelas_e_titulos():
    ruins = []
    for p in TEMPLATES:
        s = _ler(p)
        if re.search(r'<table[^>]*border="1"', s) or re.search(r'class="titulo-form"\s+style=', s):
            ruins.append(os.path.relpath(p, ROOT))
    assert not ruins, ruins


def test_ui_css_carregado_depois_do_style_css():
    base = _ler(os.path.join(ROOT, "app", "templates", "base.html"))
    assert base.index("css/style.css") < base.index("css/ui.css")


def test_menu_sem_link_solto_dentro_de_ul():
    for nome in ("layout.html", os.path.join("admin", "layout.html")):
        s = _ler(os.path.join(ROOT, "app", "templates", nome))
        for ul in re.findall(r"<ul[^>]*>(.*?)</ul>", s, re.S):
            assert not re.search(r"<ul[^>]*>\s*<a ", "<ul>" + ul), f"<a> direto dentro de <ul> em {nome}"
            for filho in re.findall(r"<a\b", ul):
                pass
            assert ul.count("<a") == ul.count("<li"), f"cada link do menu precisa de <li> em {nome}"
