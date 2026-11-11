"""Leitura/validacao da planilha de PRODUCAO (Relatorio Inspecao Federal - Producao).

Layout esperado (o cabecalho pode estar em qualquer linha das primeiras 40):

    COOPERATIVA ... LTDA
    Relatorio Inspecao Federal - Producao - 01/09/2026 ate 04/11/2026
    PRODUTO | CODIGO | 244/26 01/09/2026 | 245/26 02/09/2026 | ... | TOTAL
    ...
    TOTAL                                                           (rodape: ignorado)

So `PRODUTO` e `TOTAL` entram no lancamento. As colunas de dia servem para descobrir o MES (o titulo
NAO serve: o intervalo do relatorio pode ir alem do mes com dados) e para conferir o TOTAL.

O mapa de producao e MENSAL: a competencia (MM/AAAA) vem das colunas de dia que tem quantidade; se elas
cobrem mais de um mes a planilha e recusada (o TOTAL misturaria meses) a menos que a competencia seja
informada manualmente.

Devolve o mesmo envelope dos demais validadores: {"records": [...], "meta": {...}}.
Nada aqui consulta o banco: o De->Para (produto) e resolvido depois.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.utils.format import normalize_str, parse_peso_br

MAX_LINHAS_CABECALHO = 40
_RE_DATA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_RE_COMPETENCIA = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{4})\s*$")
TOLERANCIA_TOTAL = 0.01          # diferenca aceita entre TOTAL e a soma dos dias (ruido de ponto flutuante)


def _issue(level: str, where: str, message: str) -> Dict[str, str]:
    return {"level": level, "where": where, "message": message}


def parse_competencia(txt: str) -> Optional[Tuple[int, int]]:
    """'09/2026' -> (2026, 9); invalido -> None."""
    m = _RE_COMPETENCIA.match(txt or "")
    if not m:
        return None
    mes, ano = int(m.group(1)), int(m.group(2))
    return (ano, mes) if 1 <= mes <= 12 and 2000 <= ano <= 2100 else None


def periodo_do_mes(ano: int, mes: int) -> Tuple[date, date]:
    """(primeiro dia, ultimo dia) do mes."""
    return date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])


def _celula(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _numero(v: Any) -> Optional[float]:
    """Celula de quantidade -> float; vazio -> None; lixo -> levanta ValueError."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, bool):
        raise ValueError(v)
    if isinstance(v, (int, float)):
        return float(v)
    txt = _celula(v)
    if not txt:
        return None
    n = parse_peso_br(txt)
    if n is None:
        raise ValueError(txt)
    return float(n)


