import io

import pytest

from app.blueprints.comercializacao.parser import validar_vendas
from helpers import LINHAS_OK, xlsx_vendas


def test_planilha_valida_le_periodo_e_registros():
    r = validar_vendas(xlsx_vendas(LINHAS_OK))
    m = r["meta"]
    assert m["errors"] == []
    assert m["periodo"] == {"ini": "2026-03-01", "fim": "2026-03-31"}
    assert m["empresa"] == "COOPERATIVA TESTE LTDA"
    assert m["counts"]["output_records"] == 5 and m["counts"]["ufs"] == 2
    # ordenado por UF, depois produto; quantidade arredondada a 2 casas
    assert [(x["uf"], x["produto"]) for x in r["records"]][:2] == [("AL", "BACON"), ("AL", "LINGUICA FRESCAL CONGELADA")]
    rs_bacon = next(x for x in r["records"] if x["uf"] == "RS" and x["produto"] == "BACON")
    assert rs_bacon["quantidade"] == pytest.approx(100.0, abs=0.011)
    assert rs_bacon["codigo"] == "00303"      # zeros a esquerda preservados


def test_sem_titulo_cabecalho_na_primeira_linha_e_sem_periodo():
    r = validar_vendas(xlsx_vendas(LINHAS_OK, titulo=False))
    assert r["meta"]["errors"] == []
    assert r["meta"]["periodo"] == {"ini": None, "fim": None}
    assert any(w["where"] == "periodo" for w in r["meta"]["warnings"])


def test_quantidade_em_texto_ptbr_e_linhas_ruins():
    r = validar_vendas(xlsx_vendas([
        ("BACON", "1", "rs", "1.234,56"),          # texto pt-BR, uf minuscula
        ("SALAME", "2", "RS", "abc"),              # invalida
        ("SALAME", "2", "SC", -5),                 # negativa
        ("SALAME", "2", "", 10),                   # sem UF
        ("", "2", "SP", 10),                       # sem produto
        ("PATE DE GALINHA", "3", "PR", 0),         # zero: ignorada com aviso
        ("PATE DE GALINHA", "3", "PR", 0.001),     # arredonda para 0: ignorada com aviso
        ("TOTAL", None, None, 99999),              # rodape de totais
    ]))
    m = r["meta"]
    assert [x["quantidade"] for x in r["records"]] == [1234.56]
    assert r["records"][0]["uf"] == "RS"
    onde = {e["where"] for e in m["errors"]}
    assert len(m["errors"]) == 4, m["errors"]
    assert len(m["warnings"]) == 2
    assert onde  # todos apontam a linha


def test_linha_repetida_soma_e_avisa():
    r = validar_vendas(xlsx_vendas([("BACON", "1", "RS", 10), ("BACON", "1", "RS", 5.5)]))
    assert [x["quantidade"] for x in r["records"]] == [15.5]
    assert any("repetido" in w["message"] for w in r["meta"]["warnings"])


def test_periodo_invertido_e_erro():
    r = validar_vendas(xlsx_vendas(LINHAS_OK, periodo="31/03/2026 até 01/03/2026"))
    assert any(e["where"] == "periodo" for e in r["meta"]["errors"])


def test_arquivo_invalido_e_sem_cabecalho_nao_levantam():
    assert validar_vendas(io.BytesIO(b"nao e xlsx"))["meta"]["errors"]
    from openpyxl import Workbook
    wb = Workbook(); wb.active.append(["a", "b"]); buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    r = validar_vendas(buf)
    assert r["meta"]["errors"][0]["where"] == "cabecalho"
