"""Modulo Comercializacao: planilha de vendas -> preview por UF -> job de execucao."""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import escape
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from app.executor.queue import get_queue
from app.executor.service import create_api_job
from app.mapa_api.montagem import json_legivel, preparar_comercializacao, usa_api
from app.mapa_api.payloads import DadosIncompletos
from app.extensions import db
from app.forms import UploadVendasForm
from app.logic.comercializacao import (
    build_commands, build_plano, rotulos_ativos, tipo_config, totais as plano_totais,
)
from app.logic.constantes import ColetorPendencias, ConstanteNaoMapeada
from app.models import ExecJob, MapaCredencial, Registro, TipoLancamento, UploadStatus, VendasTmp
from app.utils.audit import log_action
from app.utils.format import normalize_str
from app.utils.pagination import PER_PAGE, paginate_query, resolve_page

from .parser import parse_data_br
from .parsers import parser_para

bp = Blueprint("comercializacao", __name__)

TIPO = "comercializacao"
STATUS_LABELS = {"AT": "Em andamento", "PT": "Pendente", "FZ": "Finalizado", "ER": "Erro"}


def _registro_do_usuario(registro_id: int) -> Registro:
    reg = Registro.query.filter_by(id=registro_id, user_id=current_user.id, tipo=TIPO).first()
    if reg is None:
        abort(404)
    return reg


def _vendas_tmp(registro_id: int):
    return VendasTmp.query.filter_by(registro_id=registro_id).one_or_none()


def _payload(tmp):
    payload = (tmp.payload if tmp else None) or {}
    return payload.get("records", []), payload.get("meta", {"errors": [], "warnings": [], "counts": {}})


def _tipos_ativos():
    return TipoLancamento.query.filter_by(ativo=True).order_by(TipoLancamento.id).all()


def _preencher_tipos(form):
    form.lancamento.choices = [
        (t.codigo, t.nome if parser_para(t.codigo) else f"{t.nome} (layout ainda não suportado)")
        for t in _tipos_ativos()
    ]


def _tipo_do_registro(registro: Registro):
    return TipoLancamento.query.filter_by(codigo=registro.lancamento or "venda", ativo=True).first()


def _fmt_issue(it):
    return f"{it.get('where') or '?'}: {it.get('message') or ''}"


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
    return render_template(
        "comercializacao/lista.html",
        registros=pagination.items, pagination=pagination, STATUS_LABELS=STATUS_LABELS,
    )


