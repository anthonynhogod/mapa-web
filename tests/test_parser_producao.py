"""Parser da planilha de producao (PRODUTO + TOTAL; mes lido das colunas de dia)."""
import io

from openpyxl import Workbook

from app.blueprints.producao.parser import parse_competencia, periodo_do_mes, validar_producao
from helpers import LINHAS_PRODUCAO, xlsx_producao


def _msgs(meta, nivel):
    return [i["message"] for i in meta[nivel]]


def test_planilha_ok_usa_total_e_descobre_o_mes_pelas_colunas_de_dia():
    r = validar_producao(xlsx_producao(LINHAS_PRODUCAO))
    meta = r["meta"]
    assert meta["errors"] == []
    # titulo diz "ate 04/11/2026" mas o mes vem dos dias com producao
    assert meta["competencia"]["texto"] == "09/2026"
    assert (meta["competencia"]["ini"], meta["competencia"]["fim"]) == ("2026-09-01", "2026-09-30")
    assert meta["competencia"]["origem"] == "colunas de dia"
    assert meta["empresa"] == "COOPERATIVA TESTE LTDA"
    por = {x["produto"]: x for x in r["records"]}
    assert set(por) == {"APRESUNTADO RESFRIADO", "BACON", "BACON RESFRIADO FATIADO"}   # rodape e total 0 fora
    assert por["APRESUNTADO RESFRIADO"]["quantidade"] == 300.5
    assert por["BACON"]["quantidade"] == 15.26 and por["BACON"]["codigo"] == "00303"
    assert any("SEM MOVIMENTO" in m for m in _msgs(meta, "warnings"))


def test_ruido_de_ponto_flutuante_e_arredondado():
    r = validar_producao(xlsx_producao([("BACON", "1", [0.1, 0.2, None], 0.30000000000000004)]))
    assert r["records"][0]["quantidade"] == 0.3
    assert r["meta"]["warnings"] == []          # TOTAL x soma dos dias dentro da tolerancia


def test_total_diferente_da_soma_dos_dias_avisa_e_vale_o_total():
    r = validar_producao(xlsx_producao([("BACON", "1", [10, 20, None], 99)]))
    assert r["records"][0]["quantidade"] == 99
    assert any("diferente da soma dos dias" in m for m in _msgs(r["meta"], "warnings"))


def test_mais_de_um_mes_e_recusado():
    r = validar_producao(xlsx_producao([("BACON", "1", [10, 20, None], None)], dias=("30/09/2026", "01/10/2026", "02/10/2026")))
    assert any("mais de um mes" in m for m in _msgs(r["meta"], "errors"))
    # informar a competencia nao resolve: o TOTAL continuaria misturando meses
    r = validar_producao(xlsx_producao([("BACON", "1", [10, 20, None], None)], dias=("30/09/2026", "01/10/2026", "02/10/2026")), "10/2026")
    assert any("mais de um mes" in m for m in _msgs(r["meta"], "errors"))


def test_dia_sem_producao_nao_conta_para_o_mes():
    # coluna de outubro existe, mas so tem dados em setembro
    r = validar_producao(xlsx_producao([("BACON", "1", [10, None, None], None)], dias=("30/09/2026", "01/10/2026", "02/10/2026")))
    assert r["meta"]["errors"] == [] and r["meta"]["competencia"]["texto"] == "09/2026"


def test_competencia_manual_vale_e_avisa_quando_difere():
    r = validar_producao(xlsx_producao(LINHAS_PRODUCAO), "08/2026")
    assert r["meta"]["competencia"]["texto"] == "08/2026" and r["meta"]["competencia"]["origem"] == "informada"
    assert any("difere" in m for m in _msgs(r["meta"], "warnings"))


def test_competencia_manual_invalida():
    r = validar_producao(xlsx_producao(LINHAS_PRODUCAO), "13/2026")
    assert any("Competencia invalida" in m for m in _msgs(r["meta"], "errors"))


def test_sem_colunas_de_dia_exige_competencia():
    wb = Workbook()
    ws = wb.active
    ws.append(["PRODUTO", "TOTAL"])
    ws.append(["BACON", 10])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    r = validar_producao(buf)
    assert r["meta"]["competencia"] is None and r["records"][0]["quantidade"] == 10
    buf.seek(0)
    assert validar_producao(buf, "09/2026")["meta"]["competencia"]["texto"] == "09/2026"


def test_duplicado_soma_e_avisa_total_invalido_e_negativo():
    r = validar_producao(xlsx_producao([
        ("BACON", "1", [10, None, None], None),
        ("bacon", "1", [5, None, None], None),
        ("SALAME", "2", [1, None, None], -3),
        ("PRESUNTO", "3", [1, None, None], "abc"),
    ]))
    meta = r["meta"]
    assert any("repetido" in m for m in _msgs(meta, "warnings"))
    assert any("negativo" in m for m in _msgs(meta, "errors"))
    assert any("invalido" in m for m in _msgs(meta, "errors"))
    assert [x for x in r["records"] if x["produto"] == "BACON"][0]["quantidade"] == 15


def test_total_em_texto_pt_br():
    r = validar_producao(xlsx_producao([("BACON", "1", [None, None, None], "1.234,50")]))
    assert r["records"][0]["quantidade"] == 1234.5


def test_cabecalho_ausente_e_arquivo_invalido():
    wb = Workbook(); wb.active.append(["a", "b"])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    assert any("Cabecalho" in m for m in _msgs(validar_producao(buf)["meta"], "errors"))
    assert _msgs(validar_producao(io.BytesIO(b"nao e xlsx"))["meta"], "errors")


def test_planilha_vazia():
    r = validar_producao(xlsx_producao([]))
    assert any("Nenhuma linha" in m for m in _msgs(r["meta"], "errors"))


def test_helpers_de_competencia():
    assert parse_competencia("09/2026") == (2026, 9) and parse_competencia(" 9 / 2026 ") == (2026, 9)
    assert parse_competencia("00/2026") is None and parse_competencia("2026-09") is None
    assert periodo_do_mes(2028, 2) == (periodo_do_mes(2028, 2)[0], periodo_do_mes(2028, 2)[0].replace(day=29))
