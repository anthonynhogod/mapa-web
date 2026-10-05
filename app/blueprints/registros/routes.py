from app.extensions import db
from app.models import Registro, UploadStatus, get_last_register_by_id, get_all_register_by_id, DifTmp, SifTmp, GtaUploadTmp, GtaTemp
from .dto import DadosGTA
from .services import incluir_gta_no_registro
from app.forms import UploadGtaForm, UploadDifForm, UploadSifForm
import math
from types import SimpleNamespace as NS

from flask import Blueprint, render_template, request, redirect, url_for, abort, flash
from flask_login import login_required, current_user
from datetime import datetime
from app.logic.preview_utils import (
    normalize_gta_manual as _normalize_gta_manual,
    merge_diagnostics    as _merge_diagnostics,
    build_legacy_structure_from_new as _build_legacy_structure_from_new,
    build_commands       as _build_commands,
    lotes_from           as _lotes_from,
)

from app.utils.format import normalize_str
from markupsafe import escape
from app.mapa_api.montagem import json_legivel, preparar_abate, usa_api
from app.mapa_api.payloads import DadosIncompletos
# >>> Service sem importar nada de 'routes':
from app.executor.service import create_job_and_finalize

# >>> Para a finalização:
from app.models import ExecJob, GtaTemp   # ajuste se GtaTemp estiver em outro módulo
from app.executor.queue import get_queue
# Paginador
from app.utils.pagination import PER_PAGE, resolve_page, paginate_query, render_paginator_text, paginate_list

import time as _time


bp = Blueprint("registros", __name__)

def _row_is_valid_temp(row) -> bool:
    """Heurística de 'validado' para uploads temporários."""
    if not row:
        return False
    # 1) Se o Enum/status já marca como VALIDATED → ok
    try:
        if getattr(row, "status", None) == UploadStatus.VALIDATED:
            return True
    except Exception:
        pass
    # 2) Caso contrário, usa meta dos validadores (payload com wrap=True)
    try:
        payload = row.payload or {}
        meta = payload.get("meta", {})
        errors = meta.get("errors", [])
        counts = meta.get("counts", {})
        out = int(counts.get("output_records", 0))
        # Considera válido se não há errors e há registros de saída
        return out > 0 and (len(errors) == 0)
    except Exception:
        return False

def _exists_manual(model_cls, registro_id: int) -> bool:
    """Presença de dados permanentes (manual)."""
    try:
        return db.session.query(model_cls.id).filter_by(registro_id=registro_id).first() is not None
    except Exception:
        return False

def _get_temp_rows(registro_id: int):
    dif_tmp = DifTmp.query.filter_by(registro_id=registro_id).one_or_none()
    sif_tmp = SifTmp.query.filter_by(registro_id=registro_id).one_or_none()
    gta_tmp = GtaUploadTmp.query.filter_by(registro_id=registro_id).one_or_none()
    return gta_tmp, dif_tmp, sif_tmp

def _all_ready(registro_id: int) -> bool:
    gta_tmp, dif_tmp, sif_tmp = _get_temp_rows(registro_id)
    gta_ready = _row_is_valid_temp(gta_tmp) or _exists_manual(GtaTemp, registro_id)
    dif_ready = _row_is_valid_temp(dif_tmp)
    sif_ready = _row_is_valid_temp(sif_tmp)
    return bool(gta_ready and dif_ready and sif_ready)

def _payload_and_meta(tmp_row):
    """Extrai (records, meta) de um upload temporário; fornece meta vazia quando não há."""
    if not tmp_row:
        return [], {"errors": [], "warnings": [], "counts": {}}
    payload = tmp_row.payload or {}
    return payload.get("records", []), payload.get("meta", {"errors": [], "warnings": [], "counts": {}})


@bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    if request.method == "POST":
        dia = request.form.get("dia")
        especie = request.form.get("especie")
        obs = request.form.get("obs")
        
        try:
            data_obj = datetime.strptime(dia, "%Y-%m-%d").date()
        except ValueError:
            flash("Data inválida.", "error")
            return redirect(url_for("registros.novo"))

        if Registro.query.filter_by(data=dia, especie=especie, user_id=current_user.id, tipo="abate").first():
            flash("Já existe um registro para esse dia e espécie!", "error")
            return redirect(url_for("registros.novo"))

        novo_registro = Registro(
            data=data_obj,
            especie=especie,
            obs=obs,
            user_id=current_user.id,
            data_registro=datetime.now()
        )
        db.session.add(novo_registro)
        db.session.commit()

        flash("Registro criado com sucesso!", "success")
        #return redirect(url_for("registro_visualizar", registro_id=novo_registro.id)) considerar isso para dinamizar o fluxo
        return redirect(url_for("registros.incluir_dados", registro_id=novo_registro.id))
    
    return render_template("registros/novo.html")

@bp.route("/prontos", methods=["GET"])
@login_required
def prontos():
    # comercializacao tem lista propria (comercializacao.lista)
    query = (Registro.query
             .filter_by(user_id=current_user.id, tipo="abate")
             .order_by(Registro.data.desc()))

    page = resolve_page(default=1)
    pagination = paginate_query(query, page, db, per_page=PER_PAGE)
    
    return render_template(
        "registros/prontos.html",
        registros=pagination.items,
        pagination=pagination,
    )


@bp.route("/<int:registro_id>", methods=["GET"])
@login_required
def detalhe(registro_id: int):
    registro = get_last_register_by_id(registro_id, current_user.id)
    return render_template("registros/detalhe.html", registro=registro)

STATUS_LABELS = {'AT': 'Em andamento', 'PT': 'Pendente', 'FZ': 'Finalizado', 'ER': 'Erro'}
STATUS_BADGES = {'AT': 'info', 'PT': 'warning', 'FZ': 'success', 'ER': 'danger'}

@bp.route("/historico", methods=["GET"])
@login_required
def historico():
    # Query base: Todos os registros do usuário (sem join)
    q = Registro.query.filter(Registro.user_id == current_user.id)
    
    # Ordenação por data descendente (mais recentes primeiro)
    q = q.order_by(Registro.data.desc())
    
    status = request.args.get("status", type=str)
    especie = request.args.get("especie", type=str)
    data_de = request.args.get("de", type=str)   # formato YYYY-MM-DD
    data_ate = request.args.get("ate", type=str) # formato YYYY-MM-DD
    
    if status:
        q = q.filter(Registro.status == status)
    if especie:
        q = q.filter(Registro.especie.ilike(f"%{especie}%"))
    if data_de:
        q = q.filter(Registro.data >= data_de)
    if data_ate:
        q = q.filter(Registro.data <= data_ate)
    
    page = request.args.get("page", 1, type=int)
    pagination = paginate_query(q, page, db, per_page=PER_PAGE)  # Certifique-se de que PER_PAGE é >1 (e.g., 10)
    
    return render_template(
        "registros/historico.html",
        registros=pagination.items,  # Lista de objetos Registro
        pagination=pagination,
        STATUS_LABELS=STATUS_LABELS,
        filtros={
            "status": status or "",
            "especie": especie or "",
            "de": data_de or "",
            "ate": data_ate or "",
        },
    )

@bp.route("/<int:registro_id>/dados", methods=["GET"])
@login_required
def incluir_dados(registro_id):
    registro = get_last_register_by_id(registro_id, current_user.id)

    # Uploads temporários
    current_dif = DifTmp.query.filter_by(registro_id=registro.id).one_or_none()
    current_sif = SifTmp.query.filter_by(registro_id=registro.id).one_or_none()
    current_gta = GtaUploadTmp.query.filter_by(registro_id=registro.id).one_or_none()

    # Presença manual (GTA manual via GtaTemp; se tiver Dif/Sif permanentes, libere aqui também)
    gta_has_manual = _exists_manual(GtaTemp, registro.id)
    # dif_has_manual = _exists_manual(Dif, registro.id)   # se existir seu modelo Dif permanente
    # sif_has_manual = _exists_manual(Sif, registro.id)   # idem para Sif

    # “Pronto” quando há upload válido ou dados manuais
    gta_ready = _row_is_valid_temp(current_gta) or gta_has_manual
    dif_ready = _row_is_valid_temp(current_dif)  # or dif_has_manual
    sif_ready = _row_is_valid_temp(current_sif)  # or sif_has_manual

    can_validate = bool(gta_ready and dif_ready and sif_ready)

    gta_form = UploadGtaForm()
    dif_form = UploadDifForm()
    sif_form = UploadSifForm()

    return render_template(
        "registros/dados/geral.html",
        registro=registro,
        dif_has_temp = bool(current_dif),
        sif_has_temp = bool(current_sif),
        gta_has_temp = bool(current_gta),
        gta_ready=gta_ready,  # (se quiser exibir indicadores por seção)
        dif_ready=dif_ready,
        sif_ready=sif_ready,
        can_validate=can_validate,
        gta_form=gta_form,
        dif_form=dif_form,
        sif_form=sif_form,
    )