# ---------------------------------------------------------------------------
# Novo: upload da planilha
# ---------------------------------------------------------------------------
@bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    form = UploadVendasForm()
    _preencher_tipos(form)
    if not form.validate_on_submit():
        if request.method == "POST":
            flash("Verifique o arquivo enviado (somente .xlsx).", "warning")
            return render_template("comercializacao/novo.html", form=form), 400
        return render_template("comercializacao/novo.html", form=form)

    codigo = form.lancamento.data
    validar = parser_para(codigo)
    if validar is None:
        flash("O layout de planilha deste tipo de lançamento ainda não é suportado.", "warning")
        return render_template("comercializacao/novo.html", form=form), 400

    arquivo = form.file.data
    filename = secure_filename(arquivo.filename or "") or "planilha.xlsx"
    resultado = validar(arquivo.stream)
    meta = resultado["meta"]
    erros = list(meta["errors"])

    # periodo: o informado manualmente vence o lido da planilha
    ini = parse_data_br(form.periodo_ini.data or "") if form.periodo_ini.data else None
    fim = parse_data_br(form.periodo_fim.data or "") if form.periodo_fim.data else None
    if (form.periodo_ini.data and not ini) or (form.periodo_fim.data and not fim):
        erros.append({"level": "error", "where": "periodo", "message": "Data invalida. Use dd/mm/aaaa."})
    lido = meta.get("periodo") or {}
    if not ini and lido.get("ini"):
        ini = datetime.strptime(lido["ini"], "%Y-%m-%d").date()
    if not fim and lido.get("fim"):
        fim = datetime.strptime(lido["fim"], "%Y-%m-%d").date()
    if not (ini and fim):
        erros.append({"level": "error", "where": "periodo",
                      "message": "Periodo nao identificado: informe inicio e fim do periodo."})
    elif ini > fim:
        erros.append({"level": "error", "where": "periodo", "message": "O inicio do periodo e posterior ao fim."})

    if erros:
        resumo = "; ".join(_fmt_issue(e) for e in erros[:5])
        flash(f"Planilha com erros: {escape(resumo)}", "danger")
        return render_template("comercializacao/novo.html", form=form), 400

    meta["periodo"] = {"ini": ini.isoformat(), "fim": fim.isoformat()}

    existente = Registro.query.filter_by(
        user_id=current_user.id, tipo=TIPO, periodo_ini=ini, periodo_fim=fim, lancamento=codigo
    ).first()
    if existente and existente.status != "AT":
        flash(
            f"Ja existe um registro de comercializacao ({escape(codigo)}) para {ini:%d/%m/%Y} a {fim:%d/%m/%Y} "
            f"(status {escape(STATUS_LABELS.get(existente.status, existente.status))}). "
            "Exclua-o em Comercializacao > Registros para enviar uma nova planilha.",
            "danger",
        )
        return render_template("comercializacao/novo.html", form=form), 409

    registro = existente or Registro(
        data=ini, periodo_ini=ini, periodo_fim=fim, tipo=TIPO, lancamento=codigo, especie="suino",
        user_id=current_user.id, data_registro=datetime.now(),
    )
    registro.obs = (form.obs.data or "").strip() or registro.obs
    if existente is None:
        db.session.add(registro)
        db.session.flush()

    tmp = _vendas_tmp(registro.id)
    if tmp is None:
        tmp = VendasTmp(registro_id=registro.id, uploaded_by=current_user.id)
        db.session.add(tmp)
    tmp.filename = filename
    tmp.model = codigo
    tmp.payload = resultado
    tmp.status = UploadStatus.VALIDATED
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Nao foi possivel salvar a planilha (registro duplicado).", "danger")
        return render_template("comercializacao/novo.html", form=form), 409

    log_action("upload:vendas", entity="Registro", entity_id=registro.id,
               meta={"arquivo": filename, "registros": meta["counts"].get("output_records")})
    if meta["warnings"]:
        aviso = "; ".join(_fmt_issue(w) for w in meta["warnings"][:3])
        flash(f"Planilha validada com avisos: {escape(aviso)}", "warning")
    flash("Planilha validada. Confira o preview antes de executar.", "success")
    return redirect(url_for("comercializacao.preview", registro_id=registro.id))


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
@bp.route("/<int:registro_id>/preview", methods=["GET"])
@login_required
def preview(registro_id: int):
    registro = _registro_do_usuario(registro_id)
    tmp = _vendas_tmp(registro.id)
    records, meta = _payload(tmp)
    if not records:
        flash("Nenhuma planilha de vendas valida para este registro. Envie uma planilha.", "warning")
        return redirect(url_for("comercializacao.novo"))

    tipo = _tipo_do_registro(registro)
    if tipo is None:
        flash("O tipo de lançamento deste registro não está ativo. Ative-o em Admin > Constantes.", "danger")
        return redirect(url_for("comercializacao.lista"))

    coletor = ColetorPendencias()
    if usa_api():
        try:
            prep = preparar_comercializacao(registro, records, tipo, coletor)
        except NotImplementedError as e:
            flash(escape(str(e)), "warning")
            return redirect(url_for("comercializacao.lista"))
        plano = prep["plano"]
        comandos = ([f"{prep['metodo']} /comercializacao  (Content-Type: application/json)"]
                    + json_legivel(prep["payload"])) if prep["payload"] is not None else []
    else:
        plano = build_plano(records, coletor, backend="browser")
        comandos = build_commands(plano, tipo=tipo_config(tipo), rotulos=rotulos_ativos()) if coletor.vazio else []

    if not coletor.vazio:
        return render_template(
            "registros/preview/pendencias.html",
            registro=registro, pendencias=coletor.listar(), gta_source=None,
            voltar_url=url_for("comercializacao.preview", registro_id=registro.id),
        )

    return render_template(
        "comercializacao/preview.html",
        registro=registro, plano=plano, totais=plano_totais(plano), meta=meta,
        comandos=comandos, tmp=tmp, modo_api=usa_api(),
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
        return redirect(url_for("comercializacao.lista"))

    cred = MapaCredencial.query.filter_by(owner_user_id=current_user.id).first()
    if cred is None:
        flash("Cadastre suas credenciais do MAPA antes de executar (Configuracoes > Credenciais MAPA).", "warning")
        return redirect(url_for("public.credenciais_mapa"))

    tmp = _vendas_tmp(registro.id)
    records, _meta = _payload(tmp)
    if not records:
        flash("Dados insuficientes para finalizar.", "warning")
        return redirect(url_for("comercializacao.novo"))

    if ExecJob.query.filter(
        ExecJob.registro_id == registro.id, ExecJob.status.in_(["ESPERA", "EXECUTANDO"])
    ).first():
        flash("Ja existe um job ativo para este registro.", "warning")
        return redirect(url_for("comercializacao.lista"))

    tipo = _tipo_do_registro(registro)
    if tipo is None:
        flash("O tipo de lançamento deste registro não está ativo. Ative-o em Admin > Constantes.", "danger")
        return redirect(url_for("comercializacao.lista"))

    extra = {
        "modulo": TIPO,
        "lancamento": tipo.codigo,
        "periodo": {"ini": registro.periodo_ini.strftime("%d/%m/%Y"),
                    "fim": registro.periodo_fim.strftime("%d/%m/%Y")},
        "numero_sif": cred.numero_sif,
    }
    try:
        # ultima trava: sem coletor, qualquer termo sem vinculo (ou credencial incompleta) aborta
        if usa_api():
            prep = preparar_comercializacao(registro, records, tipo)
            job = create_api_job(registro, prep, plano_totais(prep["plano"]), gta_source=tipo.codigo, extra_meta=extra)
        else:
            plano = build_plano(records, backend="browser")
            job = ExecJob(
                registro_id=registro.id, owner_user_id=registro.user_id, gta_source=tipo.codigo,
                commands=build_commands(plano, tipo=tipo_config(tipo), rotulos=rotulos_ativos()),
                meta={**extra, "totais": plano_totais(plano)}, status="ESPERA", progress=0, errors=[],
            )
            db.session.add(job)
            registro.status = "PT"
    except ConstanteNaoMapeada as e:
        db.session.rollback()
        flash(
            f"Nao foi possivel finalizar: {escape(e.tipo)} sem vinculo ({escape(e.valor)}). "
            "Cadastre o vinculo em Admin > Constantes e valide novamente.",
            "danger",
        )
        return redirect(url_for("comercializacao.preview", registro_id=registro.id))
    except DadosIncompletos as e:
        db.session.rollback()
        flash(f"{escape(str(e))}. Complete em Configurações > Credenciais MAPA.", "danger")
        return redirect(url_for("comercializacao.preview", registro_id=registro.id))
    except NotImplementedError as e:
        db.session.rollback()
        flash(escape(str(e)), "warning")
        return redirect(url_for("comercializacao.lista"))

    if tmp is not None:
        tmp.status = UploadStatus.PROCESSED
    db.session.commit()
    get_queue().enqueue(job.id)
    log_action("job:create", entity="ExecJob", entity_id=job.id, meta={"modulo": TIPO})

    flash("Validacao confirmada. Job enfileirado para execucao.", "success")
    return redirect(url_for("comercializacao.lista"))
