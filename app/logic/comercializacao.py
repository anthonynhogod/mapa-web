"""Plano e comandos do Mapa de Comercializacao.

Fluxo (espelha o do abate): planilha validada -> plano por UF (aplicando o De->Para
do banco, sem fallback) -> lista de comandos JS -> ExecJob executado pelo worker.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.logic.constantes import (
    ColetorPendencias, resolve_estado_venda, resolve_produto_venda,
)
from app.logic.js_literal import js_num, js_str

# Primeiro comando de todo job de comercializacao. Por padrao ABORTA se o registro
# do periodo ja tiver transacoes (evita UF duplicada); no "limpar e relancar" ele
# e trocado por LIMPAR_CMD.
VERIFICAR_CMD = "verificarRegistroVazio()"
LIMPAR_CMD = "limparTransacoes()"
FINALIZAR_CMD = "finalizarRegistroComercializacao()"


def build_plano(records: List[Dict[str, Any]], coletor: Optional[ColetorPendencias] = None) -> List[Dict[str, Any]]:
    """
    [{"uf": "RS", "index": 23, "itens": [{"produto", "descricao", "id", "quantidade"}]}]

    Ordenado por UF; dentro da UF, por produto. Produtos diferentes que apontam para o
    mesmo id do portal sao lancados separados (o MAPA aceita). Termo sem vinculo vira
    pendencia (com coletor) ou ConstanteNaoMapeada (sem coletor).
    """
    por_uf: Dict[str, Dict[str, Any]] = {}
    for r in sorted(records or [], key=lambda r: (str(r.get("uf", "")).upper(), str(r.get("produto", "")))):
        est = resolve_estado_venda(r.get("uf", ""), coletor)
        prod = resolve_produto_venda(r.get("produto", ""), coletor)
        if est is None or prod is None:
            continue  # pendencia ja registrada; nada vai ao portal
        qtd = round(float(r.get("quantidade") or 0), 2)
        if qtd <= 0:
            continue
        bloco = por_uf.setdefault(est["uf"], {"uf": est["uf"], "index": est["index"], "itens": []})
        bloco["itens"].append({
            "produto": r.get("produto"),
            "descricao": prod["descricao"],
            "id": prod["id"],
            "quantidade": qtd,
        })
    return sorted(por_uf.values(), key=lambda b: b["uf"])


def build_commands(plano: List[Dict[str, Any]], *, limpar: bool = False) -> List[str]:
    cmds: List[str] = [LIMPAR_CMD if limpar else VERIFICAR_CMD]
    for pos, bloco in enumerate(plano):
        cmds.append(f"incluirEstadoVenda({js_str(bloco['uf'])}, {js_num(bloco['index'])})")
        for it in bloco["itens"]:
            cmds.append(
                "incluirProdutoVenda("
                f"{js_str(bloco['uf'])}, {js_num(pos)}, {js_num(it['quantidade'])}, "
                f"{js_str(it['descricao'])}, {js_num(it['id'])})"
            )
    cmds.append(FINALIZAR_CMD)
    return cmds


def totais(plano: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "ufs": len(plano),
        "itens": sum(len(b["itens"]) for b in plano),
        "quantidade": round(sum(i["quantidade"] for b in plano for i in b["itens"]), 2),
    }
