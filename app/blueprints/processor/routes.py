# app/blueprints/processor/routes.py
from flask import Blueprint, request, redirect, url_for, flash, current_app, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from app.forms import UploadGtaForm, UploadDifForm, UploadSifForm
from app.models import db, Registro, GtaUploadTmp, DifTmp, SifTmp
from .services import process_upload, ValidationFailed

bp = Blueprint("processor", __name__)

# Se seus validadores usam pd.read_excel, limite a .xlsx para evitar erro.
ALLOWED_BY_TYPE = {
    "gta": {"xlsx"},  # quando suportar csv/txt, adicione aqui e adapte validar_gta
    "dif": {"xlsx"},
    "sif": {"xlsx"},
}

MODEL_BY_TYPE = {
    "gta": GtaUploadTmp,
    "dif": DifTmp,
    "sif": SifTmp,
}

def _has_existing_temp(registro_id: int, filetype: str) -> bool:
    Model = MODEL_BY_TYPE[filetype]
    return db.session.query(Model.id).filter_by(registro_id=registro_id).first() is not None


def _allowed_by_type(filename: str, filetype: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_BY_TYPE.get(filetype, set())

def _ensure_registro_owner(registro_id: int, user_id: int):
    reg = Registro.query.filter_by(id=registro_id, user_id=user_id).first()
    if reg is None:
        abort(404)

@bp.errorhandler(RequestEntityTooLarge)
def _too_large(e):
    max_len = current_app.config.get("MAX_CONTENT_LENGTH", 0)
    max_mb = int(max_len / (1024 * 1024)) if max_len else None
    if max_mb:
        flash(f"Arquivo ultrapassa o limite configurado ({max_mb} MB).", "danger")
    else:
        flash("Arquivo ultrapassa o limite configurado.", "danger")
    return redirect(request.referrer or url_for("registros.prontos"))

# app/blueprints/processor/routes.py

def _handle_upload(*, registro_id: int, form, filetype: str, success_msg: str):
    _ensure_registro_owner(registro_id, current_user.id)

    if not form.validate_on_submit():
        flash("Verifique o arquivo e o modelo selecionado.", "warning")
        return redirect(url_for("registros.incluir_dados", registro_id=registro_id))

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Nenhum arquivo selecionado.", "warning")
        return redirect(url_for("registros.incluir_dados", registro_id=registro_id))

    filename = secure_filename(f.filename)
    if not _allowed_by_type(filename, filetype):
        exts = ", ".join(sorted(ALLOWED_BY_TYPE.get(filetype, set())))
        flash(f"Extensão não permitida para {filetype.upper()}. Use: {exts.upper()}.", "danger")
        return redirect(url_for("registros.incluir_dados", registro_id=registro_id))

    model = getattr(form, "model", None).data if hasattr(form, "model") else None

    
    if _has_existing_temp(registro_id, filetype) and request.form.get("confirm") != "yes":
        flash(f"Já existe um {filetype.upper()} temporário vinculado a este registro. Confirme a substituição para prosseguir.", "warning")
        return redirect(url_for("registros.incluir_dados", registro_id=registro_id, confirm=f"replace-{filetype}"))

    try:
        result = process_upload(
            file=f,
            filename=filename,
            filetype=filetype,
            model=model or "",
            registro_id=registro_id,
            user_id=current_user.id,
        )
        if getattr(result, "warnings_count", 0) > 0:
            flash(f"Arquivo validado com avisos ({result.warnings_count}). Verifique os detalhes na pré-visualização.", "warning")
        flash(success_msg, "success")

        # ➜ NOVO: redireciona para a preview do tipo
        if filetype == "gta":
            return redirect(url_for("registros.preview_gta", registro_id=registro_id))
        elif filetype == "dif":
            return redirect(url_for("registros.preview_dif", registro_id=registro_id))
        elif filetype == "sif":
            return redirect(url_for("registros.preview_sif", registro_id=registro_id))

    except ValidationFailed as ve:
        # Erros bloqueantes de validação: não validar e exibir detalhes
        db.session.rollback()

        # Monte uma mensagem concisa com até 5 erros
        def _fmt_issue(it):
            where = it.get("where") or "?"
            msg = it.get("message") or ""
            level = (it.get("level") or "").upper()
            return f"[{level}] {where}: {msg}"

        top_errors = "; ".join(_fmt_issue(e) for e in ve.errors[:5]) or "Erros de validação encontrados."
        flash(f"Falha ao processar {filetype.upper()}: {top_errors}", "danger")

        # Se houver warnings, informe também (sem bloquear)
        if ve.warnings:
            top_warnings = "; ".join(_fmt_issue(w) for w in ve.warnings[:3])
            flash(f"Avisos durante a leitura: {top_warnings}", "info")
    except Exception as e:
        current_app.logger.exception(f"Falha ao processar {filetype.upper()}")
        db.session.rollback()
        flash(f"Falha ao processar {filetype.upper()}: {e}", "danger")
    # fallback
    return redirect(url_for("registros.incluir_dados", registro_id=registro_id))


@bp.post("/<int:registro_id>/upload-gta")
@login_required
def upload_gta(registro_id):
    form = UploadGtaForm()
    return _handle_upload(
        registro_id=registro_id,
        form=form,
        filetype="gta",
        success_msg="GTA enviado e validado para a tabela temporária."
    )

@bp.post("/<int:registro_id>/upload-dif")
@login_required
def upload_dif(registro_id):
    form = UploadDifForm()
    return _handle_upload(
        registro_id=registro_id,
        form=form,
        filetype="dif",
        success_msg="DIF enviado e validado para a tabela temporária."
    )

@bp.post("/<int:registro_id>/upload-sif")
@login_required
def upload_sif(registro_id):
    form = UploadSifForm()
    return _handle_upload(
        registro_id=registro_id,
        form=form,
        filetype="sif",
        success_msg="SIF enviado e validado para a tabela temporária."
    )