@bp.route("/<int:registro_id>/dados/gta", methods=["GET", "POST"])
@login_required
def incluir_gta(registro_id: int):
    registro = get_last_register_by_id(registro_id, current_user.id)

    if registro.status != "AT":
        abort(400, description="Registro não está em aberto para inclusão de dados.")

    if request.method == "POST":
        dados, erros = DadosGTA.from_request(request)
        if erros:
            for e in erros:
                flash(e, "error")
            return render_template("registros/dados/gta.html", registro=registro), 400

        ok, msg, cat = incluir_gta_no_registro(registro, dados)
        print(ok, msg, cat)
        flash(msg, cat)
        if ok:
            return redirect(url_for("registros.incluir_gta", registro_id=registro.id))
        return render_template("registros/dados/gta.html", registro=registro), 400

    return render_template("registros/dados/gta.html", registro=registro)
    
@bp.route("/<int:registro_id>/excluir", methods=["POST"])
@login_required
def excluir(registro_id):
    registro = get_last_register_by_id(registro_id, current_user.id)

    if request.form.get("confirm") != "yes":
        # Se alguém postar sem confirmar, apenas recusa
        flash("Confirmação ausente. Exclusão cancelada.", "warning")
        return redirect(url_for("registros.prontos"))

    voltar = "comercializacao.lista" if registro.tipo == "comercializacao" else "registros.prontos"
    db.session.delete(registro)
    db.session.commit()
    flash("Registro excluído.", "success")
    return redirect(url_for(voltar))


