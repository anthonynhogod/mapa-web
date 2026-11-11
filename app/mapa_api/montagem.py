"""Ponte entre o sistema (registros, credenciais, De->Para) e o webservice do MAPA."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from flask import current_app

from app.logic.comercializacao import build_plano, tipo_config
from app.logic.constantes import ColetorPendencias
from app.logic.preview_utils import build_legacy_structure_from_new, merge_diagnostics
from app.models import MapaCredencial

from . import payloads
from .client import MapaApiClient


def usa_api() -> bool:
    return (current_app.config.get("EXEC_BACKEND") or "api").lower() != "browser"


def credencial_do_usuario(user_id: int, especie: Optional[str] = None) -> Optional[MapaCredencial]:
    q = MapaCredencial.query.filter_by(owner_user_id=user_id)
    return (q.filter_by(especie=especie).first() if especie else None) or q.first()


def cliente(cred: MapaCredencial) -> MapaApiClient:
    c = current_app.config
    return MapaApiClient(
        cred.usuario_app, cred.senha,
        ambiente=c.get("MAPA_API_AMBIENTE", "homologacao"), url=c.get("MAPA_API_URL"),
        md5=c.get("MAPA_API_MD5", True), timeout=c.get("MAPA_API_TIMEOUT", 60.0),
    )


def _estab(cred: Optional[MapaCredencial], coletor: Optional[ColetorPendencias]):
    """Estabelecimento da credencial; faltas viram pendencias 'credencial' (ou levantam)."""
    if cred is None:
        if coletor is None:
            raise payloads.DadosIncompletos(["credenciais MAPA não cadastradas"])
        coletor.registrar("credencial", "credenciais MAPA não cadastradas")
        return None
    try:
        return payloads.estabelecimento(cred)
    except payloads.DadosIncompletos as e:
        if coletor is None:
            raise
        for f in e.faltando:
            coletor.registrar("credencial", f)
        return None


def preparar_abate(registro, gta_records, dif_records, sif_records,
                   coletor: Optional[ColetorPendencias] = None) -> Dict[str, Any]:
    cred = credencial_do_usuario(registro.user_id, registro.especie)
    estab = _estab(cred, coletor)
    merged = merge_diagnostics(dif_records, sif_records)
    estrutura = build_legacy_structure_from_new(gta_records, merged, uf_index_default=23)
    fmt = current_app.config.get("MAPA_API_DATA_ABATE", "br")
    linhas = payloads.montar_abate(estab or {}, registro.data, estrutura, registro.especie, coletor, fmt)
    if estab is None:
        linhas = []
    return {
        "servico": "abate", "metodo": "PUT" if registro.api_id else "POST", "payload": linhas,
        "avisos": payloads.avisos_abate(linhas),
    }


def preparar_comercializacao(registro, records, tipo, coletor: Optional[ColetorPendencias] = None) -> Dict[str, Any]:
    cred = credencial_do_usuario(registro.user_id)
    estab = _estab(cred, coletor)
    plano = build_plano(records, coletor, backend="api")
    cfg = tipo_config(tipo)
    cfg["api_produto_tipo"] = tipo.api_produto_tipo
    cfg["api_tipo"], cfg["api_nacional"], cfg["api_tipo_operador"] = tipo.api_tipo, tipo.api_nacional, tipo.api_tipo_operador
    fmt = current_app.config.get("MAPA_API_DATA_COMERCIALIZACAO", "iso")
    payload = None
    if estab is not None:
        payload = payloads.montar_comercializacao(estab, registro.periodo_ini, registro.periodo_fim, plano, cfg, fmt)
    return {
        "servico": "comercializacao", "metodo": "PUT" if registro.api_id else "POST", "payload": payload,
        "plano": plano, "avisos": [],
    }


def descricao_requisicao(prep: Dict[str, Any]) -> str:
    return f"{prep['metodo']} /{prep['servico']}"


def json_legivel(payload: Any) -> List[str]:
    return json.dumps(payload, ensure_ascii=False, indent=2).splitlines()