def _data_do_titulo(v: Any) -> Optional[date]:
    """Cabecalho de coluna de dia: '244/26 01/09/2026' (texto) ou data do Excel."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    achados = _RE_DATA.findall(_celula(v))
    if not achados:
        return None
    d, m, a = (int(x) for x in achados[-1])
    try:
        return date(a, m, d)
    except ValueError:
        return None


def _achar_cabecalho(df: pd.DataFrame):
    """(linha, coluna_produto, coluna_total, coluna_codigo|None) ou (None, ...)."""
    for i in range(min(len(df), MAX_LINHAS_CABECALHO)):
        produto = total = codigo = None
        for j, v in enumerate(df.iloc[i].tolist()):
            nome = normalize_str(_celula(v))
            if nome in ("produto", "descricao", "descricao do produto") and produto is None:
                produto = j
            elif nome == "total" and total is None:
                total = j
            elif nome in ("codigo", "cod") and codigo is None:
                codigo = j
        if produto is not None and total is not None:
            return i, produto, total, codigo
    return None, None, None, None


def validar_producao(origem, competencia: Optional[str] = None) -> Dict[str, Any]:
    """
    `origem`: caminho ou file-like de um .xlsx. `competencia`: 'MM/AAAA' informada manualmente (opcional;
    vence a lida da planilha). Nunca levanta por conteudo invalido.
    """
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    try:
        df = pd.read_excel(origem, header=None, dtype=object)
    except Exception as e:  # arquivo corrompido / nao-xlsx
        return _envelope([], errors=[_issue("error", "arquivo", f"Nao foi possivel ler a planilha: {e}")])

    linha_cab, c_prod, c_total, c_cod = _achar_cabecalho(df)
    if linha_cab is None:
        return _envelope([], errors=[_issue(
            "error", "cabecalho",
            "Cabecalho nao encontrado. A planilha precisa ter as colunas PRODUTO e TOTAL.",
        )])

    empresa = next((c for c in (_celula(v) for v in df.iloc[0].tolist()) if c), "") if linha_cab > 0 else ""
    cab = df.iloc[linha_cab].tolist()
    colunas_dia = [(j, _data_do_titulo(cab[j])) for j in range(len(cab)) if j not in (c_prod, c_total, c_cod)]
    colunas_dia = [(j, d) for j, d in colunas_dia if d is not None]

    # ---- linhas de produto ----
    acumulado: Dict[str, Dict[str, Any]] = {}
    meses_com_dado: Dict[Tuple[int, int], int] = {}
    divergentes: List[str] = []
    lidas = 0
    for i in range(linha_cab + 1, len(df)):
        linha = df.iloc[i].tolist()
        produto = _celula(linha[c_prod])
        bruto = linha[c_total]
        codigo = _celula(linha[c_cod]) if c_cod is not None else ""
        onde = f"linha {i + 1}"

        if not (produto or _celula(bruto)):
            continue                                   # linha em branco
        if normalize_str(produto) == "total" and not codigo:
            continue                                   # rodape de totais
        lidas += 1
        if not produto:
            errors.append(_issue("error", onde, "Produto vazio."))
            continue
        try:
            qtd = _numero(bruto)
        except ValueError:
            errors.append(_issue("error", onde, f"Total invalido para {produto!r}: {_celula(bruto)!r}."))
            continue
        if qtd is None:
            warnings.append(_issue("warning", onde, f"{produto!r} sem TOTAL; ignorado."))
            continue
        if qtd < 0:
            errors.append(_issue("error", onde, f"Total negativo para {produto!r}: {qtd}."))
            continue
        if round(qtd, 2) <= 0:
            warnings.append(_issue("warning", onde, f"{produto!r} com total {qtd} (arredonda para 0); ignorado."))
            continue

        # dias com quantidade (descobre o mes) e soma dos dias (confere o TOTAL)
        soma_dias = 0.0
        for j, d in colunas_dia:
            try:
                v = _numero(linha[j])
            except ValueError:
                errors.append(_issue("error", onde, f"Quantidade invalida em {d:%d/%m/%Y} para {produto!r}: {_celula(linha[j])!r}."))
                continue
            if v:
                soma_dias += v
                meses_com_dado[(d.year, d.month)] = meses_com_dado.get((d.year, d.month), 0) + 1
        if colunas_dia and abs(soma_dias - qtd) > TOLERANCIA_TOTAL:
            divergentes.append(f"{produto!r}: TOTAL {qtd:g} x soma dos dias {soma_dias:g}")

        chave = normalize_str(produto)
        if chave in acumulado:
            acumulado[chave]["quantidade_original"] += qtd
            warnings.append(_issue("warning", onde, f"{produto!r} repetido na planilha; totais somados."))
        else:
            acumulado[chave] = {"produto": produto, "codigo": codigo, "quantidade_original": qtd}

    if divergentes:
        warnings.append(_issue(
            "warning", "total",
            f"TOTAL diferente da soma dos dias em {len(divergentes)} produto(s) (vale o TOTAL): "
            + "; ".join(divergentes[:3]) + ("..." if len(divergentes) > 3 else ""),
        ))

    # ---- competencia (mes) ----
    lida = None
    if len(meses_com_dado) == 1:
        lida = next(iter(meses_com_dado))
    elif len(meses_com_dado) > 1:
        meses = ", ".join(f"{m:02d}/{a}" for a, m in sorted(meses_com_dado))
        if competencia is None:
            errors.append(_issue(
                "error", "periodo",
                f"A planilha tem producao em mais de um mes ({meses}); o TOTAL misturaria os meses. "
                "Exporte um mes por vez.",
            ))
    elif not colunas_dia:
        warnings.append(_issue("warning", "periodo", "Colunas de dia nao encontradas; informe a competencia (MM/AAAA)."))
    else:
        warnings.append(_issue("warning", "periodo", "Nenhuma quantidade nas colunas de dia; informe a competencia (MM/AAAA)."))

    final = lida
    origem_comp = "colunas de dia" if lida else None
    if competencia:
        manual = parse_competencia(competencia)
        if manual is None:
            errors.append(_issue("error", "periodo", f"Competencia invalida: {competencia!r}. Use MM/AAAA."))
        else:
            if lida and manual != lida:
                warnings.append(_issue(
                    "warning", "periodo",
                    f"Competencia informada ({manual[1]:02d}/{manual[0]}) difere da lida nas colunas de dia "
                    f"({lida[1]:02d}/{lida[0]}); vale a informada.",
                ))
            if len(meses_com_dado) > 1:
                errors.append(_issue(
                    "error", "periodo",
                    "A planilha tem mais de um mes: informar a competencia nao separa os totais. Exporte um mes por vez.",
                ))
            final, origem_comp = manual, "informada"

    records = []
    for r in acumulado.values():
        r["quantidade"] = round(r["quantidade_original"], 2)
        records.append(r)
    records.sort(key=lambda r: normalize_str(r["produto"]))

    if not records and not errors:
        errors.append(_issue("error", "dados", "Nenhuma linha de producao encontrada na planilha."))

    comp = None
    if final:
        ini, fim = periodo_do_mes(*final)
        comp = {"ano": final[0], "mes": final[1], "texto": f"{final[1]:02d}/{final[0]}",
                "ini": ini.isoformat(), "fim": fim.isoformat(), "origem": origem_comp}
    return _envelope(records, errors=errors, warnings=warnings, linhas=lidas, competencia=comp, empresa=empresa)


def _envelope(records, *, errors=None, warnings=None, linhas=0, competencia=None, empresa=""):
    return {
        "records": records,
        "meta": {
            "errors": errors or [],
            "warnings": warnings or [],
            "counts": {
                "input_rows": linhas,
                "output_records": len(records),
                "produtos": len(records),
            },
            "competencia": competencia,
            "empresa": empresa,
        },
    }
