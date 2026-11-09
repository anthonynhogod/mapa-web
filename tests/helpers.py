import io

from openpyxl import Workbook


def xlsx_vendas(linhas, *, titulo=True, periodo="01/03/2026 até 31/03/2026", empresa="COOPERATIVA TESTE LTDA"):
    """Monta uma planilha no layout 'Relatório Inspeção Federal - Vendas'."""
    wb = Workbook()
    ws = wb.active
    if titulo:
        ws.append([empresa])
        ws.append([])
        ws.append([f"Relatório Inspeção Federal - Vendas - {periodo}" if periodo else "Relatório Inspeção Federal - Vendas"])
        ws.append([])
    ws.append(["PRODUTO", "CÓDIGO", "ESTADO", "QUANTIDADE"])
    for l in linhas:
        ws.append(list(l))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


LINHAS_OK = [
    ("BACON", "00303", "RS", 100.005),
    ("APRESUNTADO RESFRIADO", "00301", "RS", 25739.291),
    ("BACON", "00303", "AL", 1500),
    ("LINGUICA TOSCANA CONGELADA", "00370", "AL", 10.5),
    ("LINGUICA FRESCAL CONGELADA", "00371", "AL", 4.25),   # mesmo id do portal que a TOSCANA
]


def xlsx_relatorio_mapa(linhas, *, antes=0, controle="167", extra=None):
    """Monta um relatorio no layout exportado pelo MAPA (mapaComercializacao). `linhas`: (produto, destino, total[, overrides])."""
    wb = Workbook()
    ws = wb.active
    for _ in range(antes):
        ws.append(["linha de titulo"])
    ws.append(["Operação", "Âmbito", "Controle", "Estabelecimento", "UF", "Município", "Grupo", "Espécie", "Produto",
               "Unidade", "Controle Origem (Receb)", "Pessoa Origem (Receb)", "UF/Pais", "Municipio Origem (Receb)", "Total"])
    for ln in linhas:
        produto, destino, total = ln[:3]
        o = {"Operação": "UF", "Unidade": "Quilograma", "Controle Origem (Receb)": "", "Pessoa Origem (Receb)": ""}
        o.update(ln[3] if len(ln) > 3 else {})
        ws.append([o["Operação"], "SIF", controle, "EMPRESA TESTE", "RS", "ENCANTADO", "Suídeos", "Suíno", produto,
                   o["Unidade"], o["Controle Origem (Receb)"], o["Pessoa Origem (Receb)"], destino, "", total])
    for e in extra or []:
        ws.append(e)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
