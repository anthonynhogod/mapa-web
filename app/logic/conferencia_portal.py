"""Conferência LINHA A LINHA do registro no portal do MAPA × o que deve estar lançado.

Fonte dos dados reais: `lerRegistroComercializacao()` (comercializacao.js), que lê as tabelas do próprio portal:
  - dtbTransacoes: id da transação e UF;
  - dtbProduto: uma linha por produto lançado (ID Transação, Produto, Quantidade), SEM agrupar.
O relatório exportado agrupa por descrição; a tabela do portal não. Por isso aqui dá para achar exatamente QUAL linha falta.

Chave de comparação: (UF, descrição normalizada, quantidade com 2 casas). As linhas do portal não trazem o id do produto;
produtos internos diferentes com a mesma descrição (ex.: BACON 17189 e 17186) se distinguem pela quantidade, e um empate
de chave entre linhas equivalentes é tratado como multiconjunto (não importa qual das duas "sobra").
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.logic import verificacao as V
from app.utils.format import normalize_str

TOLERANCIA = 0.005


class LeituraInvalida(ValueError):
    """A leitura do portal não tem o formato esperado (não confundir com divergência)."""


def _q(v: Any) -> float:
    return round(float(v or 0), 2)


def linhas_do_portal(leitura: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve a UF de cada linha de produto pela transação. Devolve (linhas, avisos)."""
    if not isinstance(leitura, dict) or "transacoes" not in leitura or "produtos" not in leitura:
        raise LeituraInvalida("A leitura do portal não trouxe as tabelas de transações e produtos.")
    if not leitura.get("tabela_produtos", True):
        raise LeituraInvalida("A tela do portal não mostrou a tabela de produtos; não dá para conferir linha a linha.")
    uf_por_id = {str(t.get("id")): (t.get("uf") or "?").upper() for t in leitura["transacoes"]}
    avisos: List[str] = []
    linhas = []
    sem_uf = 0
    for p in leitura["produtos"]:
        if p.get("quantidade") is None:
            raise LeituraInvalida(f"Quantidade ilegível no portal para {p.get('produto')!r}.")
        uf = uf_por_id.get(str(p.get("transacao")))
        if uf is None:
            sem_uf += 1
            uf = "?"
        linhas.append({"uf": uf, "produto": p.get("produto"), "quantidade": _q(p["quantidade"]),
                       "transacao": p.get("transacao")})
    if sem_uf:
        avisos.append(f"{sem_uf} linha(s) de produto apontam para uma transação que não está na tabela de transações lida.")
    return linhas, avisos


def diferenca(itens: Iterable[Dict[str, Any]], linhas: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """
    itens  : o que DEVE estar lançado [{uf, descricao, produto, id, quantidade}]
    linhas : o que o portal TEM [{uf, produto, quantidade, transacao}]
    -> {"ok": n, "faltando": [...], "sobrando": [...], "diferentes": [...]}
    """
    plano: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for it in itens:
        plano[(str(it["uf"]).upper(), normalize_str(it["descricao"]), _q(it["quantidade"]))].append(it)
    portal: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for ln in linhas:
        portal[(str(ln["uf"]).upper(), normalize_str(ln["produto"]), _q(ln["quantidade"]))].append(ln)

    ok = 0
    faltando: List[Dict[str, Any]] = []
    sobrando: List[Dict[str, Any]] = []
    for k in set(plano) | set(portal):
        a, b = plano.get(k, []), portal.get(k, [])
        casadas = min(len(a), len(b))
        ok += casadas
        faltando += [{"uf": k[0], "descricao": x["descricao"], "produto": x.get("produto"), "id": x.get("id"),
                      "quantidade": k[2]} for x in a[casadas:]]
        sobrando += [{"uf": k[0], "produto": x["produto"], "quantidade": k[2], "transacao": x.get("transacao")}
                     for x in b[casadas:]]

    # mesma UF + mesma descricao faltando num lado e sobrando no outro = QUANTIDADE diferente (nao falta + sobra)
    por_chave_f: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for f in faltando:
        por_chave_f[(f["uf"], normalize_str(f["descricao"]))].append(f)
    por_chave_s: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for s in sobrando:
        por_chave_s[(s["uf"], normalize_str(s["produto"]))].append(s)
    diferentes: List[Dict[str, Any]] = []
    faltando_final: List[Dict[str, Any]] = []
    sobrando_final: List[Dict[str, Any]] = []
    for k in set(por_chave_f) | set(por_chave_s):
        fs = sorted(por_chave_f.get(k, []), key=lambda x: x["quantidade"])
        ss = sorted(por_chave_s.get(k, []), key=lambda x: x["quantidade"])
        for f, s in zip(fs, ss):
            diferentes.append({"uf": f["uf"], "descricao": f["descricao"], "produto": f.get("produto"), "id": f.get("id"),
                               "esperado": f["quantidade"], "portal": s["quantidade"],
                               "diff": round(s["quantidade"] - f["quantidade"], 2), "transacao": s.get("transacao")})
        faltando_final += fs[len(ss):]
        sobrando_final += ss[len(fs):]
    ordem = lambda x: (x["uf"], normalize_str(x.get("descricao") or x.get("produto") or ""), x.get("quantidade", x.get("esperado", 0)))  # noqa: E731
    return {"ok": ok, "faltando": sorted(faltando_final, key=ordem), "sobrando": sorted(sobrando_final, key=ordem),
            "diferentes": sorted(diferentes, key=ordem)}


def ambiguas(itens: Iterable[Dict[str, Any]]) -> List[str]:
    """Itens do plano com a MESMA chave (UF, descrição, quantidade) mas ids de produto diferentes: o portal não distingue."""
    vistos: Dict[Tuple, set] = defaultdict(set)
    for it in itens:
        vistos[(str(it["uf"]).upper(), normalize_str(it["descricao"]), _q(it["quantidade"]))].add(it.get("id"))
    return [f"{k[0]} · {k[1]} · {k[2]}" for k, ids in vistos.items() if len(ids) > 1]


def verificar_portal(itens: List[Dict[str, Any]], leitura: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resultado completo (compatível com a conferência do relatório: células descrição × UF) + o detalhe por linha.
    O STATUS é decidido pela comparação linha a linha (mais exigente que a por célula).
    """
    linhas, avisos = linhas_do_portal(leitura)
    cel = V.verificar(itens, [{"produto": l["produto"], "destino": l["uf"], "total": l["quantidade"]} for l in linhas])
    cel.pop("avisos", None)
    dif = diferenca(itens, linhas)
    n_div = len(dif["faltando"]) + len(dif["sobrando"]) + len(dif["diferentes"])
    amb = ambiguas(itens)
    if amb:
        avisos.append("Itens do plano que o portal não distingue (mesma UF, descrição e quantidade com produtos diferentes): "
                      + "; ".join(amb[:6]) + ".")
    cel["status"] = "OK" if n_div == 0 else "DIVERGENTE"
    cel["linhas_diff"] = dif
    cel["totais"]["linhas_plano"] = len(itens)
    cel["totais"]["linhas_portal"] = len(linhas)
    cel["totais"]["linhas_divergentes"] = n_div
    cel["avisos"] = avisos
    return cel


def itens_a_complementar(dif: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Linhas que só precisam ser INCLUÍDAS (faltando). Sobrando/quantidade diferente exigem excluir: fora desta etapa."""
    return [f for f in dif["faltando"] if f.get("id") is not None]
