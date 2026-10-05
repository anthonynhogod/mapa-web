# routes.py
from datetime import date, timedelta
from sqlalchemy import func
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import MapaCredencial, Registro, GtaTemp, ExecJob  # <— adicionamos Registro e GtaTemp
from app.security import scope_by_user

STATUS_LABELS = {'AT': 'Em andamento', 'PT': 'Pendente', 'FZ': 'Finalizado', 'ER': 'Erro'}
STATUS_BADGES = {'AT': 'info', 'PT': 'warning', 'FZ': 'success', 'ER': 'danger'}

def get_status_counts(user_id: int):
    rows = (
        db.session.query(Registro.status, func.count(Registro.id))
        .filter(Registro.user_id == user_id)
        .group_by(Registro.status)
        .all()
    )
    counts = {'AT': 0, 'PT': 0, 'FZ': 0, 'ER': 0}
    counts.update({status: total for status, total in rows})
    return counts

bp = Blueprint("public", __name__)

@bp.route("/", methods=["GET", "POST"])
def index():
    return render_template("index.html")

@bp.route("/sobre", methods=["GET", "POST"])
def sobre():
    return render_template("sobre.html")

@bp.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    return render_template("dashboard.html")

def _date_range_labels(days=30):
    today = date.today()
    start = today - timedelta(days=days-1)
    labels = [(start + timedelta(d)).isoformat() for d in range(days)]
    return start, labels

@bp.route("/dashboard-data", methods=["GET"])
@login_required
def dashboard_data():
    # Totais por status
    totals = get_status_counts(current_user.id)

    # Série diária (últimos 30 dias) por status
    start, labels = _date_range_labels(30)
    rows = (
        db.session.query(Registro.data_fim, Registro.status, func.count(Registro.id))
        .filter(
            Registro.user_id == current_user.id,
            Registro.data_fim >= start
        )
        .group_by(Registro.data_fim, Registro.status)
        .order_by(Registro.data_fim)
        .all()
    )
    
    series = {s: {lbl: 0 for lbl in labels} for s in ['AT', 'PT', 'FZ', 'ER']}
    for d, s, c in rows:
        d = d.date()
        key = d.isoformat()
        if key in series.get(s, {}):
            series[s][key] = c
    timeseries = {
        "labels": labels,
        "AT": [series['AT'][k] for k in labels],
        "PT": [series['PT'][k] for k in labels],
        "FZ": [series['FZ'][k] for k in labels],
        "ER": [series['ER'][k] for k in labels],
    }

    # Distribuição por espécie (últimos 30 dias)
    by_species = (
        db.session.query(Registro.especie, func.count(Registro.id))
        .filter(Registro.user_id == current_user.id, Registro.data >= start)
        .group_by(Registro.especie)
        .order_by(func.count(Registro.id).desc())
        .all()
    )
    species = [{"especie": e or "—", "count": n} for e, n in by_species]

    return jsonify({
        "status_totals": totals,
        "timeseries": timeseries,
        "by_species": species,
        "labels": STATUS_LABELS
    })


@bp.route("/dados-api", methods=["GET", "POST"])
@login_required
def dados_api():
    return render_template("dados-api.html")

@bp.route('/main', methods=['GET', 'POST'])
@login_required
def main():
    registros = Registro.query.filter_by(user_id=current_user.id).order_by(Registro.data.desc()).limit(10).all()
    counts = get_status_counts(current_user.id)
    return render_template(
        "main.html",
        registros=registros,
        finalizados=counts['FZ'],
        pendentes=counts['PT'],
        andamento=counts['AT'],
        erros=counts['ER'],
        STATUS_LABELS=STATUS_LABELS
    )

@bp.route('/test', methods=['GET', 'POST'])
@login_required
def test():
    counts = get_status_counts(current_user.id)
    return render_template(
        "layout.html",
        finalizados=counts['FZ'],
        pendentes=counts['PT'],
        andamento=counts['AT'],
        erros=counts['ER'],
        STATUS_LABELS=STATUS_LABELS
    )


