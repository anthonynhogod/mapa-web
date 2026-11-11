# app/executor/cli.py
import click
from flask import current_app
from flask.cli import with_appcontext
from datetime import datetime, timedelta
import os, socket

from .queue import start_pool  
from app import db
from app.models import ExecJob
from app.executor.worker import shutdown_all_browsers

@click.group("worker")
def worker_cli():
    """Comandos do executor de jobs (fila/pool de threads)."""


@worker_cli.command("run")
@click.option("--threads", default=2, show_default=True, help="Número de workers em paralelo.")
@click.option("--profile", type=click.Choice(["prod", "test", "dev"], case_sensitive=False),
              default=None, help="Perfil do Navegador (prod=testes em headless ultra; test=GUI fast; dev=GUI balanced).")
@click.option("--gui/--headless", default=None, help="Força GUI (SHOW) ou headless (HIDE), sobrescreve o perfil.")
@click.option("--speed", type=click.Choice(["ultra","fast","balanced","safe"], case_sensitive=False),
              default=None, help="Sobrescreve o speed_profile do Navegador.")
@click.option("--tap-ui", type=click.Choice(["never","auto","always"], case_sensitive=False),
              default=None, help="Sobrescreve o tap de UI (coleta de mensagens).")
@with_appcontext
def run(threads: int, profile: str|None, gui: bool|None, speed: str|None, tap_ui: str|None):
    app = current_app._get_current_object()

    # ====== Resolve perfil padrão ======
    prof = (profile or os.getenv("NAV_PROFILE") or "").lower()
    if prof == "prod":
        nav_mode = "HIDE"
        nav_speed = "ultra"
        nav_tap   = "never"
    elif prof == "test":
        nav_mode = "SHOW"
        nav_speed = "fast"
        nav_tap   = "auto"
    elif prof == "dev":
        nav_mode = "SHOW"
        nav_speed = "balanced"
        nav_tap   = "auto"
    else:
        # Fallbacks por env/config quando nenhum perfil foi passado
        nav_mode  = os.getenv("NAV_MODE") or app.config.get("NAV_MODE") or "HIDE"
        nav_speed = os.getenv("NAV_SPEED_PROFILE") or app.config.get("NAV_SPEED_PROFILE") or "balanced"
        nav_tap   = os.getenv("NAV_TAP_UI") or app.config.get("NAV_TAP_UI") or "auto"
    # ====== Overrides por flags ======
    if gui is not None:
        nav_mode = "SHOW" if gui else "HIDE"
    if speed:
        nav_speed = speed.lower()
    if tap_ui:
        nav_tap = tap_ui.lower()

    # ====== Persiste em app.config (lido pelos workers) ======
    app.config["NAV_MODE"] = nav_mode
    app.config["NAV_SPEED_PROFILE"] = nav_speed
    app.config["NAV_TAP_UI"] = nav_tap

    click.echo(f"Navegador: mode={nav_mode} speed_profile={nav_speed} tap_ui={nav_tap} (profile={prof or 'custom'})")

    # Inicia o pool
    start_pool(app, num_workers=threads)
    click.echo(f"Workers iniciados: {threads}")
    
    try:
        while True:
            click.echo("Executor ativo... (Ctrl+C para encerrar)")
            import time; time.sleep(60)
    except KeyboardInterrupt:
        click.echo("Encerrando executor...")
        # marca jobs EXECUTANDO deste processo como FALHOU
        host = socket.gethostname()
        pid = os.getpid()
        _mark_running_of_this_process_as_failed(host, pid, reason="Executor interrompido manualmente")
        click.echo("Jobs EXECUTANDO deste processo marcados como FALHOU.")
        # <-- NOVO: fecha navegadores vivos deste processo
        try:
            shutdown_all_browsers()
            click.echo("Navegadores ativos foram encerrados.")
        except Exception:
            pass


def init_app(app):
    app.cli.add_command(worker_cli)

# ============ HELPERS (NEW) ============

def _mark_running_of_this_process_as_failed(host: str, pid: int | None = None, reason: str = "Executor interrompido manualmente"):
    """Marca como FALHOU todos os EXECUTANDO com meta.host/meta.pid deste processo ou do PID especificado."""
    if pid is None:
        pid = os.getpid()
    now = datetime.utcnow()
    changed = 0
    jobs = ExecJob.query.filter(ExecJob.status == "EXECUTANDO").all()
    for j in jobs:
        meta = j.meta or {}
        if meta.get("host") == host and meta.get("pid") == pid:
            j.status = "FALHOU"
            j.finished_at = now
            j.errors = (j.errors or []) + [reason]
            changed += 1
    if changed:
        db.session.commit()
    return changed

