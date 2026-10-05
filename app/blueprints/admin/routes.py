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
            start_pool(current_app, num_workers=int(numero), profile=perfil)
            flash(f"Pool de {numero} threads iniciado com perfil {perfil}.", "success")
        except Exception as e:
            flash(f"Erro ao iniciar threads: {e}", "danger")
        return redirect(url_for('admin.threads'))

    # GET: Lista workers ativos do DB e jobs pendentes
    from app.models import WorkerSession
    all_workers = WorkerSession.query.order_by(WorkerSession.started_at.desc()).all()  # Ordena por data decresc
    rows = ExecJob.query.filter(ExecJob.status != "SUCESSO").all()
    return render_template("admin/threads.html", active_processes=all_workers, rows=rows)
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
    jobs_to_fail = ExecJob.query.filter(
        ExecJob.status == "EXECUTANDO",
        ExecJob.meta.contains({"worker_name": worker.name})
    ).all()
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
    
    now = datetime.utcnow()
    limpar = request.form.get("limpar") == "1"
    if limpar:
        # Comercializacao: reprocessar limpando as transacoes do portal antes de relancar
        # (o padrao e abortar se o registro do periodo ja tiver dados).
        from app.logic.comercializacao import VERIFICAR_FN, para_limpar
        cmds = list(job.commands or [])
        if cmds and cmds[0].startswith(VERIFICAR_FN + "("):
            cmds[0] = para_limpar(cmds[0])
            job.commands = cmds
        else:
            flash(f"Job {job_id}: 'limpar portal' só vale para o modo navegador; ignorado.", "warning")
    if (job.meta or {}).get("incerto"):
        flash(f"Job {job_id}: o resultado do envio anterior é DESCONHECIDO. Se o mapa já foi gravado no "
              "MAPA, o reenvio pode duplicá-lo (POST).", "warning")
    job.status = "ESPERA"
    registro: Registro = db.session.get(Registro, job.registro_id)
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