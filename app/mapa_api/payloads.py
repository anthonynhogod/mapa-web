"""Montagem dos corpos JSON do webservice a partir dos dados ja validados e do De->Para.

Fonte: "PGA_SIGSIF - Manual de utilizacao dos servicos" v1.3 (08/02/2021).
O manual traz o exemplo (swagger) do envio de COMERCIALIZACAO; o do ABATE so descreve os campos
(lista de registros "planos"). Por isso:
  - os montadores sao funcoes puras e isoladas (facil ajustar o formato);
  - o sistema sempre mostra o JSON antes de enviar e o ambiente padrao e HOMOLOGACAO.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from app.logic.constantes import (
    ColetorPendencias, resolve_destino_api, resolve_diagnostico_api, resolve_especie_api,
    resolve_parte_api,
)

# tipoIndex do plano de abate (1=emergencia, 2=morto/necropsia, 3=normal) -> dominio da API
TIPO_LOTE = {1: "EM", 2: "NE", 3: "NO"}
UFS = {"AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE",
       "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"}


class DadosIncompletos(Exception):
    def __init__(self, faltando: List[str]):
        self.faltando = faltando
        super().__init__("Credenciais MAPA incompletas: " + ", ".join(faltando))


def _digitos(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def fmt_data(d: date, formato: str = "iso") -> str:
    """'iso' -> yyyy-mm-dd (swagger da comercializacao); 'br' -> dd/mm/aaaa (tabela de parametros)."""
    return d.strftime("%d/%m/%Y") if formato == "br" else d.isoformat()


def estabelecimento(cred) -> Dict[str, Any]:
    """Estabelecimento (numero SIF, ambito, CPF/CNPJ, UF, IBGE) a partir da MapaCredencial."""
    falta = []
    numero = str(getattr(cred, "numero_sif", "") or "").strip()
    cnpj = _digitos(getattr(cred, "cpf_cnpj", ""))
    ambito = (getattr(cred, "ambito", "") or "").strip().upper()
    uf = (getattr(cred, "cod_uf", "") or "").strip().upper()
    ibge = _digitos(getattr(cred, "cod_municipio_ibge", ""))
    if not numero:
        falta.append("número SIF")
    if len(cnpj) not in (11, 14):
        falta.append("CPF/CNPJ (11 ou 14 dígitos)")
    if ambito not in ("SIF", "ER"):
        falta.append("âmbito (SIF ou ER)")
    if uf not in UFS:
        falta.append("UF do estabelecimento")
    if len(ibge) != 7:
        falta.append("código IBGE do município (7 dígitos)")
    if falta:
        raise DadosIncompletos(falta)
    return {"numero": numero, "ambito": ambito, "cpf_cnpj": cnpj, "cod_uf": uf, "cod_municipio_ibge": ibge}


# ---------------------------------------------------------------------------
# Comercializacao
# ---------------------------------------------------------------------------
def montar_comercializacao(estab: Dict[str, Any], ini: date, fim: date, plano: List[Dict[str, Any]],
                           tipo: Dict[str, Any], formato_data: str = "iso") -> Dict[str, Any]:
    """
    {data_inicio, data_fim, estabelecimento, transacoes:[{tipo, nacional, tipo_operador, cod_uf,
     produtos:[{cod_produto, quantidade, (tipo)}]}]}

    `plano` = app.logic.comercializacao.build_plano(...) (uma transacao por UF). Hoje so o operador
    "UF" tem layout de planilha; os demais (recebimento etc.) levantam NotImplementedError.
    """
    operador = tipo["api_tipo_operador"]
    if operador != "UF":
        raise NotImplementedError(f"Tipo de operador {operador} ainda não tem layout de planilha.")
    transacoes = []
    for bloco in plano:
        produtos = []
        for it in bloco["itens"]:
            p = {"cod_produto": int(it["cod_api"]), "quantidade": float(it["quantidade"])}
            if tipo.get("api_produto_tipo"):
                p["tipo"] = tipo["api_produto_tipo"]
            produtos.append(p)
        transacoes.append({
            "tipo": tipo["api_tipo"],
            "nacional": bool(tipo["api_nacional"]),
            "tipo_operador": operador,
            "cod_uf": bloco["uf"],
            "produtos": produtos,
        })
    return {
        "data_inicio": fmt_data(ini, formato_data),
        "data_fim": fmt_data(fim, formato_data),
        "estabelecimento": estab,
        "transacoes": transacoes,
    }


# ---------------------------------------------------------------------------
# Abate
# ---------------------------------------------------------------------------
def montar_abate(estab: Dict[str, Any], dia: date, estrutura_lotes: Iterable, especie: str,
                 coletor: Optional[ColetorPendencias] = None, formato_data: str = "br") -> List[Dict[str, Any]]:
    """
    Lista de linhas "planas" (uma por GTA x lote x diagnostico x parte x destino; GTA/lote sem
    diagnostico gera uma linha so com os campos do lote), conforme a tabela de parametros do
    "Enviar mapa de abate". `estrutura_lotes` = build_legacy_structure_from_new(...).
    """
    especie_id = resolve_especie_api(especie, coletor)
    base = {**estab, "data_abate": fmt_data(dia, formato_data), "abate": "S"}
    linhas: List[Dict[str, Any]] = []

    for lote_gta_list in estrutura_lotes or []:
        for gta_block in lote_gta_list or []:
            g = gta_block.get("GTA") or {}
            for tipo_pack in gta_block.get("Tipos") or []:
                t = tipo_pack.get("Tipo") or {}
                q_m = int(t.get("quantMachos") or 0)
                q_f = int(t.get("quantFemeas") or 0)
                lote_base = {
                    **base,
                    "nr_gta": g.get("nGta"),
                    "cdSerie": (g.get("Serie") or "").strip().upper(),
                    "especie": especie_id,
                    "quantidadeMachos": q_m,
                    "quantidadeFemeas": q_f,
                    "numeroLote": int(t.get("lote") or 0),
                    "tipoLote": TIPO_LOTE.get(int(t.get("tipoIndex") or 3), "NO"),
                    "pesoMortoMacho": float(t.get("pesoMachos") or 0),
                    "pesoMortoFemea": float(t.get("pesoFemeas") or 0),
                }
                diagnos = tipo_pack.get("Diagnosticos") or []
                if not diagnos:
                    linhas.append(lote_base)
                    continue
                cap = max(q_m, q_f)
                for d in diagnos:
                    did = resolve_diagnostico_api(d.get("Diagnostico", ""), coletor)
                    qtd = int(d.get("Quantidade") or 0)
                    qtd_usada = min(qtd, cap) if cap else qtd
                    for parte in d.get("Partes") or []:
                        pid = resolve_parte_api(parte.get("Parte", ""), coletor)
                        for dest in parte.get("Destinos") or []:
                            dst = resolve_destino_api(dest.get("Destino", ""), coletor)
                            if did is None or pid is None or dst is None:
                                continue          # pendencia registrada: nada e emitido
                            linhas.append({
                                **lote_base,
                                "diagnostico": did,
                                "quantidadeAnimaisAcometidos": qtd_usada,
                                "parteAfetada": pid,
                                "quantidadePartesAfetadas": int(dest.get("Quantidade") or 0),
                                "destinoCondenacao": dst,
                            })
    return linhas


def avisos_abate(linhas: List[Dict[str, Any]]) -> List[str]:
    """Divergencias entre os dados e as regras do manual (o MAPA e quem decide; so avisamos)."""
    avisos = set()
    for l in linhas:
        n = str(l.get("nr_gta") or "")
        if not n.isdigit() or len(n) > 6:
            avisos.add(f"GTA {n!r}: o manual pede número com até 6 dígitos.")
        s = l.get("cdSerie") or ""
        if len(s) != 1 or not s.isalpha():
            avisos.add(f"GTA {n}: série {s!r}; o manual pede uma letra de A a Z.")
        if l.get("quantidadeMachos", 0) < 0 or l.get("quantidadeFemeas", 0) < 0:
            avisos.add(f"GTA {n}: quantidade negativa.")
    return sorted(avisos)