# ============ COMANDOS NOVOS (NEW) ============

@worker_cli.command("mark-error")
@click.option("--job-id", type=int, required=True, help="ID do job a ser marcado como erro.")
@click.option("--msg", default="Marcado como erro via CLI", show_default=True)
@with_appcontext
def mark_error(job_id: int, msg: str):
    """Força EXECUTANDO->FALHOU (ou qualquer status) para um job específico."""
    job = db.session.get(ExecJob, job_id)
    if not job:
        click.echo(f"Job {job_id} não encontrado.")
        return
    now = datetime.utcnow()
    job.status = "FALHOU"
    job.finished_at = now
    job.errors = (job.errors or []) + [msg]
    db.session.commit()
    click.echo(f"Job {job_id} marcado como FALHOU.")

@worker_cli.command("cleanup-running")
@click.option("--older-than-min", type=int, default=15, show_default=True,
              help="Marca como FALHOU jobs EXECUTANDO mais antigos que N minutos.")
@click.option("--only-current", is_flag=True, default=False,
              help="Limita a jobs EXECUTANDO deste processo (meta.host/pid).")
@click.option("--msg", default="Limpado por CLI (EXECUTANDO antigo)", show_default=True)
@with_appcontext
def cleanup_running(older_than_min: int, only_current: bool, msg: str):
    """
    Marca como FALHOU jobs EXECUTANDO antigos ou do processo atual.
    - Por padrão, atua em todos os EXECUTANDO com started_at < agora - N minutos.
    - Com --only-current, filtra por meta.host/meta.pid deste processo.
    """
    host = socket.gethostname()
    pid = os.getpid()
    now = datetime.utcnow()
    threshold = now - timedelta(minutes=max(older_than_min, 0))

    qs = ExecJob.query.filter(ExecJob.status == "EXECUTANDO").all()
    changed = 0
    for j in qs:
        meta = j.meta or {}
        if only_current and not (meta.get("host") == host and meta.get("pid") == pid):
            continue
        if j.started_at and j.started_at > now:
            # started_at no futuro? marca mesmo assim (inconsistência de relógio)
            pass
        elif not only_current and j.started_at and j.started_at >= threshold:
            # não é antigo o suficiente
            continue

        j.status = "FALHOU"
        j.finished_at = now
        j.errors = (j.errors or []) + [msg]
        changed += 1

    if changed:
        db.session.commit()
    click.echo(f"{changed} job(s) EXECUTANDO marcados como FALHOU.")

@worker_cli.command("retry")
@click.option("--job-id", type=int, default=None, help="ID do job a re-enfileirar (FALHOU->ESPERA).")
@click.option("--all-failed", is_flag=True, default=False, help="Re-enfileira todos os FALHOU.")
@click.option("--limit", type=int, default=50, show_default=True, help="Limite ao re-enfileirar em massa.")
@with_appcontext
def retry(job_id: int | None, all_failed: bool, limit: int):
    """
    Re-enfileira job(s) FALHOU (status -> ESPERA, progress=0, finished_at=NULL).
    Útil após marcar como erro ou após limpeza de EXECUTANDO pendurados.
    """
    now = datetime.utcnow()
    changed = 0

    if job_id:
        j = db.session.get(ExecJob, job_id)
        if not j:
            click.echo(f"Job {job_id} não encontrado.")
            return
        j.status = "ESPERA"
        j.progress = 0
        j.finished_at = None
        j.started_at = None
        j.errors = []
        changed = 1
    elif all_failed:
        rows = (ExecJob.query
                .filter(ExecJob.status == "FALHOU")
                .order_by(ExecJob.id.asc())
                .limit(max(limit, 1))
                .all())
        for j in rows:
            j.status = "ESPERA"
            j.progress = 0
            j.finished_at = None
            j.started_at = None
            j.errors = []
            changed += 1
    else:
        click.echo("Use --job-id ou --all-failed.")
        return

    if changed:
        db.session.commit()
    click.echo(f"{changed} job(s) re-enfileirado(s) como ESPERA.")


@worker_cli.command("ls")
@click.option("-n", "--limit", default=10, show_default=True, help="Quantidade de jobs para listar.")
@with_appcontext
def ls(limit: int):
    """Lista os últimos jobs (id, status, progresso, início/fim)."""
    from app.models import ExecJob
    rows = (ExecJob.query
            .order_by(ExecJob.id.desc())
            .limit(limit)
            .all())
    for j in rows:
        click.echo(f"#{j.id}  {j.status:<10}  prog={j.progress:<4}  "
                   f"start={j.started_at}  end={j.finished_at}")


