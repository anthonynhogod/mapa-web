# app/logic/preview_utils.py
from collections import defaultdict
from app.utils.format import normalize_str

def normalize_gta_manual(gtas):
    """
    Converte objetos GtaTemp (ou similares) para a mesma estrutura do payload de upload.
    """
    recs = []
    for g in gtas:
        recs.append({
            "data": getattr(g, "data", None),
            "numero_gta": g.numero,
            "serie": g.serie,
            "machos": g.machos,
            "femeas": g.femeas,
            "total": int((g.machos or 0) + (g.femeas or 0)),
            "lote": g.lote,
            "peso_medio": g.peso,
        })
    return recs

def lotes_from(records):
    lotes = set()
    for r in records or []:
        try:
            if r.get("lote") is not None:
                lotes.add(int(r["lote"]))
        except Exception:
            continue
    return sorted(lotes)


def _canon_diag(nome: str) -> str:
    """
    Chave de agrupamento do diagnóstico.

    As variantes de grafia deixaram de ser um dicionário hardcoded: agora vêm
    dos aliases cadastrados em CONSTANTES (tabela diagnostico_alias).
    """
    from app.logic.constantes import canonicalizar_diagnostico
    return canonicalizar_diagnostico(nome)

def merge_diagnostics(dif_records, sif_records):
    """
    Une DIF + SIF por lote/diagnóstico/parte/destino, somando quantidades.
    Adaptado do padrão do Registro._mesclar_lotes (estrutura final por lote).  # (turn11search2)
    """
    from collections import defaultdict
    resultado = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    def add(records):
        for r in records or []:
            lote = int(r.get("lote", 0) or 0)
            if lote <= 0:
                continue
            nome_diag = _canon_diag(r.get("descricao", ""))
            emergencia = int(r.get("emergencia", 0) or 0)
            parte = str(r.get("parte afetada") or r.get("parte") or "").strip().title()
            destino = str(r.get("destino", "")).strip().title()
            qtd = int(r.get("quantidade", 0) or 0)
            if not (nome_diag and parte and destino and qtd > 0):
                continue
            chave_diag = (nome_diag, emergencia)
            resultado[lote][chave_diag][(parte, destino)] += qtd

    add(dif_records)
    add(sif_records)


    # estrutura final: lista por lote, com diagnósticos e partes
    estrutura = []
    for lote, diagnos in resultado.items():
        diag_list = []
        for (nome_diag, emergencia), partes_dest in diagnos.items():
            partes_dict = defaultdict(lambda: defaultdict(int))
            total_q = 0
            for (parte, destino), qtd in partes_dest.items():
                partes_dict[parte][destino] += qtd
                total_q += qtd
            partes_list = []
            for parte, destinos in partes_dict.items():
                destinos_list = [{"Destino": d, "Quantidade": q} for d, q in destinos.items()]
                partes_list.append({"Parte": parte, "Destinos": destinos_list})
            diag_list.append({
                "Diagnostico": nome_diag,
                "Quantidade": total_q,
                "Emergencia": emergencia,
                "Partes": partes_list,
            })
        estrutura.append({"Lote": lote, "Diagnosticos": diag_list})
    return estrutura

from typing import List, Dict, Any

# As tabelas de descrições/partes/destinos saíram daqui: agora vivem no banco
# e são administradas em Admin > CONSTANTES. Use app.logic.constantes.
#   resolve_diagnostico(nome, coletor) -> descrição oficial do MAPA
#   resolve_parte(nome, coletor)       -> id_mapa da parte afetada
#   resolve_destino(nome, coletor)     -> id_mapa do destino
# Nenhuma delas tem fallback: sem vínculo cadastrado, vira pendência.

