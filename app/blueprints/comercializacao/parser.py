"""Leitura/validacao da planilha de vendas (Relatorio Inspecao Federal - Vendas).

Layout esperado (o cabecalho pode estar em qualquer linha das primeiras 40):

    COOPERATIVA ... LTDA
    Relatorio Inspecao Federal - Vendas - 01/03/2026 ate 31/03/2026
    PRODUTO | CODIGO | ESTADO | QUANTIDADE
    ...

Devolve o mesmo envelope dos demais validadores do sistema:
    {"records": [...], "meta": {"errors": [...], "warnings": [...], "counts": {...}, ...}}
Nada aqui consulta o banco: o De->Para (produto/estado) e resolvido depois.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from app.utils.format import normalize_str, parse_peso_br

MAX_LINHAS_CABECALHO = 40
_RE_PERIODO = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4})\s*(?:at[eé]|a|-|–|—)\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
_RE_DATA = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")

# nome normalizado da coluna -> campo interno
_COLUNAS = {
    "produto": "produto",
    "descricao": "produto",
    "descricao do produto": "produto",
    "codigo": "codigo",
    "cod": "codigo",
    "estado": "uf",
    "uf": "uf",
    "quantidade": "quantidade",
    "qtd": "quantidade",
    "qtde": "quantidade",
}


def _issue(level: str, where: str, message: str) -> Dict[str, str]:
    return {"level": level, "where": where, "message": message}


def parse_data_br(txt: str) -> Optional[date]:
    try:
        return datetime.strptime(txt.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def _celula(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _achar_cabecalho(df: pd.DataFrame):
    """Retorna (indice_da_linha, {campo: indice_coluna}) ou (None, {})."""
    for i in range(min(len(df), MAX_LINHAS_CABECALHO)):
        mapa: Dict[str, int] = {}
        for j, v in enumerate(df.iloc[i].tolist()):
            campo = _COLUNAS.get(normalize_str(_celula(v)))
            if campo and campo not in mapa:
                mapa[campo] = j
        if {"produto", "uf", "quantidade"} <= set(mapa):
            return i, mapa
    return None, {}


def _extrair_periodo(df: pd.DataFrame, ate_linha: int):
    """Procura 'dd/mm/aaaa ate dd/mm/aaaa' acima do cabecalho."""
    for i in range(ate_linha):
        for v in df.iloc[i].tolist():
            m = _RE_PERIODO.search(_celula(v))
            if m:
                ini, fim = parse_data_br(m.group(1)), parse_data_br(m.group(2))
                if ini and fim:
                    return ini, fim
    return None, None


def _quantidade(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    return parse_peso_br(_celula(v))


def validar_vendas(origem) -> Dict[str, Any]:
    """`origem`: caminho ou file-like de um .xlsx. Nunca levanta por conteudo invalido."""
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    try:
        df = pd.read_excel(origem, header=None, dtype=object)
    except Exception as e:  # arquivo corrompido / nao-xlsx
        return _envelope([], errors=[_issue("error", "arquivo", f"Nao foi possivel ler a planilha: {e}")])

    linha_cab, cols = _achar_cabecalho(df)
    if linha_cab is None:
        return _envelope([], errors=[_issue(
            "error", "cabecalho",
            "Cabecalho nao encontrado. A planilha precisa ter as colunas PRODUTO, ESTADO e QUANTIDADE.",
        )])

    empresa = next((c for c in (_celula(v) for v in df.iloc[0].tolist()) if c), "") if linha_cab > 0 else ""
    ini, fim = _extrair_periodo(df, linha_cab)
    if not (ini and fim):
        warnings.append(_issue(
            "warning", "periodo",
            "Periodo nao identificado no titulo da planilha; informe-o manualmente.",
        ))
    elif ini > fim:
        errors.append(_issue("error", "periodo", f"Periodo invertido: {ini:%d/%m/%Y} > {fim:%d/%m/%Y}."))

    acumulado: Dict[tuple, Dict[str, Any]] = {}
    lidas = 0
    for i in range(linha_cab + 1, len(df)):
        linha = df.iloc[i].tolist()
        produto = _celula(linha[cols["produto"]])
        uf = _celula(linha[cols["uf"]]).upper()
        bruto = linha[cols["quantidade"]]
        codigo = _celula(linha[cols["codigo"]]) if "codigo" in cols else ""
        onde = f"linha {i + 1}"

        if not (produto or uf or _celula(bruto)):
            continue  # linha em branco
        if normalize_str(produto).startswith("total") and not uf:
            continue  # rodape de totais
        lidas += 1

        if not produto:
            errors.append(_issue("error", onde, "Produto vazio."))
            continue
        if not uf:
            errors.append(_issue("error", onde, f"Estado vazio para o produto {produto!r}."))
            continue

        qtd = _quantidade(bruto)
        if qtd is None:
            errors.append(_issue("error", onde, f"Quantidade invalida para {produto!r}/{uf}: {_celula(bruto)!r}."))
            continue
        if qtd < 0:
            errors.append(_issue("error", onde, f"Quantidade negativa para {produto!r}/{uf}: {qtd}."))
            continue
        arred = round(qtd, 2)
        if arred <= 0:
            warnings.append(_issue("warning", onde, f"{produto!r}/{uf} com quantidade {qtd} (arredonda para 0); ignorado."))
            continue

        chave = (normalize_str(produto), normalize_str(uf))
        if chave in acumulado:
            # mesma linha repetida na planilha = dado duplicado: soma e avisa
            acumulado[chave]["quantidade_original"] += qtd
            warnings.append(_issue("warning", onde, f"{produto!r}/{uf} repetido na planilha; quantidades somadas."))
        else:
            acumulado[chave] = {
                "produto": produto, "codigo": codigo, "uf": uf,
                "quantidade_original": qtd,
            }

    records = []
    for r in acumulado.values():
        r["quantidade"] = round(r["quantidade_original"], 2)
        records.append(r)
    records.sort(key=lambda r: (r["uf"], r["produto"]))

    if not records and not errors:
        errors.append(_issue("error", "dados", "Nenhuma linha de venda encontrada na planilha."))

    return _envelope(
        records, errors=errors, warnings=warnings, linhas=lidas,
        periodo={"ini": ini.isoformat() if ini else None, "fim": fim.isoformat() if fim else None},
        empresa=empresa,
    )


def _envelope(records, *, errors=None, warnings=None, linhas=0, periodo=None, empresa=""):
    return {
        "records": records,
        "meta": {
            "errors": errors or [],
            "warnings": warnings or [],
            "counts": {
                "input_rows": linhas,
                "output_records": len(records),
                "ufs": len({r["uf"] for r in records}),
                "produtos": len({r["produto"] for r in records}),
            },
            "periodo": periodo or {"ini": None, "fim": None},
            "empresa": empresa,
        },
    }
