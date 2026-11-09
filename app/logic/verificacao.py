"""Conferência dos lançamentos: relatório do MAPA × o que foi enviado.

Por que por CÉLULA (descrição do MAPA × UF de destino)?
  O relatório do MAPA já vem AGRUPADO: produtos internos diferentes com a mesma descrição (ex.: APRESUNTADO RESFRIADO e
  APRESUNTADO RESFRIADO FATIADO) viram uma linha só por UF. Descrição × UF é, portanto, o menor nível comparável.
  Totais só por estado e só por descrição NÃO bastam: erros se compensam (ex.: +x em Bacon/MT e -x em Bacon/TO deixam
  igual o total de Bacon e o total de cada UF). Aqui os totais por UF, por descrição e o geral são DERIVADOS das
  células, nunca calculados separados.

Limite: abaixo da célula o relatório não enxerga; mostramos os produtos/ids internos que compõem cada célula para
facilitar a investigação.
"""
from __future__ import annotations

import itertools
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from app.utils.format import normalize_str, parse_peso_br

EPS = 0.005                  # ruído de ponto flutuante (as quantidades têm 2 casas)
TOL_ARRED_POR_ITEM = 0.01    # diferença de até 0,01 por item enviado vale como arredondamento
MAX_LINHAS_CABECALHO = 40

# nome normalizado (normalize_str tira acento/pontuação: "UF/Pais" -> "ufpais") -> campo interno
_COLUNAS = {
    "operacao": "operacao", "ambito": "ambito", "controle": "controle", "estabelecimento": "estabelecimento",
    "uf": "uf_estab", "grupo": "grupo", "especie": "especie", "produto": "produto", "unidade": "unidade",
    "controle origem receb": "origem_controle", "pessoa origem receb": "origem_pessoa",
    "municipio origem receb": "origem_municipio", "ufpais": "destino", "uf pais": "destino",
    "total": "total", "quantidade": "total",
}

STATUS_ROTULO = {
    "ok": "Confere",
    "arredondamento": "Arredondamento",
    "divergente": "Quantidade diferente",
    "faltando_no_mapa": "Falta no MAPA",
    "a_mais_no_mapa": "A mais no MAPA",
    "nome_nosso_sem_par": "Nome sem correspondência (nosso)",
    "nome_mapa_sem_par": "Nome sem correspondência (MAPA)",
}
STATUS_OK = {"ok", "arredondamento"}


def _issue(level: str, where: str, message: str) -> Dict[str, str]:
    return {"level": level, "where": where, "message": message}


