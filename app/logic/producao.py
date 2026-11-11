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
    [{"produto", "id", "descricao", "ingrediente", "quantidade"}]  (um item por produto da planilha, ordenado por produto)

    Cada produto da planilha e um lancamento UNICO no portal, mesmo quando varios apontam para o mesmo id
    (ex.: "MIUDOS SALGADOS" e "MIUDOS SALGADOS (MASCARA)"): o Ministerio exige lancamentos separados, entao
    NUNCA se somam quantidades por id. (A planilha ja soma linhas repetidas do MESMO produto.)
    Termo sem vinculo (ou vinculo sem id do portal) vira pendencia (com coletor) ou ConstanteNaoMapeada.
    """
    itens: List[Dict[str, Any]] = []
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
        itens.append({"produto": r.get("produto"), "id": prod["id"], "descricao": prod["descricao"],
                      "ingrediente": prod.get("ingrediente"), "quantidade": qtd})
    return itens


def build_commands(plano: List[Dict[str, Any]], *, competencia: str, numero_sif: str) -> List[str]:
    """
    `competencia`: 'MM/AAAA'. Ordem: abre a inclusao e preenche o cabecalho (estabelecimento, periodo,
    producao=Sim) -> um comando por produto -> confere a tabela e salva.
    """
    cmds = [f"{ABRIR_CMD}({js_str(competencia)}, {js_str(numero_sif)})"]
    for it in plano:
        cmds.append(f"{ITEM_CMD}({js_str(it['descricao'])}, {js_num(it['id'])}, {js_num(it['quantidade'])}, "
                    f"{js_str(it.get('ingrediente') or '')})")
    cmds.append(f"{FINALIZAR_CMD}({js_str(competencia)}, {js_num(len(plano))})")
    return cmds


def totais(plano: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "itens": len(plano),
        "quantidade": round(sum(i["quantidade"] for i in plano), 2),
    }
