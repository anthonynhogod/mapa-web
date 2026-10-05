"""Admin > API do MAPA: ambiente, catalogos do webservice e sincronizacao do De->Para."""
from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.mapa_api import sync
from app.mapa_api.client import CATALOGOS, ApiError
from app.mapa_api.montagem import cliente, usa_api
from app.models import EspecieApi, MapaCredencial
from app.security import admin_required
from app.utils.audit import log_action
from app.logic.constantes import invalidar_cache


def _cliente_do_admin():
    cred = MapaCredencial.query.filter_by(owner_user_id=current_user.id).first()
    if cred is None:
        flash("Cadastre suas Credenciais MAPA (Configurações) para consultar os catálogos.", "warning")
        return None
    return cliente(cred)


def register(bp):
    @bp.route("/api-mapa")
    @admin_required
    def api_mapa():
        return render_template(
            "admin/api_mapa.html",
            usa_api=usa_api(),
            ambiente=current_app.config.get("MAPA_API_AMBIENTE", "homologacao"),
            url=current_app.config.get("MAPA_API_URL"),
            especies=EspecieApi.query.order_by(EspecieApi.nome).all(),
            catalogos=list(CATALOGOS),
            relatorio=None,
        )

    @bp.route("/api-mapa/sincronizar", methods=["POST"])
    @admin_required
    def api_mapa_sincronizar():
        cli = _cliente_do_admin()
        if cli is None:
            return redirect(url_for("admin.api_mapa"))
        try:
            rel = sync.sincronizar(cli)
        except ApiError as e:
            db.session.rollback()
            flash(f"Falha ao consultar o webservice ({escape(cli.ambiente)}): {escape(str(e))}", "danger")
            return redirect(url_for("admin.api_mapa"))
        log_action("api:sincronizar", meta={"ambiente": cli.ambiente,
                                            "preenchidos": {k: len(v["preenchidos"]) for k, v in rel.items()}})
        return render_template(
            "admin/api_mapa.html",
            usa_api=usa_api(), ambiente=cli.ambiente, url=current_app.config.get("MAPA_API_URL"),
            especies=EspecieApi.query.order_by(EspecieApi.nome).all(),
            catalogos=list(CATALOGOS), relatorio=rel,
        )

    @bp.route("/api-mapa/catalogo/<nome>")
    @admin_required
    def api_mapa_catalogo(nome):
        if nome not in CATALOGOS:
            flash("Catálogo inexistente.", "warning")
            return redirect(url_for("admin.api_mapa"))
        cli = _cliente_do_admin()
        if cli is None:
            return redirect(url_for("admin.api_mapa"))
        try:
            dados = cli.catalogo(nome, usar_cache=False)
        except ApiError as e:
            flash(f"Falha ao consultar {escape(nome)}: {escape(str(e))}", "danger")
            return redirect(url_for("admin.api_mapa"))
        itens = dados if isinstance(dados, list) else [dados]
        linhas = []
        for it in itens:
            ident, nm = sync.extrair(it)
            linhas.append({"id": ident, "nome": nm, "cru": it})
        return render_template("admin/api_catalogo.html", nome=nome, linhas=linhas, ambiente=cli.ambiente)

    @bp.route("/api-mapa/especie/<int:item_id>", methods=["POST"])
    @admin_required
    def api_mapa_especie(item_id):
        e = db.session.get(EspecieApi, item_id)
        if not e:
            flash("Espécie não encontrada.", "warning")
            return redirect(url_for("admin.api_mapa"))
        raw = (request.form.get("id_api") or "").strip()
        if raw and (not raw.isdigit() or int(raw) <= 0):
            flash("Informe um inteiro maior que zero (ou deixe vazio).", "warning")
            return redirect(url_for("admin.api_mapa"))
        e.id_api = int(raw) if raw else None
        db.session.commit()
        invalidar_cache("especie")
        log_action("constante:update", entity="EspecieApi", entity_id=e.id, meta={"nome": e.nome, "id_api": e.id_api})
        flash("Espécie salva.", "success")
        return redirect(url_for("admin.api_mapa"))