@bp.route("/<int:registro_id>/preview/geral", methods=["GET", "POST"])
@login_required
def preview_registro(registro_id):
    _t0 = _time.perf_counter()
    """
    Etapas:
      1) Seleção da fonte GTA (upload x manual) → POST
      2) Divergência de lotes (GTA x DIF x SIF) → POST de confirmação
      3) Preview final (tabelas + comandos), com botão "Confirmar & Executar"
    """
    registro = get_last_register_by_id(registro_id, current_user.id)
    if not registro:
        abort(404)

    # Uploads temporários
    gta_tmp, dif_tmp, sif_tmp = _get_temp_rows(registro.id)

    # DIF / SIF (records + meta)
    dif_records, dif_meta = _payload_and_meta(dif_tmp)
    sif_records, sif_meta = _payload_and_meta(sif_tmp)

    # GTA: upload válido? Existem dados manuais?
    gta_upload_valid = _row_is_valid_temp(gta_tmp)
    gta_manual_qs = GtaTemp.query.filter_by(registro_id=registro.id).all()
    gta_manual_records = _normalize_gta_manual(gta_manual_qs)

    # Há duas fontes simultâneas?
    both_gta = bool(gta_upload_valid and len(gta_manual_records) > 0)
    gta_source = (request.values.get("gta_source") or "").strip()

    # 1) Seleção da fonte GTA
    if both_gta and not gta_source:
        meta_upload = gta_tmp.payload.get("meta", {"counts": {"output_records": 0}}) if gta_tmp else {"counts": {"output_records": 0}}
        meta_manual = {"counts": {"output_records": len(gta_manual_records)}}
        return render_template(
            "registros/preview/gta-usada.html",
            registro=registro,
            meta_upload=meta_upload,
            meta_manual=meta_manual,
            manual_count=len(gta_manual_records),
        )

    # Define gta_records conforme escolha/disponibilidade
    if gta_source == "manual" or (not gta_upload_valid and len(gta_manual_records) > 0):
        gta_records = gta_manual_records
        chosen_source = "manual"
        gta_meta = {"counts": {"output_records": len(gta_records)}, "errors": [], "warnings": []}
    else:
        gta_records, gta_meta = _payload_and_meta(gta_tmp)
        chosen_source = "upload"

    # Presença mínima
    if not gta_records or not dif_records or not sif_records:
        flash("Ainda faltam dados válidos de GTA, DIF ou SIF para validar o registro.", "warning")
        return redirect(url_for("registros.incluir_dados", registro_id=registro.id))

    # 2) Divergência de lotes (somente se houver)
    lotes = {
        "gta": _lotes_from(gta_records),
        "dif": _lotes_from(dif_records),
        "sif": _lotes_from(sif_records),
    }
    ok_lotes = (lotes["gta"] == lotes["dif"] == lotes["sif"])

    if not ok_lotes and request.values.get("confirm_mismatch") != "yes":
        diff = {
            "only_gta": sorted(set(lotes["gta"]) - set(lotes["dif"]) - set(lotes["sif"])),
            "only_dif": sorted(set(lotes["dif"]) - set(lotes["gta"]) - set(lotes["sif"])),
            "only_sif": sorted(set(lotes["sif"]) - set(lotes["gta"]) - set(lotes["dif"])),
        }
        return render_template(
            "registros/preview/divergencias.html",
            registro=registro,
            lotes=lotes,
            diff=diff,
            gta_source=chosen_source,
        )

    mismatch = not ok_lotes
    diff = {"only_gta": [], "only_dif": [], "only_sif": []}

    # 3) Preview final (gera comandos)
    from app.logic.constantes import ColetorPendencias

    # Constantes sem vínculo cadastrado BLOQUEIAM o registro. Nada é enviado ao
    # MAPA "no melhor esforço": ou todo diagnóstico/parte/destino/espécie tem vínculo
    # (e o estabelecimento está completo), ou o dia não é válido.
    coletor = ColetorPendencias()
    avisos_api = []
    if usa_api():
        prep = preparar_abate(registro, gta_records, dif_records, sif_records, coletor)
        comandos = [f"{prep['metodo']} /abate  (Content-Type: application/json)"] + json_legivel(prep["payload"])
        avisos_api = prep["avisos"]
    else:
        merged_diag = _merge_diagnostics(dif_records, sif_records)
        estrutura_lotes = _build_legacy_structure_from_new(gta_records, merged_diag, uf_index_default=23)
        comandos = _build_commands(estrutura_lotes, coletor=coletor)

    if not coletor.vazio:
        return render_template(
            "registros/preview/pendencias.html",
            registro=registro,
            pendencias=coletor.listar(),
            gta_source=chosen_source,
        )

    totais = {
        "gta_animais": sum(int(r.get("total", 0) or 0) for r in gta_records),
        "dif_itens": len(dif_records),
        "sif_itens": len(sif_records),
    }
    print(f"[PREVIEW] registro={registro.id} tempo_view={_time.perf_counter()-_t0:.3f}s", flush=True)
    return render_template(
        "registros/preview/geral.html",
        registro=registro,
        gta_source=chosen_source,
        gta_records=gta_records,
        dif_records=dif_records,
        sif_records=sif_records,
        totais=totais,
        mismatch=mismatch,
        comandos=comandos,
        diff=diff,  # sempre definido
        modo_api=usa_api(),
        avisos_api=avisos_api,
    )


