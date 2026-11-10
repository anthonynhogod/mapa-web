"""Modulo Producao: planilha de producao (mensal) -> preview por produto -> job de execucao no navegador.

O mapa de producao so e lancado pelo portal (navegador): o webservice do MAPA nao cobre a producao.
O registro nasce na tela de inclusao do portal, entao um job que falha antes do ultimo comando nao deixa
nada gravado.
"""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import escape
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from app.executor.queue import get_queue
from app.extensions import db
from app.forms import UploadProducaoForm
from app.logic.bloqueios import avaliar_credencial, motivos_pendencias, resumo_flash
from app.logic.constantes import ColetorPendencias, ConstanteNaoMapeada
from app.logic.producao import build_commands, build_plano, totais as plano_totais
from app.models import ExecJob, Registro, UploadStatus, VendasTmp
from app.utils.audit import log_action
from app.utils.pagination import PER_PAGE, paginate_query, resolve_page

from .parser import parse_competencia, periodo_do_mes, validar_producao

bp = Blueprint("producao", __name__)

TIPO = "producao"
STATUS_LABELS = {"AT": "Em andamento", "PT": "Pendente", "FZ": "Finalizado", "ER": "Erro"}


def _registro_do_usuario(registro_id: int) -> Registro:
    reg = Registro.query.filter_by(id=registro_id, user_id=current_user.id, tipo=TIPO).first()
    if reg is None:
        abort(404)
    return reg


def _tmp(registro_id: int):
    return VendasTmp.query.filter_by(registro_id=registro_id).one_or_none()


def _payload(tmp):
    payload = (tmp.payload if tmp else None) or {}
    return payload.get("records", []), payload.get("meta", {"errors": [], "warnings": [], "counts": {}})


def _fmt_issue(it):
    return f"{it.get('where') or '?'}: {it.get('message') or ''}"


def _competencia(registro: Registro) -> str:
    return registro.periodo_ini.strftime("%m/%Y")


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------
@bp.route("/", methods=["GET"])
@login_required
def lista():
    q = (Registro.query
         .filter_by(user_id=current_user.id, tipo=TIPO)
         .order_by(Registro.periodo_ini.desc(), Registro.id.desc()))
    pagination = paginate_query(q, resolve_page(default=1), db, per_page=PER_PAGE)
    return render_template("producao/lista.html", registros=pagination.items, pagination=pagination,
                           STATUS_LABELS=STATUS_LABELS)


# ---------------------------------------------------------------------------
# Novo: upload da planilha
# ---------------------------------------------------------------------------
@bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    form = UploadProducaoForm()
    if not form.validate_on_submit():
        if request.method == "POST":
            flash("Verifique o arquivo enviado (somente .xlsx).", "warning")
            return render_template("producao/novo.html", form=form), 400
        return render_template("producao/novo.html", form=form)

    arquivo = form.file.data
    filename = secure_filename(arquivo.filename or "") or "planilha.xlsx"
    informada = (form.competencia.data or "").strip()
    resultado = validar_producao(arquivo.stream, informada or None)
    meta = resultado["meta"]
    erros = list(meta["errors"])

    comp = meta.get("competencia")
    if not comp and not erros:
        erros.append({"level": "error", "where": "periodo",
                      "message": "Competencia nao identificada: informe o mes (MM/AAAA)."})
    if erros:
        resumo = "; ".join(_fmt_issue(e) for e in erros[:5])
        flash(f"Planilha com erros: {escape(resumo)}", "danger")
        return render_template("producao/novo.html", form=form), 400

    ano, mes = parse_competencia(comp["texto"])
    ini, fim = periodo_do_mes(ano, mes)

    existente = Registro.query.filter_by(
        user_id=current_user.id, tipo=TIPO, periodo_ini=ini, periodo_fim=fim
    ).first()
    if existente and existente.status != "AT":
        flash(
            f"Ja existe um registro de producao para {ini:%m/%Y} "
            f"(status {escape(STATUS_LABELS.get(existente.status, existente.status))}). "
            "Exclua-o em Producao > Registros para enviar uma nova planilha.",
            "danger",
        )
        return render_template("producao/novo.html", form=form), 409

    registro = existente or Registro(
        data=ini, periodo_ini=ini, periodo_fim=fim, tipo=TIPO, lancamento="producao", especie="suino",
        user_id=current_user.id, data_registro=datetime.now(),
    )
    registro.obs = (form.obs.data or "").strip() or registro.obs
    if existente is None:
        db.session.add(registro)
        db.session.flush()

    tmp = _tmp(registro.id)
    if tmp is None:
        tmp = VendasTmp(registro_id=registro.id, uploaded_by=current_user.id)
        db.session.add(tmp)
    tmp.filename = filename
    tmp.model = "producao"
    tmp.payload = resultado
    tmp.status = UploadStatus.VALIDATED
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Nao foi possivel salvar a planilha (registro duplicado).", "danger")
        return render_template("producao/novo.html", form=form), 409

    log_action("upload:producao", entity="Registro", entity_id=registro.id,
               meta={"arquivo": filename, "registros": meta["counts"].get("output_records"), "competencia": comp["texto"]})
    if meta["warnings"]:
        aviso = "; ".join(_fmt_issue(w) for w in meta["warnings"][:3])
        flash(f"Planilha validada com avisos: {escape(aviso)}", "warning")
    flash("Planilha validada. Confira o preview antes de executar.", "success")
    return redirect(url_for("producao.preview", registro_id=registro.id))


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
@bp.route("/<int:registro_id>/preview", methods=["GET"])
@login_required
def preview(registro_id: int):
    registro = _registro_do_usuario(registro_id)
    tmp = _tmp(registro.id)
    records, meta = _payload(tmp)
    if not records:
        flash("Nenhuma planilha de producao valida para este registro. Envie uma planilha.", "warning")
        return redirect(url_for("producao.novo"))

    coletor = ColetorPendencias()
    plano = build_plano(records, coletor)
    cred, bloqueios = avaliar_credencial(registro.user_id)
    if not coletor.vazio:
        return render_template(
            "registros/preview/pendencias.html",
            registro=registro, pendencias=coletor.listar(), gta_source=None, bloqueios=bloqueios,
            voltar_url=url_for("producao.preview", registro_id=registro.id),
        )

    sif = (cred.numero_sif if cred else "") or ""
    comandos = build_commands(plano, competencia=_competencia(registro), numero_sif=sif)
    return render_template(
        "producao/preview.html",
        registro=registro, plano=plano, totais=plano_totais(plano), meta=meta,
        comandos=comandos, tmp=tmp, bloqueios=bloqueios, competencia=_competencia(registro),
    )


