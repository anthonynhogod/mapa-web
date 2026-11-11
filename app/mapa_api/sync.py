"""Sincroniza os catalogos do webservice com o De->Para local (preenche os ids da API).

So preenche o que esta VAZIO e casa de forma inequivoca (nome normalizado igual ao nome/apelido
cadastrado); nunca sobrescreve valor informado a mao. O formato de resposta dos catalogos nao tem
exemplo no manual (algumas tabelas listam so nome/situacao), entao a extracao do id e tolerante e,
se nao achar o campo de id, o relatorio mostra uma amostra crua para ajuste.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.extensions import db
from app.logic import constantes
from app.models import (
    Destino, DestinoAlias, Diagnostico, DiagnosticoAlias, EspecieApi, ParteAfetada,
    ParteAfetadaAlias, ProdutoVenda, ProdutoVendaAlias,
)
from app.utils.format import normalize_str

ID_KEYS = ("id", "Id", "ID", "cod_produto", "codigo", "cod", "idDiagnostico", "id_diagnostico",
           "idParteAfetada", "idDestino", "idDestinoCondenacao", "idEspecie")
NOME_KEYS = ("nome", "Nome", "descricao", "produto", "nomeEspecie", "nome_especie", "diagnostico")


def extrair(item: Any) -> Tuple[Optional[int], Optional[str]]:
    if not isinstance(item, dict):
        return None, None
    ident = None
    for k in ID_KEYS:
        v = item.get(k)
        if v is not None and str(v).strip().isdigit():
            ident = int(v)
            break
    nome = next((str(item[k]).strip() for k in NOME_KEYS if item.get(k)), None)
    return ident, nome


def _lista(dados: Any) -> List[Any]:
    if isinstance(dados, list):
        return dados
    if isinstance(dados, dict):
        for v in dados.values():
            if isinstance(v, list):
                return v
    return []


# catalogo -> (modelo, modelo_alias, fk_alias, campo_id, getter do nome "oficial")
ALVOS = {
    "diagnosticos": (Diagnostico, DiagnosticoAlias, "diagnostico_id", "id_api", "diagnostico"),
    "partes": (ParteAfetada, ParteAfetadaAlias, "parte_id", "id_api", "parte"),
    "destinos": (Destino, DestinoAlias, "destino_id", "id_api", "destino"),
    "produtos": (ProdutoVenda, ProdutoVendaAlias, "produto_id", "cod_api", "produto_venda"),
}


def sincronizar(cliente) -> Dict[str, Any]:
    """Busca os catalogos e preenche ids vazios. Devolve relatorio por catalogo."""
    rel: Dict[str, Any] = {}

    # --- especies (chave = nome da especie como usada no sistema: 'suino') ---
    esp = _lista(cliente.catalogo("especies", usar_cache=False))
    r_esp = {"total": len(esp), "preenchidos": [], "sem_correspondencia": [], "amostra": esp[:1]}
    api_por_nome = {normalize_str(n): i for i, n in (extrair(x) for x in esp) if i is not None and n}
    for e in EspecieApi.query.all():
        if e.id_api is not None:
            continue
        # 'suino' tambem casa com 'suinos' / 'suina' etc. (primeiro prefixo unico)
        cands = [i for n, i in api_por_nome.items() if n == normalize_str(e.nome) or n.startswith(normalize_str(e.nome)[:5])]
        if len(set(cands)) == 1:
            e.id_api = cands[0]
            r_esp["preenchidos"].append(f"{e.nome} -> {e.id_api}")
        else:
            r_esp["sem_correspondencia"].append(e.nome)
    rel["especies"] = r_esp

    # --- demais catalogos ---
    for nome_cat, (Model, Alias, fk, campo, tipo_cache) in ALVOS.items():
        dados = _lista(cliente.catalogo(nome_cat, usar_cache=False))
        r = {"total": len(dados), "preenchidos": [], "sem_correspondencia": [], "ja_preenchidos": 0,
             "amostra": dados[:1], "sem_id": False}
        pares = [extrair(x) for x in dados]
        if dados and all(i is None for i, _ in pares):
            r["sem_id"] = True            # catalogo sem campo de id reconhecivel
            rel[nome_cat] = r
            continue
        por_nome: Dict[str, List[int]] = {}
        for ident, nome in pares:
            if ident is not None and nome:
                por_nome.setdefault(normalize_str(nome), []).append(ident)

        usados = set()
        for item in Model.query.filter_by(ativo=True).all():
            if getattr(item, campo) is not None:
                r["ja_preenchidos"] += 1
                continue
            chaves = {a.alias_norm for a in item.aliases}
            achados = {i for k in chaves for i in por_nome.get(k, [])}
            rotulo = getattr(item, "descricao_mapa", None) or item.nome
            if len(achados) == 1:
                ident = achados.pop()
                setattr(item, campo, ident)
                usados.add(ident)
                r["preenchidos"].append(f"{rotulo} -> {ident}")
            else:
                r["sem_correspondencia"].append(f"{rotulo}" + (" (ambíguo)" if len(achados) > 1 else ""))
        rel[nome_cat] = r

    db.session.commit()
    constantes.invalidar_cache()
    return rel
