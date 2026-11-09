# app/blueprints/admin/routes.py
from datetime import datetime
from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash
from app.extensions import db
from app.models import Usuario, Role, AuditLog, ExecJob
from app.security import admin_required
from sqlalchemy.orm import selectinload
from app.utils.pagination import paginate_query

bp = Blueprint("admin", __name__)

# CRUD das constantes administraveis (Admin > Constantes)
from app.blueprints.admin.constantes import register as _register_constantes
_register_constantes(bp)

# Admin > API do MAPA (webservice): catalogos e sincronizacao do De->Para
from app.blueprints.admin.api_mapa import register as _register_api_mapa
_register_api_mapa(bp)

@bp.route("/")
@admin_required
def index():
    return render_template("admin/painel.html")


@bp.route("/usuarios")
@admin_required
def users():
    users = Usuario.query.order_by(Usuario.id.desc()).all()
    return render_template("admin/users_list.html", users=users)

@bp.route("/usuarios/novo", methods=["GET", "POST"])
@admin_required
def usuario_novo():
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        senha = (request.form.get("senha") or "").strip()
        role  = (request.form.get("role") or "user").strip()
        if not nome or not senha:
            flash("Informe nome e senha.", "warning")
            return redirect(url_for("admin.usuario_novo"))
        if Usuario.query.filter_by(nome=nome).first():
            flash("Nome já utilizado.", "danger")
            return redirect(url_for("admin.usuario_novo"))
        user = Usuario(
            nome=nome,
            senha=generate_password_hash(senha, method="pbkdf2:sha256"),
            role=Role.ADMIN if role == "admin" else Role.USER,
            is_active=True
        )
        db.session.add(user)
        db.session.commit()
        flash("Usuário criado.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=None)

@bp.route("/usuarios/<int:user_id>/edit", methods=["GET","POST"])
@admin_required
def users_edit(user_id):
    user = db.session.get(Usuario, user_id)
    if not user:
        flash("Usuário não encontrado.", "warning")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        nome = (request.form.get("nome"))
        role = (request.form.get("role") or "user").strip()
        active = request.form.get("active") == "1"
        new_pass = (request.form.get("nova_senha") or "").strip()
        user.role = Role.ADMIN if role == "admin" else Role.USER
        user.is_active = active
        user.nome = nome
        if new_pass:
            user.senha = generate_password_hash(new_pass, method="pbkdf2:sha256")
        db.session.commit()
        flash("Usuário atualizado.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", user=user)


@bp.route("/usuarios/<int:user_id>/excluir", methods=["POST", "GET"])
@admin_required
def user_excluir(user_id):
    user = db.session.get(Usuario, user_id)

    if request.form.get("confirm") != "yes":
        # Se alguém postar sem confirmar, apenas recusa
        flash("Confirmação ausente. Exclusão cancelada.", "warning")
        return redirect(url_for("admin.users"))
    else:
        db.session.delete(user)
        db.session.commit()
        flash("Usuário excluído.", "success")
    return redirect(url_for("admin.users"))

@bp.route("/audits")
@admin_required
def audits():
    rows = (AuditLog.query
            .options(selectinload(AuditLog.user)).order_by(AuditLog.created_at.desc()))
    
    rows.limit(500)

    page = request.args.get("page", 1, type=int)
    pagination = paginate_query(rows, page, db, per_page=20)
    return render_template("admin/audit_list.html", rows=pagination.items, pagination=pagination)

# Remova: _active_processes, ENV, subprocess, signal, os.kill, etc. Mantenha threading, time, etc.


# ---------------------------------------------------------------------------
# Threads: dados exibidos (pagina e atualizacao automatica usam os mesmos parciais)
# ---------------------------------------------------------------------------
FILTROS_JOBS = [("abertos", "Em aberto"), ("falhos", "Falhos"), ("todos", "Todos (últimos 100)")]


def _filtro_jobs(valor):
    return valor if valor in dict(FILTROS_JOBS) else "abertos"


def _resumo_erro(errors):
    """Primeira mensagem de erro, curta e legivel (a lista completa, com traceback, fica recolhida)."""
    for e in errors or []:
        linha = next((l.strip() for l in str(e).splitlines() if l.strip()), "")
        if linha and not linha.startswith("Traceback"):
            return linha if len(linha) <= 200 else linha[:200] + "…"
    return ""


def _jobs_view(filtro="abertos", limite=100):
    from app.models import Registro, Usuario
    q = ExecJob.query.order_by(ExecJob.id.desc())
    if filtro == "abertos":
        q = q.filter(ExecJob.status != "SUCESSO")
    elif filtro == "falhos":
        q = q.filter(ExecJob.status == "FALHOU")
    jobs = q.limit(limite).all()
    regs = {r.id: r for r in Registro.query.filter(Registro.id.in_({j.registro_id for j in jobs})).all()} if jobs else {}
    nomes = dict(db.session.query(Usuario.id, Usuario.nome).filter(
        Usuario.id.in_({j.owner_user_id for j in jobs}))) if jobs else {}
    out = []
    for j in jobs:
        meta, reg = (j.meta or {}), regs.get(j.registro_id)
        total = len(j.commands or [])
        backend = meta.get("backend", "browser")
        pct = 100 if j.status == "SUCESSO" else (int(100 * (j.progress or 0) / total) if total else 0)
        out.append({
            "id": j.id, "status": j.status, "pct": pct, "backend": backend,
            "modulo": "Comercialização" if meta.get("modulo") == "comercializacao" else "Abate",
            "lancamento": meta.get("lancamento"),
            "registro_id": j.registro_id, "periodo": reg.periodo_label if reg else "registro removido",
            "usuario": nomes.get(j.owner_user_id, "?"),
            "inicio": j.started_at, "fim": j.finished_at,
            "erro": _resumo_erro(j.errors), "errors": list(j.errors or []),
            "incerto": bool(meta.get("incerto")),
            "pode_retry": j.status == "FALHOU",
            "pode_limpar": j.status == "FALHOU" and meta.get("modulo") == "comercializacao" and backend != "api",
        })
    return out


def _workers_view():
    from app.models import WorkerSession
    return WorkerSession.query.order_by(WorkerSession.started_at.desc()).all()


def _registros_sem_job():
    """Registros ainda nao confirmados (status AT) por modulo: explica por que nao ha job na fila."""
    from app.models import Registro
    linhas = (db.session.query(Registro.tipo, db.func.count(Registro.id))
              .filter(Registro.status == "AT", ~Registro.exec_jobs.any()).group_by(Registro.tipo).all())
    return {("Comercialização" if t == "comercializacao" else "Abate"): n for t, n in linhas}


@bp.route("/driver/verificar", methods=["POST"])
@admin_required
def driver_verificar():
    """Refaz a verificacao do chromedriver (baixa/atualiza se nao combinar com o Chrome). Pode levar alguns segundos."""
    from app.runner import drivers
    st = drivers.atualizar()
    flash(st["mensagem"], "success" if st["ok"] else "danger")
    return redirect(url_for("admin.threads"))


@bp.route("/threads/jobs", methods=["GET"])
@admin_required
def threads_jobs_fragmento():
    filtro = _filtro_jobs(request.args.get("filtro"))
    return render_template("admin/_threads_jobs.html", jobs=_jobs_view(filtro), filtro=filtro)


@bp.route("/threads/workers", methods=["GET"])
@admin_required
def threads_workers_fragmento():
    return render_template("admin/_threads_workers.html", workers=_workers_view())

@bp.route("/threads", methods=["POST", "GET"])
@admin_required
def threads():
    if request.method == "POST":
        numero = request.form.get("numero", default=0)
        perfil = request.form.get("profile")

        if not numero or numero == "" or not perfil or perfil == "":
            flash("Defina o número de threads e o perfil.", "warning")
            return redirect(url_for('admin.threads'))

        # Inicia pool de threads
        from app.executor.queue import start_pool
        try:
            start_pool(current_app, num_workers=int(numero), profile=perfil,
                       force_restart=bool(request.form.get("force_restart")))
            flash(f"Pool de {numero} threads iniciado com perfil {perfil}.", "success")
        except Exception as e:
            flash(f"Erro ao iniciar threads: {e}", "danger")
        return redirect(url_for('admin.threads'))

    # GET: a pagina mostra o que a ultima atualizacao automatica mostraria (mesmos parciais)
    filtro = _filtro_jobs(request.args.get("filtro"))
    from app.runner import drivers
    return render_template(
        "admin/threads.html", driver=drivers.estado(), filtro=filtro, jobs=_jobs_view(filtro), workers=_workers_view(),
        sem_job=_registros_sem_job(), FILTROS=FILTROS_JOBS,
    )
# Remova import signal se estiver lá

@bp.route("/stop-thread/<int:worker_id>", methods=["POST"])
@admin_required
def stop_thread(worker_id):
    from app.models import WorkerSession
    from app.executor.queue import _active_threads
    worker = db.session.get(WorkerSession, worker_id)
    if not worker or worker.status != "EXECUTANDO":
        flash(f"Worker {worker_id} já parado.", "info")
        return redirect(url_for('admin.threads'))
    
    # Sinaliza parada graciosa via evento
    if worker.name in _active_threads:
        _, event = _active_threads[worker.name]
        event.set()
        flash(f"Sinal de parada enviado para worker {worker_id}.", "info")
    else:
        worker.status = "PARADO"
        db.session.commit()
        flash(f"Worker {worker_id} já estava parado.", "info")
    return redirect(url_for('admin.threads'))

@bp.route("/force-stop-thread/<int:worker_id>", methods=["POST"])
@admin_required
def force_stop_thread(worker_id):
    from app.models import WorkerSession
    from app.executor.queue import _active_threads
    worker = db.session.get(WorkerSession, worker_id)
    if not worker:
        flash(f"Worker {worker_id} não encontrado.", "warning")
        return redirect(url_for('admin.threads'))
    
    # Sinaliza parada forçada
    if worker.name in _active_threads:
        _, event = _active_threads[worker.name]
        event.set()
        # Aguarda um pouco
        import time
        time.sleep(1)
        # Remove do dict apenas se ainda existir
        _active_threads.pop(worker.name, None)  # Usa pop com default para evitar KeyError
    else:
        flash(f"Worker {worker.name} não encontrado em threads ativas.", "info")
    
    # Marca jobs como FALHOU
    from app.models import ExecJob
    # (filtra em Python: JSON.contains nao existe no SQLite)
    jobs_to_fail = [j for j in ExecJob.query.filter(ExecJob.status == "EXECUTANDO").all()
                    if (j.meta or {}).get("worker_name") == worker.name]
    for job in jobs_to_fail:
        job.status = "FALHOU"
        job.finished_at = datetime.utcnow()
        job.errors = (job.errors or []) + ["Worker finalizado forçadamente."]
    db.session.commit()
    
    # Remove worker do DB
    db.session.delete(worker)
    db.session.commit()
    flash(f"Worker {worker_id} finalizado. Jobs marcados como FALHOU.", "success")
    return redirect(url_for('admin.threads'))

@bp.route("/api/active-processes", methods=["GET"])
@admin_required
def api_active_processes():
    from app.models import WorkerSession
    workers = WorkerSession.query.filter(WorkerSession.status == "EXECUTANDO").all()
    data = {w.id: {"name": w.name, "profile": w.profile, "threads": w.threads, "started_at": w.started_at.timestamp()} for w in workers}
    return {"active_processes": data}

# api_pending_jobs permanece igual
@bp.route("/retry-job/<int:job_id>", methods=["POST"])
@admin_required
def retry_job(job_id):
    from app.models import ExecJob, Registro
    from datetime import datetime
    
    job = db.session.get(ExecJob, job_id)
    if not job:
        flash(f"Job {job_id} não encontrado.", "warning")
        return redirect(url_for('admin.threads'))
    
    if job.status != "FALHOU":
        flash(f"Job {job_id} não está em status FALHOU (atual: {job.status}).", "warning")
        return redirect(url_for('admin.threads'))
    
    limpar = request.form.get("limpar") == "1"
    registro: Registro = db.session.get(Registro, job.registro_id)
    if registro is None:
        flash(f"Job {job_id}: o registro #{job.registro_id} não existe mais.", "danger")
        return redirect(url_for('admin.threads'))
    if limpar and not (registro.tipo == "comercializacao" and (job.meta or {}).get("backend") != "api"):
        flash(f"Job {job_id}: 'limpar portal' só vale para comercialização no modo navegador; ignorado.", "warning")
        limpar = False
    # VETO no retry: refaz comandos/payload com o De->Para ATUAL; se algo estiver sem vinculo, nao reenfileira
    from app.executor.service import Bloqueado, reconstruir_job
    from app.logic.bloqueios import resumo_flash
    try:
        reconstruir_job(job, registro, limpar=limpar)
    except Bloqueado as e:
        db.session.rollback()
        flash(resumo_flash(f"Job {job_id} NÃO foi reenfileirado:", e.motivos), "danger")
        return redirect(url_for('admin.threads'))
    if (job.meta or {}).get("incerto"):
        flash(f"Job {job_id}: o resultado do envio anterior é DESCONHECIDO. Se o mapa já foi gravado no "
              "MAPA, o reenvio pode duplicá-lo (POST).", "warning")
    job.status = "ESPERA"
    registro.status = "PT"
    job.progress = 0
    job.finished_at = None
    job.started_at = None
    job.errors = []
    db.session.commit()
    
    flash(f"Job {job_id} re-enfileirado como ESPERA.", "success")
    return redirect(url_for('admin.threads'))


@bp.route("/api/pending-jobs", methods=["GET"])
@admin_required
def api_pending_jobs():
    from app.models import ExecJob
    rows = ExecJob.query.filter(ExecJob.status != "SUCESSO").all()
    jobs = []
    for job in rows:
        total_commands = len(job.commands or [])
        progress_percent = 0
        if total_commands > 0 and job.progress is not None:
            progress_percent = int((job.progress / total_commands) * 100)
        jobs.append({
            "id": job.id,
            "status": job.status,
            "modulo": (job.meta or {}).get("modulo", "abate"),
            "backend": (job.meta or {}).get("backend", "browser"),
            "errors": job.errors or [],
            "progress": progress_percent,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        })
    return {"pending_jobs": jobs}

@bp.route("/api/workers", methods=["GET"])
@admin_required
def api_workers():
    from app.models import WorkerSession
    workers = WorkerSession.query.all()
    data = {w.id: {"name": w.name, "profile": w.profile, "threads": w.threads, "status": w.status, "started_at": w.started_at.timestamp()} for w in workers}
    return {"workers": data}