def build_legacy_structure_from_new(
    gta_records: List[Dict[str, Any]],
    merged_diag: List[Dict[str, Any]],
    *,
    uf_index_default: int = 23
) -> List[List[Dict[str, Any]]]:
    """
    Constrói a ESTRUTURA ANTIGA: lista de LOTES; cada lote é uma lista de GTAs,
    e cada GTA contém "GTA": {...} + "Tipos": [ {"Tipo": {...}, "Diagnosticos": [...]}, ... ].
    Regras:
      - Apenas o PRIMEIRO GTA do lote carrega os "Diagnosticos" (3, 1 e 2);
        os GTAs subsequentes têm só um Tipo 3 (sem "Diagnosticos").
      - Classificação de tipos:
          tipoIndex=2 ⟵ "MORTO (NO TRANSPORTE)" e "MORTO NO PRÉ ABATE"
          tipoIndex=1 ⟵ Emergiância == 1
          tipoIndex=3 ⟵ demais
      - Distribuição de capacidade (só no 1º GTA do lote):
          * reserva para tipo 2 (mnt/pre-abate), depois tipo 1 (emerg.), e o restante no tipo 3.
      - indexGta é GLOBAL e segue a ordem dos GTAs.
      - Pesos: peso = quantidade_por_sexo * peso_medio (2 casas decimais).
    """
    from app.utils.format import normalize_str

    def _round2(x: float) -> float:
        try:
            return round(float(x), 2)
        except Exception:
            return 0.0

    def _is_tipo2(nome: str) -> bool:
        # Normaliza: “morto (no transporte)” ou “morto no pré abate”
        n = normalize_str(nome or "")
        # transporte
        if ("morto" in n) and ("transporte" in n):
            return True
        # pré abate (sem acento -> 'pre abate')
        if ("morto" in n) and ("pre" in n) and ("abate" in n):
            return True
        return False

    def _classify_tipo(diag: Dict[str, Any]) -> int:
        nome = diag.get("Diagnostico", "") or ""
        if _is_tipo2(nome):
            return 2
        em = int(diag.get("Emergencia", 0) or 0)
        if em == 1:
            return 1
        return 3

    def _sum_quant(diags: List[Dict[str, Any]]) -> int:
        total = 0
        for d in diags or []:
            try:
                total += int(d.get("Quantidade", 0) or 0)
            except Exception:
                continue
        return total

    # Indexa GTAs por lote, preservando ordem
    lotes_gta: Dict[int, List[Dict[str, Any]]] = {}
    for g in gta_records or []:
        try:
            lote = int(g.get("lote", 0) or 0)
        except Exception:
            lote = 0
        if lote > 0:
            lotes_gta.setdefault(lote, []).append(g)

    # Indexa diagnósticos por lote e separa por tipoIndex
    diag_por_lote: Dict[int, Dict[int, List[Dict[str, Any]]]] = {}
    lotes_ordem = []
    for bloco in merged_diag or []:
        lote = int(bloco.get("Lote", 0) or 0)
        if lote <= 0:
            continue
        lotes_ordem.append(lote)
        d_by_tipo = {1: [], 2: [], 3: []}
        for d in (bloco.get("Diagnosticos") or []):
            t = _classify_tipo(d)
            d_by_tipo[t].append(d)
        diag_por_lote[lote] = d_by_tipo

    # Acrescenta lotes que estejam em GTA mas não no merged_diag
    for lote in list(lotes_gta.keys()):
        if lote not in lotes_ordem:
            lotes_ordem.append(lote)

    estrutura: List[List[Dict[str, Any]]] = []
    index_gta_global = 0

    for lote in lotes_ordem:
        gtas = lotes_gta.get(lote, []) or []
        if not gtas:
            continue

        d_by_tipo = diag_por_lote.get(lote, {1: [], 2: [], 3: []})
        diags_t1 = d_by_tipo.get(1, [])
        diags_t2 = d_by_tipo.get(2, [])
        diags_t3 = d_by_tipo.get(3, [])

        # Totais do LOTE para calcular a capacidade do 1º GTA
        tot_t1 = _sum_quant(diags_t1)
        tot_t2 = _sum_quant(diags_t2)

        lote_list: List[Dict[str, Any]] = []

        for idx_in_lote, g in enumerate(gtas):
            n_gta = g.get("numero_gta")
            serie = (g.get("serie") or "").strip()
            machos = int(g.get("machos", 0) or 0)
            femeas = int(g.get("femeas", 0) or 0)
            peso_medio = float(g.get("peso_medio", 0) or 0.0)

            primario = 1 if idx_in_lote == 0 else 0

            gta_block = {
                "GTA": {
                    "ufIndex": uf_index_default,
                    "nGta": n_gta,
                    "Serie": serie,
                    "machos": machos,
                    "femeas": femeas,
                    "Primario": primario,
                    "Prioridade": 1,
                },
                "Tipos": []
            }

            if primario:
                # 1º GTA: reservar capacidade para tipo 2, depois tipo 1, resto no tipo 3
                t2_m = min(tot_t2, machos)
                t2_f = min(max(tot_t2 - t2_m, 0), femeas)

                rem_m = machos - t2_m
                rem_f = femeas - t2_f

                t1_m = min(tot_t1, rem_m)
                t1_f = min(max(tot_t1 - t1_m, 0), rem_f)

                t3_m = max(machos - t2_m - t1_m, 0)
                t3_f = max(femeas - t2_f - t1_f, 0)

                # Ordem dos Tipos (fixa indexTipo nos comandos): 3, 1, 2
                tipo3 = {
                    "Tipo": {
                        "indexGta": index_gta_global,
                        "tipoIndex": 3,
                        "lote": int(lote),
                        "quantMachos": int(t3_m),
                        "pesoMachos": _round2(t3_m * peso_medio),
                        "quantFemeas": int(t3_f),
                        "pesoFemeas": _round2(t3_f * peso_medio),
                        "Prioridade": 2,
                    },
                    "Diagnosticos": diags_t3
                }
                gta_block["Tipos"].append(tipo3)

                if diags_t1:
                    tipo1 = {
                        "Tipo": {
                            "indexGta": index_gta_global,
                            "tipoIndex": 1,
                            "lote": int(lote),
                            "quantMachos": int(t1_m),
                            "pesoMachos": _round2(t1_m * peso_medio),
                            "quantFemeas": int(t1_f),
                            "pesoFemeas": _round2(t1_f * peso_medio),
                            "Prioridade": 2,
                        },
                        "Diagnosticos": diags_t1
                    }
                    gta_block["Tipos"].append(tipo1)

                if diags_t2:
                    tipo2 = {
                        "Tipo": {
                            "indexGta": index_gta_global,
                            "tipoIndex": 2,
                            "lote": int(lote),
                            "quantMachos": int(t2_m),
                            "pesoMachos": _round2(t2_m * peso_medio),
                            "quantFemeas": int(t2_f),
                            "pesoFemeas": _round2(t2_f * peso_medio),
                            "Prioridade": 2,
                        },
                        "Diagnosticos": diags_t2
                    }
                    gta_block["Tipos"].append(tipo2)

            else:
                # Demais GTAs: apenas Tipo 3, 100% da capacidade; sem diagnósticos
                tipo3 = {
                    "Tipo": {
                        "indexGta": index_gta_global,
                        "tipoIndex": 3,
                        "lote": int(lote),
                        "quantMachos": int(machos),
                        "pesoMachos": _round2(machos * peso_medio),
                        "quantFemeas": int(femeas),
                        "pesoFemeas": _round2(femeas * peso_medio),
                        "Prioridade": 5,
                    }
                }
                gta_block["Tipos"].append(tipo3)

            lote_list.append(gta_block)
            index_gta_global += 1

        estrutura.append(lote_list)

    return estrutura