def _celula(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _numero(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    return parse_peso_br(_celula(v))


def _sem_zeros(v: Any) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    return s.lstrip("0") or ("0" if s else "")


# ---------------------------------------------------------------------------------------------
# Leitura do relatório do MAPA
# ---------------------------------------------------------------------------------------------
def ler_relatorio_mapa(origem) -> Dict[str, Any]:
    """
    `origem`: caminho/file-like do .xlsx exportado do MAPA ("mapaComercializacao").
    Devolve {"linhas": [{produto, destino, total, linha}], "meta": {errors, warnings, ignoradas, controles, ...}}.
    Só entram linhas de venda por UF (Operação = UF, sem origem de recebimento, em quilogramas); o restante é
    contado em `meta.ignoradas` para ficar claro o que não foi conferido.
    """
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    try:
        df = pd.read_excel(origem, header=None, dtype=object)
    except Exception as e:  # noqa: BLE001
        return _env_mapa([], errors=[_issue("error", "arquivo", f"Não foi possível ler a planilha: {e}")])

    cab, cols = None, {}
    for i in range(min(len(df), MAX_LINHAS_CABECALHO)):
        mapa: Dict[str, int] = {}
        for j, v in enumerate(df.iloc[i].tolist()):
            campo = _COLUNAS.get(normalize_str(_celula(v)))
            if campo and campo not in mapa:
                mapa[campo] = j
        if {"produto", "destino", "total"} <= set(mapa):
            cab, cols = i, mapa
            break
    if cab is None:
        return _env_mapa([], errors=[_issue(
            "error", "cabecalho",
            "Cabeçalho não encontrado. O relatório do MAPA precisa ter as colunas Produto, UF/Pais e Total.")])

    linhas: List[Dict[str, Any]] = []
    ignoradas: Dict[str, int] = {}
    controles, estabs = set(), set()

    def ignorar(motivo: str):
        ignoradas[motivo] = ignoradas.get(motivo, 0) + 1

    for i in range(cab + 1, len(df)):
        r = df.iloc[i].tolist()
        get = lambda campo: _celula(r[cols[campo]]) if campo in cols else ""  # noqa: E731
        produto, destino, bruto = get("produto"), get("destino").upper(), r[cols["total"]]
        if not (produto or destino or _celula(bruto)):
            continue
        if normalize_str(produto).startswith("total") and not destino:
            continue                                     # rodapé de totais
        onde = f"linha {i + 1}"
        op = normalize_str(get("operacao"))
        if op and op != "uf":
            ignorar(f"operação {get('operacao')!r} (só vendas por UF são conferidas)")
            continue
        if get("origem_controle") or get("origem_pessoa"):
            ignorar("recebimentos (têm origem informada)")
            continue
        un = normalize_str(get("unidade"))
        if un and un not in ("quilograma", "quilogramas", "kg"):
            ignorar(f"unidade {get('unidade')!r} (só quilograma é conferido)")
            continue
        if not produto or not destino:
            errors.append(_issue("error", onde, "Produto ou UF/País vazio."))
            continue
        total = _numero(bruto)
        if total is None:
            errors.append(_issue("error", onde, f"Total inválido para {produto!r}/{destino}: {_celula(bruto)!r}."))
            continue
        if get("controle"):
            controles.add(get("controle"))
        if get("estabelecimento"):
            estabs.add(get("estabelecimento"))
        linhas.append({"produto": produto, "destino": destino, "total": total, "linha": i + 1})

    if not linhas and not errors:
        errors.append(_issue("error", "dados", "Nenhuma linha de venda por UF encontrada no relatório."))
    if len(controles) > 1:
        warnings.append(_issue("warning", "controle", f"O relatório tem mais de um estabelecimento (controle): {sorted(controles)}."))
    return _env_mapa(linhas, errors=errors, warnings=warnings, ignoradas=ignoradas,
                     controles=sorted(controles), estabelecimentos=sorted(estabs))


def _env_mapa(linhas, *, errors=None, warnings=None, ignoradas=None, controles=None, estabelecimentos=None):
    return {"linhas": linhas, "meta": {
        "errors": errors or [], "warnings": warnings or [], "ignoradas": ignoradas or {},
        "controles": controles or [], "estabelecimentos": estabelecimentos or [],
        "counts": {"linhas": len(linhas), "ignoradas": sum((ignoradas or {}).values())},
    }}


# ---------------------------------------------------------------------------------------------
# Base de comparação: o que foi ENVIADO
# ---------------------------------------------------------------------------------------------
def itens_do_plano(plano: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """plano = [{uf, itens: [{produto, descricao, id, quantidade}]}] -> lista plana de itens."""
    out = []
    for bloco in plano or []:
        for it in bloco.get("itens", []):
            out.append({"uf": str(bloco.get("uf", "")).upper(), "produto": it.get("produto"),
                        "descricao": it.get("descricao"), "id": it.get("id"), "quantidade": float(it.get("quantidade") or 0)})
    return out


_RE_CMD_PRODUTO = re.compile(r'incluirProdutoVenda\(\s*("(?:[^"\\]|\\.)*")\s*,\s*[-\d.]+\s*,\s*([-\d.]+)\s*,\s*("(?:[^"\\]|\\.)*")\s*,\s*(\d+)')


def itens_dos_comandos(comandos: Iterable[str]) -> List[Dict[str, Any]]:
    """Reconstrói os itens enviados a partir dos comandos JS do job (jobs antigos, sem plano gravado)."""
    out = []
    for c in comandos or []:
        m = _RE_CMD_PRODUTO.search(c or "")
        if not m:
            continue
        try:
            uf, desc = json.loads(m.group(1)), json.loads(m.group(3))
        except ValueError:
            continue
        out.append({"uf": uf.upper(), "produto": None, "descricao": desc, "id": int(m.group(4)),
                    "quantidade": float(m.group(2))})
    return out


def base_do_registro(registro) -> Tuple[List[Dict[str, Any]], str, List[str], Optional[int]]:
    """
    (itens, origem, avisos, job_id). Ordem de preferência: plano gravado no último job com SUCESSO ->
    comandos desse job -> planilha reprocessada com o De->Para ATUAL (com aviso: pode diferir do enviado).
    Levanta ValueError se não houver dado nenhum.
    """
    from app.models import ExecJob, VendasTmp
    avisos: List[str] = []
    jobs = ExecJob.query.filter_by(registro_id=registro.id).order_by(ExecJob.id.desc()).all()
    escolhido = next((j for j in jobs if j.status == "SUCESSO"), jobs[0] if jobs else None)
    if escolhido is not None:
        if escolhido.status != "SUCESSO":
            avisos.append(f"Nenhum job deste registro terminou com sucesso; usando o job #{escolhido.id} ({escolhido.status}).")
        itens = itens_do_plano((escolhido.meta or {}).get("plano"))
        if itens:
            return itens, "job_plano", avisos, escolhido.id
        itens = itens_dos_comandos(escolhido.commands)
        if itens:
            avisos.append(f"O job #{escolhido.id} não guardou o plano; os itens foram lidos dos comandos executados.")
            return itens, "job_comandos", avisos, escolhido.id
    tmp = VendasTmp.query.filter_by(registro_id=registro.id).one_or_none()
    records = ((tmp.payload or {}).get("records") if tmp else None) or []
    if not records:
        raise ValueError("Este registro não tem job com plano nem planilha de vendas guardada: envie a planilha de vendas.")
    itens, pend = itens_da_planilha(records)
    if pend:
        raise ValueError("Produtos/UFs sem vínculo no De→Para: " + "; ".join(pend[:8]))
    avisos.append("Não há job com plano gravado: a base foi RECALCULADA da planilha com o De→Para de hoje e pode "
                  "diferir do que foi realmente enviado.")
    return itens, "planilha_reprocessada", avisos, escolhido.id if escolhido else None


def itens_da_planilha(records: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve produto/UF da planilha de vendas no De->Para atual. Devolve (itens, pendências em texto)."""
    from app.logic.comercializacao import build_plano
    from app.logic.constantes import ColetorPendencias
    col = ColetorPendencias()
    plano = build_plano(list(records), col, backend="browser")
    return itens_do_plano(plano), [f"{p['rotulo']}: {p['valor']}" for p in col.listar()]


# ---------------------------------------------------------------------------------------------
# Agregação e comparação por célula
# ---------------------------------------------------------------------------------------------
def _chave(desc: Any, uf: Any) -> Tuple[str, str]:
    return normalize_str(str(desc or "")), str(uf or "").strip().upper()


def celulas_nossas(itens: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    cel: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for it in itens:
        k = _chave(it["descricao"], it["uf"])
        c = cel.setdefault(k, {"descricao": it["descricao"], "uf": k[1], "qtd": 0.0, "itens": []})
        q = round(float(it["quantidade"]), 2)              # é o valor que foi enviado ao portal
        c["qtd"] += q
        c["itens"].append({"produto": it.get("produto"), "id": it.get("id"), "quantidade": q})
    return cel


def celulas_mapa(linhas: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    cel: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ln in linhas:
        k = _chave(ln["produto"], ln["destino"])
        c = cel.setdefault(k, {"descricao": ln["produto"], "uf": k[1], "qtd": 0.0, "linhas": 0})
        c["qtd"] += float(ln["total"])                      # soma defensiva: a mesma célula pode repetir
        c["linhas"] += 1
    return cel


def _fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _dica(status: str, nossa: Optional[Dict[str, Any]], mapa: Optional[Dict[str, Any]], diff: float) -> str:
    itens = (nossa or {}).get("itens") or []
    nomes = ", ".join(sorted({str(i.get("produto") or i.get("id")) for i in itens})) or "—"
    if status == "faltando_no_mapa":
        return f"O plano enviado tinha {len(itens)} item(ns) aqui ({nomes}) e nada consta no MAPA. Confira o log do job."
    if status == "a_mais_no_mapa":
        return "Consta no MAPA mas não estava no plano enviado (lançamento manual, de outro registro ou de outro período?)."
    if status == "nome_nosso_sem_par":
        return ("A descrição não aparece em nenhuma linha do relatório: o nome pode ser outro no MAPA ou o produto "
                "não foi lançado em nenhum estado.")
    if status == "nome_mapa_sem_par":
        return ("A descrição não existe no que foi enviado: nome diferente do cadastrado ou produto lançado fora do sistema.")
    if status == "divergente" and nossa and mapa:
        n, m = nossa["qtd"], mapa["qtd"]
        tol = TOL_ARRED_POR_ITEM * max(len(itens), 1) + EPS
        if n > 0 and abs(m - 2 * n) <= tol:
            return "O MAPA tem o dobro do enviado: possível lançamento duplicado."
        alvo = abs(diff)
        for tam in (1, 2, 3):
            for comb in itertools.combinations(itens[:12], tam):
                if abs(sum(i["quantidade"] for i in comb) - alvo) <= tol:
                    quem = ", ".join(str(i.get("produto") or i.get("id")) for i in comb)
                    return (f"A diferença ({_fmt(alvo)}) é igual à quantidade de: {quem}. "
                            + ("Esse item parece não ter sido gravado no MAPA." if diff < 0 else "Possível duplicidade."))
    return ""


def comparar(nossas: Dict, mapa: Dict) -> Dict[str, Any]:
    """Compara célula a célula e deriva os totais por UF, por descrição e o geral."""
    desc_nossas = {k[0] for k in nossas}
    desc_mapa = {k[0] for k in mapa}
    celulas: List[Dict[str, Any]] = []
    for k in sorted(set(nossas) | set(mapa), key=lambda k: (k[1], k[0])):
        n, m = nossas.get(k), mapa.get(k)
        qn, qm = round(n["qtd"], 2) if n else 0.0, round(m["qtd"], 2) if m else 0.0
        diff = round(qm - qn, 2)
        if n and m:
            if abs(diff) <= EPS:
                status = "ok"
            elif abs(diff) <= TOL_ARRED_POR_ITEM * len(n["itens"]) + EPS:
                status = "arredondamento"
            else:
                status = "divergente"
        elif n:
            status = "faltando_no_mapa" if k[0] in desc_mapa else "nome_nosso_sem_par"
        else:
            status = "a_mais_no_mapa" if k[0] in desc_nossas else "nome_mapa_sem_par"
        celulas.append({
            "descricao": (n or m)["descricao"], "uf": k[1], "nosso": qn, "mapa": qm, "diff": diff, "status": status,
            "rotulo": STATUS_ROTULO[status], "dica": _dica(status, n, m, diff),
            "itens": (n or {}).get("itens", []), "linhas_mapa": (m or {}).get("linhas", 0),
        })

    def derivar(chave: str) -> List[Dict[str, Any]]:
        grupos: Dict[str, Dict[str, Any]] = {}
        for c in celulas:
            g = grupos.setdefault(c[chave], {chave: c[chave], "nosso": 0.0, "mapa": 0.0, "celulas": 0, "divergentes": 0})
            g["nosso"] += c["nosso"]
            g["mapa"] += c["mapa"]
            g["celulas"] += 1
            g["divergentes"] += 0 if c["status"] in STATUS_OK else 1
        out = []
        for g in grupos.values():
            g["nosso"], g["mapa"] = round(g["nosso"], 2), round(g["mapa"], 2)
            g["diff"] = round(g["mapa"] - g["nosso"], 2)
            out.append(g)
        return sorted(out, key=lambda g: (-abs(g["diff"]), str(g[chave]).lower()))

    por_uf = derivar("uf")
    por_produto = derivar("descricao")
    cont: Dict[str, int] = {}
    for c in celulas:
        cont[c["status"]] = cont.get(c["status"], 0) + 1
    nosso_total = round(sum(c["nosso"] for c in celulas), 2)
    mapa_total = round(sum(c["mapa"] for c in celulas), 2)
    nas_duas = sum(1 for c in celulas if c["itens"] and c["linhas_mapa"])
    divergentes = [c for c in celulas if c["status"] not in STATUS_OK]
    return {
        "status": "OK" if not divergentes else "DIVERGENTE",
        "celulas": celulas, "por_uf": por_uf, "por_produto": por_produto, "contagem": cont,
        "totais": {"nosso": nosso_total, "mapa": mapa_total, "diff": round(mapa_total - nosso_total, 2),
                   "celulas": len(celulas), "divergentes": len(divergentes), "nas_duas": nas_duas,
                   "so_nosso": sum(1 for c in celulas if c["itens"] and not c["linhas_mapa"]),
                   "so_mapa": sum(1 for c in celulas if not c["itens"] and c["linhas_mapa"])},
    }


def verificar(itens_enviados: List[Dict[str, Any]], linhas_mapa: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Atalho: itens enviados × linhas do relatório -> resultado de `comparar` + avisos de sanidade."""
    res = comparar(celulas_nossas(itens_enviados), celulas_mapa(linhas_mapa))
    t = res["totais"]
    avisos = []
    if t["celulas"] > 5 and t["nas_duas"] / t["celulas"] < 0.5:
        avisos.append("Menos da metade das células existe nos dois lados: este relatório parece ser de OUTRO período "
                      "ou de outro registro.")
    res["avisos"] = avisos
    return res
