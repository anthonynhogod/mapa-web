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