def build_commands(estrutura_lotes: List, coletor=None) -> List[str]:
    """
    Gera os COMANDOS no padrão ANTIGO (idêntico ao set_comandos do Registro.py).

    Regras de paginação:
      - Antes de incluirTipoGta: if indexGta > 9 -> passarPaginaDiagnostico(0)
      - Antes de CADA incluirParte de um diagnóstico: if indexDiagnostico > 9 -> passarPaginaDiagnostico(2)

    Cap por Tipo: quantidade do incluirDiagnostico = min(qtd_diag, max(quantMachos, quantFemeas))

    Resolução de constantes (diagnóstico/parte/destino) vem do banco e NÃO tem
    fallback. Passando um ColetorPendencias, os termos sem vínculo são
    acumulados e os comandos correspondentes são omitidos — cabe ao chamador
    barrar a validação. Sem coletor, o primeiro termo sem vínculo levanta
    ConstanteNaoMapeada.
    """
    from app.logic.constantes import (
        resolve_diagnostico, resolve_parte, resolve_destino,
    )
    from app.logic.js_literal import js_str

    comandos: List[str] = []

    for lote_gta_list in (estrutura_lotes or []):
        if not lote_gta_list:
            continue

        for gta_block in lote_gta_list:
            g = gta_block.get("GTA", {}) or {}
            uf_index = int(g.get("ufIndex") or 23)
            n_gta = g.get("nGta")
            serie = (g.get("Serie") or "").strip()
            machos = int(g.get("machos") or 0)
            femeas = int(g.get("femeas") or 0)

            # 1) GTA
            comandos.append(f"incluirGta({uf_index}, {n_gta}, {js_str(serie)}, {machos}, {femeas})")

            # 2) Tipos
            for index_tipo, tipo_pack in enumerate(gta_block.get("Tipos", []) or []):
                t = tipo_pack.get("Tipo", {}) or {}
                index_gta = int(t.get("indexGta") or 0)
                tipo_index = int(t.get("tipoIndex") or 3)
                lote = int(t.get("lote") or 0)
                q_m = int(t.get("quantMachos") or 0)
                p_m = float(t.get("pesoMachos") or 0.0)
                q_f = int(t.get("quantFemeas") or 0)
                p_f = float(t.get("pesoFemeas") or 0.0)

                if index_gta > 9:
                    comandos.append("passarPaginaDiagnostico(0)")

                comandos.append(
                    f"incluirTipoGta({index_gta}, {tipo_index}, {lote}, "
                    f"{q_m}, {p_m}, {q_f}, {p_f})"
                )

                diagnos = tipo_pack.get("Diagnosticos") or []
                if not diagnos:
                    continue

                # cap para este tipo
                cap = max(q_m, q_f)

                for index_diagnostico, d in enumerate(diagnos):
                    nome_bruto = d.get("Diagnostico", "") or ""
                    nome_final = resolve_diagnostico(nome_bruto, coletor)
                    qtd_diag = int(d.get("Quantidade", 0) or 0)
                    qtd_usada = min(qtd_diag, cap) if cap else qtd_diag

                    # Sem vínculo: não emite nada para este diagnóstico nem para
                    # suas partes. O coletor já registrou a pendência.
                    if nome_final is None:
                        for parte in (d.get("Partes") or []):
                            resolve_parte(parte.get("Parte", "") or "", coletor)
                            for dest in (parte.get("Destinos") or []):
                                resolve_destino(dest.get("Destino", "") or "", coletor)
                        continue

                    comandos.append(f"incluirDiagnostico({index_tipo}, {js_str(nome_final)}, {qtd_usada})")

                    for parte in (d.get("Partes") or []):
                        parte_nome = parte.get("Parte", "") or ""
                        pid = resolve_parte(parte_nome, coletor)

                        for dest in (parte.get("Destinos") or []):
                            dest_nome = dest.get("Destino", "") or ""
                            did = resolve_destino(dest_nome, coletor)
                            q = int(dest.get("Quantidade", 0) or 0)

                            # Um id ausente nunca vira 0: o comando é omitido e
                            # a pendência barra a validação lá na frente.
                            if pid is None or did is None:
                                continue

                            if index_diagnostico > 9:
                                comandos.append("passarPaginaDiagnostico(2)")

                            comandos.append(f"incluirParte({index_diagnostico}, {pid}, {did}, {q})")

    comandos.append("finalizarRegistro()")

    return comandos