@bp.route("/credenciais-mapa", methods=["GET", "POST"])
@login_required
def credenciais_mapa():
    from flask import current_app
    from app.mapa_api.montagem import usa_api
    from app.mapa_api.payloads import UFS
    cred = MapaCredencial.query.filter_by(owner_user_id=current_user.id).first()
    api = usa_api()

    if request.method == "POST":
        usuario_app = (request.form.get("usuario_app") or "").strip()
        numero_sif  = (request.form.get("numero_sif")  or "").strip()
        especie     = (request.form.get("especie")     or "").strip()
        senha_input = (request.form.get("senha")       or "").strip()
        cpf_cnpj    = "".join(ch for ch in (request.form.get("cpf_cnpj") or "") if ch.isdigit())
        ambito      = (request.form.get("ambito") or "").strip().upper()
        cod_uf      = (request.form.get("cod_uf") or "").strip().upper()
        ibge        = "".join(ch for ch in (request.form.get("cod_municipio_ibge") or "") if ch.isdigit())

        # Regra: senha obrigatória apenas no primeiro cadastro
        if not all([usuario_app, numero_sif, especie]) or (cred is None and not senha_input):
            flash("Preencha todos os campos (senha obrigatória no primeiro cadastro).", "warning")
            return redirect(url_for(".credenciais_mapa"))

        # Dados do estabelecimento exigidos pelo webservice (manual PGA-SIGSIF v1.3)
        if api:
            problemas = []
            if len(cpf_cnpj) not in (11, 14):
                problemas.append("CPF/CNPJ com 11 ou 14 dígitos")
            if ambito not in ("SIF", "ER"):
                problemas.append("âmbito SIF ou ER")
            if cod_uf not in UFS:
                problemas.append("UF do estabelecimento")
            if len(ibge) != 7:
                problemas.append("código IBGE do município com 7 dígitos")
            if problemas:
                flash("Informe: " + "; ".join(problemas) + ".", "warning")
                return redirect(url_for(".credenciais_mapa"))

        try:
            if cred is None:
                cred = MapaCredencial(
                    owner_user_id=current_user.id,
                    usuario_app=usuario_app,
                    numero_sif=numero_sif,
                    especie=especie,
                )
                cred.senha = senha_input  # obrigatório no primeiro cadastro
                db.session.add(cred)
                msg = "Credencial criada com sucesso."
            else:
                cred.usuario_app = usuario_app
                cred.numero_sif = numero_sif
                cred.especie = especie
                if senha_input:  # atualiza somente se informada
                    cred.senha = senha_input
                msg = "Credencial atualizada com sucesso."
            cred.cpf_cnpj, cred.ambito = cpf_cnpj or None, ambito or None
            cred.cod_uf, cred.cod_municipio_ibge = cod_uf or None, ibge or None
            if senha_input:
                cred.validado = False

            db.session.commit()
            flash(msg, "success")

        except Exception:
            db.session.rollback()
            flash("Não foi possível salvar a credencial.", "danger")

        return redirect(url_for(".credenciais_mapa"))

    # GET: renderiza o form com dados (se houver)
    return render_template(
        "credenciais-mapa.html", cred=cred, usa_api=api, ufs=sorted(UFS),
        ambiente=current_app.config.get("MAPA_API_AMBIENTE", "homologacao"),
    )


@bp.route("/credenciais-mapa/testar", methods=["POST"])
@login_required
def credenciais_mapa_testar():
    """Autentica no webservice (GET /especies) e registra o resultado na credencial."""
    from markupsafe import escape
    from app.mapa_api.client import ApiError
    from app.mapa_api.montagem import cliente
    cred = MapaCredencial.query.filter_by(owner_user_id=current_user.id).first()
    if cred is None:
        flash("Cadastre a credencial primeiro.", "warning")
        return redirect(url_for(".credenciais_mapa"))
    cli = cliente(cred)
    try:
        cli.validar_credenciais()
        cred.mark_validation(True)
        flash(f"Conexão com o webservice do MAPA OK (ambiente: {escape(cli.ambiente)}).", "success")
    except ApiError as e:
        cred.mark_validation(False, str(e))
        flash(f"Falha ao autenticar no webservice (ambiente {escape(cli.ambiente)}): {escape(str(e))}", "danger")
    db.session.commit()
    return redirect(url_for(".credenciais_mapa"))