# =======================
# PREVIEW GTA 
# =======================
@bp.get("/<int:registro_id>/preview/gta")
@login_required
def preview_gta(registro_id):
    _t0 = _time.perf_counter()
    registro = get_last_register_by_id(registro_id, current_user.id)
    if not registro:
        abort(404)

    gta_tmp = GtaUploadTmp.query.filter_by(registro_id=registro.id).one_or_none()
    if _row_is_valid_temp(gta_tmp):
        gta_records, gta_meta = _payload_and_meta(gta_tmp)
        source = "upload"
    else:
        # Fallback para dados manuais de GTA
        gtas = GtaTemp.query.filter_by(registro_id=registro.id).all()
        gta_records = [
            {
                "data": getattr(g, "data", None),
                "numero_gta": g.numero,
                "serie": g.serie,
                "machos": g.machos,
                "femeas": g.femeas,
                "total": int((g.machos or 0) + (g.femeas or 0)),
                "lote": g.lote,
                "peso_medio": g.peso,
            }
            for g in gtas
        ]
        gta_meta = {"errors": [], "warnings": [], "counts": {"output_records": len(gta_records)}}
        source = "manual"

    # Resumo leve
    total_animais = sum(int(r.get("total", 0) or 0) for r in gta_records)
    machos_total = sum(int(r.get("machos", 0) or 0) for r in gta_records)
    femeas_total = sum(int(r.get("femeas", 0) or 0) for r in gta_records)
    resumo = {
        "total_animais": total_animais,
        "machos_total": machos_total,
        "femeas_total": femeas_total,
    }

    page = resolve_page(default=1)
    pagination = paginate_list(gta_records, page, per_page=PER_PAGE)
    print(f"[PREVIEW] registro={registro.id} tempo_view={_time.perf_counter()-_t0:.3f}s", flush=True)
    return render_template(
        "registros/preview/gta.html",
        registro=registro,
        source=source,
        meta=gta_meta,
        resumo=resumo,
        records=pagination.items,
        pagination=pagination,
    )


# =======================
# PREVIEW DIF 
# =======================
@bp.get("/<int:registro_id>/preview/dif")
@login_required
def preview_dif(registro_id):
    _t0 = _time.perf_counter()
    registro = get_last_register_by_id(registro_id, current_user.id)
    if not registro:
        abort(404)

    dif_tmp = DifTmp.query.filter_by(registro_id=registro.id).one_or_none()
    if not _row_is_valid_temp(dif_tmp):
        flash("Nenhum DIF válido encontrado para este registro.", "warning")
        return redirect(url_for("registros.incluir_dados", registro_id=registro.id))

    dif_records, dif_meta = _payload_and_meta(dif_tmp)

    # Resumos
    total_itens = len(dif_records)
    por_destino = {}
    por_parte = {}
    for r in dif_records:
        por_destino[r.get("destino") or "—"] = por_destino.get(r.get("destino") or "—", 0) + 1
        p = r.get("parte afetada") or r.get("parte") or "—"
        por_parte[p] = por_parte.get(p, 0) + 1

    resumo = {
        "total_itens": total_itens,
        "por_destino": sorted(por_destino.items(), key=lambda x: (-x[1], x[0])),
        "top_partes": sorted(por_parte.items(), key=lambda x: (-x[1], x[0]))[:8],
    }

    page = resolve_page(default=1)
    pagination = paginate_list(dif_records, page, per_page=PER_PAGE)
    print(f"[PREVIEW] registro={registro.id} tempo_view={_time.perf_counter()-_t0:.3f}s", flush=True)
    return render_template(
        "registros/preview/dif.html",
        registro=registro,
        source="upload",
        meta=dif_meta,
        resumo=resumo,
        total=len(dif_records),
        records=pagination.items,
        pagination=pagination,
    )