# app/executor/cli.py (ou app/cli.py)
@worker_cli.command("show-map")
@click.option("--job-id", type=int, required=True)
@with_appcontext
def show_map(job_id: int):
    """Mostra o relatório de mapeamento salvo no meta.debug_map do ExecJob."""
    from app.models import ExecJob
    j = db.session.get(ExecJob, job_id)
    if not j:
        click.echo(f"Job {job_id} não encontrado.")
        return
    debug = (j.meta or {}).get("debug_map")
    if not debug:
        click.echo("Sem debug_map neste job.")
        return

    click.echo("=== DIAGNÓSTICOS ===")
    for d in debug.get("diagnosticos", []):
        click.echo(f"- idx={d['idx_diag']:>2} | '{d['original']}' -> '{d['final_usado']}' (regra={d['regra']}) qtd={d['qtd']}")

    click.echo("\n=== PARTES OK ===")
    for p in debug.get("partes_ok", []):
        click.echo(f"- {p}")

    click.echo("\n=== PARTES FALHOU (pid=0) ===")
    for p in debug.get("partes_fail", []):
        click.echo(f"- {p}")

    click.echo("\n=== DESTINOS OK ===")
    for d in debug.get("destinos_ok", []):
        click.echo(f"- {d}")

    click.echo("\n=== DESTINOS FALHOU (did=0) ===")
    for d in debug.get("destinos_fail", []):
        click.echo(f"- {d}")

    click.echo("\n=== CONTAGENS ===")
    click.echo(debug.get("contagens", {}))
    

@worker_cli.command("dump-commands")
@click.option("--job-id", type=int, required=True)
@with_appcontext
def dump_commands(job_id: int):
    from app.models import ExecJob
    j = db.session.get(ExecJob, job_id)
    if not j:
        click.echo("Job não encontrado.")
        return
    for i, c in enumerate(j.commands or []):
        click.echo(f"{i+1:03d}: {c}")
        if i >= 99:  # mostra 100 para inspeção rápida
            break
        
        
import json

def safe_load_payload(payload):
    import json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return []

    if isinstance(payload, list):
        result = []
        for item in payload:
            if isinstance(item, str):
                try:
                    result.append(json.loads(item))
                except Exception:
                    continue
            elif isinstance(item, dict):
                result.append(item)
        return result

    return []
