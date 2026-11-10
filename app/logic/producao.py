"""Plano e comandos do Mapa de PRODUCAO.

Fluxo (espelha o da comercializacao): planilha validada -> plano (aplicando o De->Para do banco, sem
fallback) -> lista de comandos JS -> ExecJob executado pelo worker no navegador.

Diferenca para a comercializacao: o mapa e MENSAL e nasce no proprio formulario de inclusao. Nada e gravado
no portal ate o ultimo comando (`finalizarRegistroProducao`), entao um job que falha no meio nao deixa
registro pela metade e pode ser reexecutado do zero.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.logic.constantes import ColetorPendencias, ConstanteNaoMapeada, resolve_produto_producao
from app.logic.js_literal import js_num, js_str
from app.utils.format import normalize_str

ABRIR_CMD = "abrirInclusaoProducao"
ITEM_CMD = "incluirProdutoProducao"
FINALIZAR_CMD = "finalizarRegistroProducao"


def build_plano(records: List[Dict[str, Any]], coletor: Optional[ColetorPendencias] = None) -> List[Dict[str, Any]]:
    """
    [{"id", "descricao", "quantidade", "produtos": [{"produto", "quantidade"}]}]  (um item por produto do portal)

    Produtos da planilha que apontam para o MESMO id do portal viram UMA linha (quantidades somadas): evita
    lancar o mesmo produto padronizado duas vezes no mapa. `produtos` guarda a origem de cada parcela.
    Termo sem vinculo (ou vinculo sem id do portal) vira pendencia (com coletor) ou ConstanteNaoMapeada.
    """
    por_id: Dict[int, Dict[str, Any]] = {}
    for r in sorted(records or [], key=lambda r: normalize_str(str(r.get("produto", "")))):
        prod = resolve_produto_producao(r.get("produto", ""), coletor)
        if prod is None:
            continue                                    # pendencia ja registrada; nada vai ao portal
        if prod.get("id") is None:
            if coletor is None:
                raise ConstanteNaoMapeada("produto_producao", r.get("produto", ""))
            coletor.registrar("produto_producao", r.get("produto", ""))
            continue
        qtd = round(float(r.get("quantidade") or 0), 2)
        if qtd <= 0:
            continue
        item = por_id.setdefault(prod["id"], {
            "id": prod["id"], "descricao": prod["descricao"], "quantidade": 0.0, "produtos": [],
        })
        item["quantidade"] = round(item["quantidade"] + qtd, 2)
        item["produtos"].append({"produto": r.get("produto"), "quantidade": qtd})
    return sorted(por_id.values(), key=lambda i: (normalize_str(i["descricao"]), i["id"]))


def build_commands(plano: List[Dict[str, Any]], *, competencia: str, numero_sif: str) -> List[str]:
    """
    `competencia`: 'MM/AAAA'. Ordem: abre a inclusao e preenche o cabecalho (estabelecimento, periodo,
    producao=Sim) -> um comando por produto -> confere a tabela e salva.
    """
    cmds = [f"{ABRIR_CMD}({js_str(competencia)}, {js_str(numero_sif)})"]
    for it in plano:
        cmds.append(f"{ITEM_CMD}({js_str(it['descricao'])}, {js_num(it['id'])}, {js_num(it['quantidade'])})")
    cmds.append(f"{FINALIZAR_CMD}({js_str(competencia)}, {js_num(len(plano))})")
    return cmds


def totais(plano: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "itens": len(plano),
        "produtos": sum(len(i["produtos"]) for i in plano),
        "quantidade": round(sum(i["quantidade"] for i in plano), 2),
    }