# =======================
# PREVIEW SIF 
# =======================
@bp.get("/<int:registro_id>/preview/sif")
@login_required
def preview_sif(registro_id):
    _t0 = _time.perf_counter()
    registro = get_last_register_by_id(registro_id, current_user.id)
    if not registro:
        abort(404)
    
    sif_tmp = SifTmp.query.filter_by(registro_id=registro.id).one_or_none()
    if not _row_is_valid_temp(sif_tmp):
        flash("Nenhum SIF válido encontrado para este registro.", "warning")
        return redirect(url_for("registros.incluir_dados", registro_id=registro.id))

    sif_records, sif_meta = _payload_and_meta(sif_tmp)

    # Resumos
    total_itens = len(sif_records)
    por_destino = {}
    por_parte = {}
    for r in sif_records:
        por_destino[r.get("destino") or "—"] = por_destino.get(r.get("destino") or "—", 0) + 1
        p = r.get("parte afetada") or r.get("parte") or "—"
        por_parte[p] = por_parte.get(p, 0) + 1

    resumo = {
        "total_itens": total_itens,
        "por_destino": sorted(por_destino.items(), key=lambda x: (-x[1], x[0])),
        "top_partes": sorted(por_parte.items(), key=lambda x: (-x[1], x[0]))[:8],
    }

    page = resolve_page(default=1)
    pagination = paginate_list(sif_records, page, per_page=PER_PAGE)
    print(f"[PREVIEW] registro={registro.id} tempo_view={_time.perf_counter()-_t0:.3f}s", flush=True)
    return render_template(
        "registros/preview/sif.html",
        registro=registro,
        source="upload",
        meta=sif_meta,
        resumo=resumo,
        total=len(sif_records),
        records=pagination.items,
        pagination=pagination,
    )

@bp.post("/<int:registro_id>/finalizar")
@login_required
def finalizar_validacao(registro_id: int):
    registro = get_last_register_by_id(registro_id, current_user.id)
    if not registro:
        abort(404)
    if getattr(registro, "status", "AT") != "AT":
        flash("Registro não está em aberto para finalização.", "warning")
        return redirect(url_for("registros.detalhe", registro_id=registro.id))

    gta_source = (request.form.get("gta_source") or "upload").strip()

    # Reconstrói datasets (mesma lógica do preview)
    gta_tmp, dif_tmp, sif_tmp = _get_temp_rows(registro.id)
    dif_records, _ = _payload_and_meta(dif_tmp)
    sif_records, _ = _payload_and_meta(sif_tmp)

    if gta_source == "manual":
        gtas_man = GtaTemp.query.filter_by(registro_id=registro.id).all()
        gta_records = _normalize_gta_manual(gtas_man)
    else:
        gta_records, _ = _payload_and_meta(gta_tmp)

    # Presença mínima
    if not gta_records or not dif_records or not sif_records:
        flash("Dados insuficientes para finalizar.", "warning")
        return redirect(url_for("registros.incluir_dados", registro_id=registro.id))

    totais = {
        "gta_animais": sum(int(r.get("total", 0) or 0) for r in gta_records),
        "dif_itens": len(dif_records),
        "sif_itens": len(sif_records),
    }

    # Idempotência: evita duplicar job ativo
    exists_active = ExecJob.query.filter(
        ExecJob.registro_id == registro.id,
        ExecJob.status.in_(["ESPERA", "EXECUTANDO"])
    ).first()
    if exists_active:
        flash("Já existe um job ativo para este registro.", "warning")
        return redirect(url_for("registros.prontos", registro_id=registro.id))

    # Cria ExecJob, seta PT, limpa tmp
    from app.logic.constantes import ConstanteNaoMapeada
    try:
        job = create_job_and_finalize(
            registro=registro,
            gta_source=gta_source,
            gta_records=gta_records,
            dif_records=dif_records,
            sif_records=sif_records,
            totais=totais
        )
    except ConstanteNaoMapeada as e:
        db.session.rollback()
        flash(
            f"Não foi possível finalizar: {escape(e.tipo)} sem vínculo no sistema do MAPA "
            f"({escape(e.valor)}). Cadastre o vínculo em Admin > Constantes e valide novamente.",
            "danger",
        )
        return redirect(url_for("registros.preview_registro", registro_id=registro.id))
    except DadosIncompletos as e:
        db.session.rollback()
        flash(f"{escape(str(e))}. Complete em Configurações > Credenciais MAPA.", "danger")
        return redirect(url_for("registros.preview_registro", registro_id=registro.id))

    # Enfileira para os workers
    get_queue().enqueue(job.id)

    flash("Validação confirmada. Job enfileirado para execução.", "success")
    return redirect(url_for("registros.prontos", registro_id=registro.id))