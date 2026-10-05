"""Plano e comandos do Mapa de Comercializacao.

Fluxo (espelha o do abate): planilha validada -> plano por UF (aplicando o De->Para
do banco, sem fallback) -> lista de comandos JS -> ExecJob executado pelo worker.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.logic.constantes import (
    ColetorPendencias, ConstanteNaoMapeada, resolve_estado_venda, resolve_produto_venda,
)
from app.logic.js_literal import js_json, js_num, js_str

# Primeiro comando de todo job. Por padrao ABORTA se o registro do periodo ja tiver
# transacoes do MESMO tipo de lancamento (venda/recebimento/expedicao dividem o registro);
# no "limpar e relancar" ele e trocado por limparTransacoes(...) com os mesmos argumentos.
VERIFICAR_FN = "verificarRegistroVazio"
LIMPAR_FN = "limparTransacoes"
FINALIZAR_CMD = "finalizarRegistroComercializacao()"


def para_limpar(cmd: str) -> str:
    """verificarRegistroVazio(...) -> limparTransacoes(...) (mesmos argumentos)."""
    if not cmd.startswith(VERIFICAR_FN + "("):
        raise ValueError("comando inicial inesperado")
    return LIMPAR_FN + cmd[len(VERIFICAR_FN):]


def build_plano(records: List[Dict[str, Any]], coletor: Optional[ColetorPendencias] = None,
                backend: str = "api") -> List[Dict[str, Any]]:
    """
    [{"uf": "RS", "index": 23, "itens": [{"produto", "descricao", "id", "quantidade"}]}]

    Ordenado por UF; dentro da UF, por produto. Produtos diferentes que apontam para o
    mesmo id do portal sao lancados separados (o MAPA aceita). Termo sem vinculo vira
    pendencia (com coletor) ou ConstanteNaoMapeada (sem coletor).

    `backend="api"` exige o cod_produto do webservice; "browser" (legado) exige o id do portal.
    """
    por_uf: Dict[str, Dict[str, Any]] = {}
    for r in sorted(records or [], key=lambda r: (str(r.get("uf", "")).upper(), str(r.get("produto", "")))):
        est = resolve_estado_venda(r.get("uf", ""), coletor)
        prod = resolve_produto_venda(r.get("produto", ""), coletor)
        if est is None or prod is None:
            continue  # pendencia ja registrada; nada vai ao portal
        if backend == "api" and prod.get("cod_api") is None:
            if coletor is None:
                raise ConstanteNaoMapeada("produto_api", r.get("produto", ""))
            coletor.registrar("produto_api", r.get("produto", ""))
            continue
        if backend != "api" and prod.get("id") is None:
            if coletor is None:
                raise ConstanteNaoMapeada("produto_venda", r.get("produto", ""))
            coletor.registrar("produto_venda", r.get("produto", ""))
            continue
        qtd = round(float(r.get("quantidade") or 0), 2)
        if qtd <= 0:
            continue
        bloco = por_uf.setdefault(est["uf"], {"uf": est["uf"], "index": est["index"], "itens": []})
        bloco["itens"].append({
            "produto": r.get("produto"),
            "descricao": prod["descricao"],
            "id": prod["id"],
            "cod_api": prod.get("cod_api"),
            "quantidade": qtd,
        })
    return sorted(por_uf.values(), key=lambda b: b["uf"])


def build_commands(plano: List[Dict[str, Any]], *, tipo: Dict[str, Any], rotulos: Optional[List[str]] = None,
                   limpar: bool = False) -> List[str]:
    """
    `tipo`: {"rotulo", "tipo_transacao_idx", "ambito_idx", "operador_idx"} do TipoLancamento.
    `rotulos`: rotulos de TODOS os tipos ativos (o JS os usa p/ distinguir as linhas da tabela
    de transacoes e abortar/limpar so o que e do mesmo tipo).
    """
    rotulo = tipo["rotulo"]
    todos = sorted(set((rotulos or []) + [rotulo]))
    cfg = {
        "tipo": int(tipo["tipo_transacao_idx"]),
        "ambito": int(tipo["ambito_idx"]),
        "operador": int(tipo["operador_idx"]),
        "rotulo": rotulo,
    }
    cmds: List[str] = [f"{LIMPAR_FN if limpar else VERIFICAR_FN}({js_str(rotulo)}, {js_json(todos)})"]
    for pos, bloco in enumerate(plano):
        cmds.append(f"incluirEstadoVenda({js_str(bloco['uf'])}, {js_num(bloco['index'])}, {js_json(cfg)})")
        for it in bloco["itens"]:
            cmds.append(
                "incluirProdutoVenda("
                f"{js_str(bloco['uf'])}, {js_num(pos)}, {js_num(it['quantidade'])}, "
                f"{js_str(it['descricao'])}, {js_num(it['id'])}, {js_str(rotulo)})"
            )
    cmds.append(FINALIZAR_CMD)
    return cmds


def totais(plano: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "ufs": len(plano),
        "itens": sum(len(b["itens"]) for b in plano),
        "quantidade": round(sum(i["quantidade"] for b in plano for i in b["itens"]), 2),
    }


def tipo_config(t) -> Dict[str, Any]:
    """TipoLancamento (ORM) -> dict usado por build_commands."""
    return {
        "codigo": t.codigo, "nome": t.nome, "rotulo": t.rotulo_portal,
        "tipo_transacao_idx": t.tipo_transacao_idx, "ambito_idx": t.ambito_idx,
        "operador_idx": t.operador_idx,
    }


def rotulos_ativos() -> List[str]:
    from app.models import TipoLancamento
    return [t.rotulo_portal for t in TipoLancamento.query.filter_by(ativo=True).all()]
