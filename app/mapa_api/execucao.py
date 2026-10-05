"""Executa um ExecJob via webservice (substitui o Selenium no worker)."""
from __future__ import annotations

import datetime as dt
from typing import Tuple

from flask import current_app

from app.extensions import db
from app.models import ExecJob, MapaCredencial, Registro

from .client import ApiError, extrair_id, resumir_corpo
from .montagem import cliente


def executar_job_api(job: ExecJob, registro: Registro, cred: MapaCredencial) -> Tuple[bool, str]:
    """
    Envia o corpo guardado em job.meta['payload']. POST se o registro ainda nao tem id do MAPA,
    PUT (com o id) se ja tem. Nunca repete sozinho: um POST com resultado desconhecido (timeout)
    e marcado como 'incerto' para o operador conferir antes de reenviar.
    """
    meta = dict(job.meta or {})
    servico, payload = meta.get("servico"), meta.get("payload")
    if not servico or payload is None:
        return False, "Job sem corpo de requisição (meta.servico/meta.payload)."

    cli = cliente(cred)
    meta["ambiente"] = cli.ambiente
    meta["metodo"] = "PUT" if registro.api_id else "POST"
    job.meta = meta
    db.session.commit()

    try:
        resposta = cli.enviar(servico, payload, api_id=registro.api_id)
    except ApiError as e:
        meta["incerto"] = bool(e.status == 0 and meta["metodo"] == "POST")
        meta["resposta_erro"] = resumir_corpo(e.corpo)
        job.meta = meta
        msg = str(e)
        if meta["incerto"]:
            msg += " — o resultado do envio é DESCONHECIDO: confira no portal se o mapa foi gravado antes de reenviar."
        return False, msg

    meta["incerto"] = False
    meta["resposta"] = resposta if isinstance(resposta, (dict, list)) else resumir_corpo(resposta)
    novo_id = extrair_id(resposta)
    if novo_id:
        registro.api_id = novo_id
    job.meta = meta
    return True, f"{meta['metodo']} /{servico}: ok (ambiente {cli.ambiente})"
