# app/blueprints/api/routes.py
from flask import Blueprint, request, jsonify, current_app, abort
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from datetime import datetime, date
from app import db
from app.models import Registro, GtaTemp, DifTmp, SifTmp, UploadStatus
from .auth import bearer_or_login_required, token_required

import logging
logging.basicConfig(level=logging.DEBUG)

bp = Blueprint("api", __name__)

# ---------- helpers ----------
def _json_error(message, status=400, **extra):
    payload = {"error": message, "status": status} | extra
    return jsonify(payload), status

def _parse_date(value):
    if value is None: return None
    if isinstance(value, date): return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except Exception:
            pass
    raise ValueError("Data inválida. Use YYYY-MM-DD ou DD/MM/YYYY.")

def serialize_registro(r: Registro) -> dict:
    return {
        "id": r.id,
        "data": r.data.isoformat() if r.data else None,
        "status": r.status,
        "especie": r.especie,
        "obs": r.obs,
        "user_id": r.user_id,
        "recurso": getattr(r, "recurso", None),
        "data_registro": r.data_registro.isoformat() if r.data_registro else None,
        "data_inicio": r.data_inicio.isoformat() if r.data_inicio else None,
        "data_fim": r.data_fim.isoformat() if r.data_fim else None,
    }

def serialize_gta(g: GtaTemp) -> dict:
    return {
        "id": g.id,
        "registro_id": g.registro_id,
        "numero": g.numero,
        "serie": g.serie,
        "machos": g.machos,
        "femeas": g.femeas,
        "lote": g.lote,
        "peso": g.peso,
        "tipo": g.tipo,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }

# ============== REGISTROS ==============

@bp.get("/registros")
@bearer_or_login_required()
def list_registros():
    q = Registro.query

    try:
        if getattr(request, "auth", {}).get("method") == "session":
            if not getattr(current_user, "is_admin", False):
                q = q.filter(Registro.user_id == current_user.id)
    except Exception:
        pass

    status = request.args.get("status")
    especie = request.args.get("especie")
    from_ = request.args.get("from")
    to_ = request.args.get("to")

    if status:
        q = q.filter(Registro.status == status)
    if especie:
        q = q.filter(Registro.especie.ilike(f"%{especie}%"))
    if from_:
        try:
            d = _parse_date(from_)
            q = q.filter(Registro.data >= d)
        except ValueError as e:
            return _json_error(str(e), 400)
    if to_:
        try:
            d = _parse_date(to_)
            q = q.filter(Registro.data <= d)
        except ValueError as e:
            return _json_error(str(e), 400)

    page = int(request.args.get("page", 1))
    size = max(1, min(100, int(request.args.get("page_size", 20))))
    pagination = q.order_by(Registro.data.desc(), Registro.id.desc()).paginate(
        page=page, per_page=size, error_out=False
    )

    return jsonify({
        "items": [serialize_registro(r) for r in pagination.items],
        "page": page,
        "page_size": size,
        "total": pagination.total,
        "pages": pagination.pages,
    })

@bp.get("/registros/<int:registro_id>")
def get_registro(registro_id: int):
    r = db.session.get(Registro, registro_id)
    if not r:
        return _json_error("Registro não encontrado.", 404)
    return jsonify(serialize_registro(r))

@bp.post("/registros")
@bearer_or_login_required()
def create_registro():
    data = request.get_json(silent=True) or {}

    # user_id detectado automaticamente
    if request.auth["method"] == "session":
        user_id = current_user.id
    else:
        user_id = request.auth.get("sub")

    try:
        r = Registro(
            data=_parse_date(data.get("data")) or date.today(),
            status=data.get("status") or "AT",
            especie=(data.get("especie") or "").strip(),
            obs=data.get("obs"),
            user_id=user_id,
            recurso="api",
        )

        db.session.add(r)
        db.session.commit()
        return jsonify(serialize_registro(r)), 201

    except (IntegrityError) as e:
        db.session.rollback()
        return _json_error("Violação de integridade.", 409, detail=str(e.orig) if getattr(e, "orig", None) else None)
    except ValueError as e:
        return _json_error(str(e), 400)

@bp.patch("/registros/<int:registro_id>")
@bearer_or_login_required()
def update_registro(registro_id: int):
    r = db.session.get(Registro, registro_id)
    if not r:
        return _json_error("Registro não encontrado.", 404)
    data = request.get_json(silent=True) or {}

    # atualizar marcação
    r.recurso = "api"

    try:
        if "data" in data: r.data = _parse_date(data.get("data"))
        if "status" in data: r.status = data.get("status") or r.status
        if "especie" in data: r.especie = (data.get("especie") or "").strip()
        if "obs" in data: r.obs = data.get("obs")

        db.session.commit()
        return jsonify(serialize_registro(r))

    except (IntegrityError) as e:
        db.session.rollback()
        return _json_error("Violação de integridade.", 409, detail=str(e.orig) if getattr(e, "orig", None) else None)
    except ValueError as e:
        return _json_error(str(e), 400)

@bp.delete("/registros/<int:registro_id>")
def delete_registro(registro_id: int):
    r = db.session.get(Registro, registro_id)
    if not r:
        return _json_error("Registro não encontrado.", 404)
    db.session.delete(r)
    db.session.commit()
    return "", 204

# ============== GTAs (bulk) ==============