# ---------------------------------------------------------------------------
# Finalizar: cria o ExecJob
# ---------------------------------------------------------------------------
@bp.route("/<int:registro_id>/finalizar", methods=["POST"])
@login_required
def finalizar(registro_id: int):
    registro = _registro_do_usuario(registro_id)
    if registro.status != "AT":
        flash("Registro nao esta em aberto para finalizacao.", "warning")
        return redirect(url_for("producao.lista"))

    cred, bloqueios = avaliar_credencial(current_user.id)
    if cred is None:
        flash("Cadastre suas credenciais do MAPA antes de executar (Configuracoes > Credenciais MAPA).", "warning")
        return redirect(url_for("public.credenciais_mapa"))

    tmp = _tmp(registro.id)
    records, _meta = _payload(tmp)
    if not records:
        flash("Dados insuficientes para finalizar.", "warning")
        return redirect(url_for("producao.novo"))

    if ExecJob.query.filter(
        ExecJob.registro_id == registro.id, ExecJob.status.in_(["ESPERA", "EXECUTANDO"])
    ).first():
        flash("Ja existe um job ativo para este registro.", "warning")
        return redirect(url_for("producao.lista"))

    # VETO: refaz a checagem completa no servidor (a tela pode estar velha) e lista TODOS os motivos
    coletor = ColetorPendencias()
    plano = build_plano(records, coletor)
    motivos = bloqueios + motivos_pendencias(coletor)
    if coletor.vazio and not plano:
        motivos.append("Nenhum produto com quantidade maior que zero para lançar.")
    if motivos:
        flash(resumo_flash("Lançamento vetado — nada foi enviado ao MAPA:", motivos), "danger")
        return redirect(url_for("producao.preview", registro_id=registro.id))

    competencia = _competencia(registro)
    try:
        # ultima trava: sem coletor, qualquer termo sem vinculo aborta
        plano = build_plano(records)
        job = ExecJob(
            registro_id=registro.id, owner_user_id=registro.user_id, gta_source="producao",
            commands=build_commands(plano, competencia=competencia, numero_sif=cred.numero_sif),
            meta={
                "modulo": TIPO, "lancamento": "producao", "competencia": competencia,
                "numero_sif": cred.numero_sif, "plano": plano, "totais": plano_totais(plano),
            },
            status="ESPERA", progress=0, errors=[],
        )
    except ConstanteNaoMapeada as e:
        db.session.rollback()
        flash(
            f"Nao foi possivel finalizar: {escape(e.tipo)} sem vinculo ({escape(e.valor)}). "
            "Cadastre o vinculo em Admin > Constantes e valide novamente.",
            "danger",
        )
        return redirect(url_for("producao.preview", registro_id=registro.id))

    db.session.add(job)
    registro.status = "PT"
    if tmp is not None:
        tmp.status = UploadStatus.PROCESSED
    db.session.commit()
    get_queue().enqueue(job.id)
    log_action("job:create", entity="ExecJob", entity_id=job.id, meta={"modulo": TIPO})

    flash("Validacao confirmada. Job enfileirado para execucao.", "success")
    return redirect(url_for("producao.lista"))
