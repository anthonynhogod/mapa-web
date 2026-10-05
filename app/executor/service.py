# app/executor/service.py
from pathlib import Path
from datetime import datetime
from flask import current_app
from app import db
from app.models import ExecJob
from app.logic.preview_utils import merge_diagnostics, build_commands, build_legacy_structure_from_new

def cleanup_tmp_folder(registro_id: int):
    base = Path(current_app.config.get("UPLOAD_TMP_DIR", "uploads/tmp"))
    target = base / str(registro_id)
    if not target.exists():
        return
    # remove arquivos
    for p in target.rglob("*"):
        try:
            if p.is_file():
                p.unlink()
        except Exception:
            pass
    # remove dirs (de baixo pra cima)
    for p in sorted(target.rglob("*"), reverse=True):
        try:
            if p.is_dir():
                p.rmdir()
        except Exception:
            pass
    try:
        target.rmdir()
    except Exception:
        pass

def create_api_job(registro, prep: dict, totais, gta_source: str = "api", extra_meta: dict | None = None) -> ExecJob:
    """Job de envio via webservice: o corpo JSON ja montado vai em meta.payload."""
    from app.mapa_api.montagem import descricao_requisicao
    job = ExecJob(
        registro_id=registro.id,
        owner_user_id=registro.user_id,
        gta_source=gta_source,
        commands=[descricao_requisicao(prep)],
        meta={"backend": "api", "servico": prep["servico"], "payload": prep["payload"], "totais": totais,
              **(extra_meta or {})},
        status="ESPERA", progress=0, errors=[],
    )
    db.session.add(job)
    registro.status = "PT"
    db.session.commit()
    cleanup_tmp_folder(registro.id)
    return job


def create_job_and_finalize(registro, gta_source: str, gta_records, dif_records, sif_records, totais) -> ExecJob:
    """
    - Gera comandos a partir dos datasets (sem revalidar nada — isso já foi feito no preview)
    - Cria ExecJob (ESPERA)
    - Seta registro.status = 'PT'
    - Limpa uploads/tmp/<registro_id>
    """
    # 1) Gera plano de comandos
    # Sem coletor de propósito: aqui qualquer constante sem vínculo levanta
    # ConstanteNaoMapeada e aborta a criação do job. É a última trava — o
    # preview já deveria ter barrado, mas nenhum comando pode ir ao portal
    # com diagnóstico cru ou id 0.
    from app.mapa_api.montagem import preparar_abate, usa_api
    if usa_api():
        # sem coletor: qualquer constante/credencial pendente levanta (ultima trava)
        prep = preparar_abate(registro, gta_records, dif_records, sif_records)
        return create_api_job(registro, prep, totais, gta_source)

    merged_diag = merge_diagnostics(dif_records, sif_records)
    estrutura_lotes = build_legacy_structure_from_new(gta_records, merged_diag, uf_index_default=23)
    comandos = build_commands(estrutura_lotes)
    # 2) Cria job
    job = ExecJob(
        registro_id=registro.id,
        owner_user_id=registro.user_id,
        gta_source=gta_source,
        commands=comandos,
        meta={"totais": totais},
        status="ESPERA",
        progress=0,
        errors=[],
        started_at=None,
        finished_at=None,
    )
    db.session.add(job)

    # 3) Trava o registro (PT) e limpa tmp
    registro.status = "PT"
    db.session.commit()  # commit antes da limpeza de disco (seguro)

    cleanup_tmp_folder(registro.id)
    return job