@bp.get("/registros/<int:registro_id>/gtas")
def list_gta_by_registro(registro_id: int):
    q = GtaTemp.query.filter_by(registro_id=registro_id)
    page = int(request.args.get("page", 1))
    size = max(1, min(100, int(request.args.get("page_size", 50))))
    pagination = q.order_by(GtaTemp.id.asc()).paginate(page=page, per_page=size, error_out=False)
    return jsonify({
        "items": [serialize_gta(g) for g in pagination.items],
        "page": page, "page_size": size, "total": pagination.total, "pages": pagination.pages,
    })

@bp.post("/registros/<int:registro_id>/gtas:bulk")
@token_required(require_scopes=["gta:write"])
def bulk_insert_gta(registro_id: int):
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    if not items:
        return _json_error("Nenhuma linha enviada.", 400)

    objs = []
    for i, r in enumerate(items, start=1):
        try:
            obj = GtaTemp(
                registro_id=registro_id,
                numero=int(r["numero"]),
                serie=str(r["serie"]).strip(),
                machos=int(r.get("machos") or 0),
                femeas=int(r.get("femeas") or 0),
                lote=int(r["lote"]) if r.get("lote") not in (None, "") else None,
                peso=float(r["peso"]) if r.get("peso") not in (None, "") else None,
                tipo=(r.get("tipo") or "A"),
            )
            objs.append(obj)
        except Exception as e:
            return _json_error(f"Linha {i} inválida: {e}", 422)

    try:
        db.session.bulk_save_objects(objs)
        db.session.commit()
        return jsonify({"inserted": len(objs)}), 201
    except IntegrityError as e:
        db.session.rollback()
        return _json_error("Falha de integridade (duplicado ou FK).", 409, detail=str(e.orig) if getattr(e, "orig", None) else None)

@bp.delete("/gtas/<int:gta_id>")
def delete_gta(gta_id: int):
    g = db.session.get(GtaTemp, gta_id)
    if not g:
        return _json_error("GTA não encontrada.", 404)
    db.session.delete(g)
    db.session.commit()
    return "", 204

# ============== DIF ==============

@bp.get("/registros/<int:registro_id>/dif-tmp")
def get_dif_tmp(registro_id: int):
    rec = (DifTmp.query
           .filter_by(registro_id=registro_id)
           .order_by(DifTmp.created_at.desc())
           .first())
    if not rec:
        return _json_error("DIF temporário não encontrado para este registro.", 404)
    return jsonify({
        "id": rec.id,
        "filename": rec.filename,
        "model": rec.model,
        "status": rec.status,
        "payload": rec.payload,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    })
    
@bp.post("/consultar-dia")
def consultar_dia():
    try:
        data = request.get_json(silent=True)
        current_app.logger.debug("Dados recebidos: %s", data)

        if not data:
            return jsonify({"status": "erro", "mensagem": "JSON inválido ou vazio."}), 400

        dia = data.get("dia")
        especie = data.get("especie")
        obs = data.get("obs")

        if not dia or not especie:
            return jsonify({
                "status": "erro",
                "mensagem": "Todos os campos devem ser preenchidos."
            }), 400

        return jsonify({"status": "ok", "mensagem": "Dia validado com sucesso."}), 200

    except Exception:
        current_app.logger.exception("Erro ao processar /consultar-dia")
        return jsonify({"status": "erro", "mensagem": "Erro interno ao processar a solicitação."}), 500


@bp.post("/registros/<int:registro_id>/dif-tmp")
@bearer_or_login_required()
def upsert_dif_tmp(registro_id: int):
    body = request.get_json(silent=True) or {}
    if not body:
        return _json_error("Payload vazio.", 400)

    if request.auth["method"] == "session":
        user_id = current_user.id
    else:
        user_id = request.auth.get("sub")

    rec = DifTmp(
        registro_id=registro_id,
        filename=body.get("filename") or "api.json",
        model=body.get("model") or "api",
        payload=body,
        status=UploadStatus.VALIDATED,
        uploaded_by=user_id,
        recurso="api",
    )
    db.session.add(rec)
    db.session.commit()
    return jsonify({"id": rec.id}), 201

# ============== SIF ==============

@bp.get("/registros/<int:registro_id>/sif-tmp")
def get_sif_tmp(registro_id: int):
    rec = (SifTmp.query
           .filter_by(registro_id=registro_id)
           .order_by(SifTmp.created_at.desc())
           .first())
    if not rec:
        return _json_error("SIF temporário não encontrado para este registro.", 404)
    return jsonify({
        "id": rec.id,
        "filename": rec.filename,
        "model": rec.model,
        "status": rec.status,
        "payload": rec.payload,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    })

@bp.post("/registros/<int:registro_id>/sif-tmp")
@bearer_or_login_required()
def upsert_sif_tmp(registro_id: int):
    body = request.get_json(silent=True) or {}
    if not body:
        return _json_error("Payload vazio.", 400)

    if request.auth["method"] == "session":
        user_id = current_user.id
    else:
        user_id = request.auth.get("sub")

    rec = SifTmp(
        registro_id=registro_id,
        filename=body.get("filename") or "api.json",
        model=body.get("model") or "api",
        payload=body,
        status=UploadStatus.VALIDATED,
        uploaded_by=user_id,
        recurso="api",
    )
    db.session.add(rec)
    db.session.commit()
    return jsonify({"id": rec.id}), 201

# ============== error handlers ==============

@bp.errorhandler(404)
def handle_404(e):
    current_app.logger.warning("404: %s", e)
    return jsonify({"status": "erro", "mensagem": "Rota não encontrada."}), 404

@bp.errorhandler(500)
def handle_500(e):
    current_app.logger.exception("500: %s", e)
    return jsonify({"status": "erro", "mensagem": "Erro interno no servidor."}), 500