@worker_cli.command("rebuild")
@click.option("--job-id", type=int, required=True, help="ID do job a ser reconstruído.")
@click.option("--delete-old", is_flag=True, default=False, help="Remove o job antigo antes de recriar.")
@click.option(
    "--gta-type",
    type=int,
    required=False,
    default=0,
    help="0=igual ao job antigo; 1=manual (GtaTemp); 2=upload (GtaUploadTmp)",
)
@with_appcontext
def rebuild(job_id: int, delete_old: bool, gta_type: int):
    """
    Reconstrói um job a partir dos dados do Registro original, GERANDO COMANDOS
    NO PADRÃO ANTIGO (idêntico ao set_comandos).

    - gta_type=1 -> usa dados manuais (GtaTemp) normalizados por normalize_gta_manual.
    - gta_type=2 -> usa dados de upload (GtaUploadTmp), lendo payload["records"] quando houver.
    - gta_type=0 -> usa a mesma fonte do job original (fallback 'upload' se vazio).
    """
    from app.models import ExecJob, Registro, GtaTemp, DifTmp, SifTmp, GtaUploadTmp
    from app.logic.preview_utils import (
        normalize_gta_manual, merge_diagnostics,
        build_legacy_structure_from_new, build_commands
    )
    from app.executor.service import cleanup_tmp_folder
    from app import db

    # 0) Carrega job/registro
    old_job = db.session.get(ExecJob, job_id)
    if not old_job:
        click.echo(f"Job {job_id} não encontrado.", err=True)
        return

    registro = db.session.get(Registro, old_job.registro_id)
    if not registro:
        click.echo(f"Registro {old_job.registro_id} não encontrado.", err=True)
        return

    # 1) Decide a fonte GTA
    if gta_type == 1:
        gta_source = "manual"
    elif gta_type == 2:
        gta_source = "upload"
    else:
        gta_source = old_job.gta_source or "upload"

    # 2) Carrega GTA conforme a fonte
    if gta_source == "manual":
        gtas_man = GtaTemp.query.filter_by(registro_id=registro.id).all()
        gta_records = normalize_gta_manual(gtas_man)  # espera objetos GtaTemp
    else:
        gta_tmp = GtaUploadTmp.query.filter_by(registro_id=registro.id).one_or_none()
        if gta_tmp:
            payload = gta_tmp.payload or {}
            if isinstance(payload, dict) and "records" in payload:
                gta_records = payload.get("records", [])
            else:
                # compat legado: lista/strings
                gta_records = safe_load_payload(payload)
        else:
            gta_records = []

    # 3) Carrega DIF/SIF (lendo "records" quando embrulhado)
    dif_tmp = DifTmp.query.filter_by(registro_id=registro.id).one_or_none()
    if dif_tmp:
        _p = dif_tmp.payload or {}
        dif_records = _p.get("records", []) if isinstance(_p, dict) else safe_load_payload(_p)
    else:
        dif_records = []

    sif_tmp = SifTmp.query.filter_by(registro_id=registro.id).one_or_none()
    if sif_tmp:
        _p = sif_tmp.payload or {}
        sif_records = _p.get("records", []) if isinstance(_p, dict) else safe_load_payload(_p)
    else:
        sif_records = []

    # 4) Validação mínima
    if not gta_records or not dif_records or not sif_records:
        click.echo(
            f"[rebuild] Faltam dados para reconstruir: "
            f"GTA={len(gta_records)} DIF={len(dif_records)} SIF={len(sif_records)}",
            err=True,
        )
        return

    # 5) Totais
    totais = {
        "machos": sum(int((r.get("machos", 0) or 0)) for r in gta_records),
        "femeas": sum(int((r.get("femeas", 0) or 0)) for r in gta_records),
        "total":  sum(int((r.get("total",  0) or 0)) for r in gta_records),
    }

    # 6) Merge dos diagnósticos (novo → formato unificado por lote)
    merged_diag = merge_diagnostics(dif_records, sif_records)

    # 7) NOVO: gera ESTRUTURA LEGACY e COMANDOS no padrão antigo
    estrutura_lotes = build_legacy_structure_from_new(gta_records, merged_diag, uf_index_default=23)
    comandos = build_commands(estrutura_lotes)

    # 8) Remoção do job antigo (opcional)
    if delete_old:
        db.session.delete(old_job)
        db.session.commit()
        click.echo(f"Job antigo #{job_id} removido.")

    # 9) Cria novo job
    new_job = ExecJob(
        registro_id=registro.id,
        owner_user_id=registro.user_id,
        gta_source=gta_source,  # grava a fonte realmente utilizada
        commands=comandos,
        meta={"totais": totais, "legacy": True},
        status="ESPERA",
        progress=0,
        errors=[],
        started_at=None,
        finished_at=None,
    )
    db.session.add(new_job)

    # 10) Atualiza registro e limpa temporários
    registro.status = "PT"
    db.session.commit()
    cleanup_tmp_folder(registro.id)

    click.echo(
        f"[rebuild] OK: novo job #{new_job.id} criado "
        f"(registro #{registro.id}, gta_source={gta_source}, "
        f"GTA={len(gta_records)} DIF/SIF={len(dif_records)}/{len(sif_records)})"
    )

@worker_cli.command("delete-job")
@click.option("--job-id", type=int, required=True, help="ID do job a ser excluído.")
@with_appcontext
def delete_job(job_id: int):
    """Exclui um job específico do banco."""
    from app.models import ExecJob
    from app import db

    job = db.session.get(ExecJob, job_id)
    if not job:
        click.echo(f"Job {job_id} não encontrado.")
        return

    db.session.delete(job)
    db.session.commit()
    click.echo(f"Job {job_id} excluído com sucesso.")


@worker_cli.command("delete-user")
@with_appcontext
@click.option("--id", type=int, required=True, help="ID do usuário que será excluido.")
def delete_user(id):
    from app.models import Usuario
    from app import db
    
    user = Usuario.query.filter_by(id=id).first()
    if not user:
        click.echo(f"Usuário com ID {id} não encontrado.")
        return
    
    db.session.delete(user)
    db.session.commit()
    click.echo(f"Usuário {id} excluído com sucesso.")

@worker_cli.command("generic")
@with_appcontext
def generic():
    from app.models import ExecJob
    from app import db

    trabalhos = ExecJob.query.filter(ExecJob.status=="QUEUED").all()
    for trabalho in trabalhos:
        trabalho.status = "ESPERA"

    #db.session.commit()