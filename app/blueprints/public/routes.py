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


def _erro_salvar_credencial(exc: Exception) -> str:
    """Motivo legivel (e seguro de exibir) de uma falha ao gravar a credencial."""
    from sqlalchemy.exc import IntegrityError
    msg = str(exc)
    if "FERNET_KEY" in msg or exc.__class__.__name__ in ("InvalidToken",) or (
            isinstance(exc, ValueError) and "Fernet" in msg):
        return ("A chave de criptografia (FERNET_KEY) não está configurada ou é inválida no servidor. "
                "Gere uma com: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
                "e coloque em FERNET_KEY no .flaskenv.")
    if isinstance(exc, IntegrityError):
        return "Já existe uma credencial para este usuário. Recarregue a página e edite a existente."
    return "Erro inesperado ao gravar (detalhes no log do servidor)."


@bp.route("/credenciais-mapa", methods=["GET", "POST"])
@login_required
def credenciais_mapa():
    from flask import current_app
    from markupsafe import escape
    from app.mapa_api.montagem import usa_api
    from app.mapa_api.payloads import UFS
    from app.utils.especies import ESPECIES, VALORES
    cred = MapaCredencial.query.filter_by(owner_user_id=current_user.id).first()
    api = usa_api()

    def render(form, status=200):
        # valor antigo fora da lista (ex.: numero digitado quando o campo era numerico): mostra e pede correcao
        especies = list(ESPECIES)
        atual = (form.get("especie") or "").strip()
        if atual and atual.lower() not in VALORES:
            especies.append((atual, f"{atual} (valor inválido — escolha uma espécie)"))
        return render_template(
            "credenciais-mapa.html", cred=cred, form=form, usa_api=api, ufs=sorted(UFS), especies=especies,
            ambiente=current_app.config.get("MAPA_API_AMBIENTE", "homologacao"),
        ), status

    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in
                ("usuario_app", "numero_sif", "especie", "cpf_cnpj", "ambito", "cod_uf", "cod_municipio_ibge")}
        senha_input = (request.form.get("senha") or "").strip()
        form["especie"] = form["especie"].lower()
        form["ambito"], form["cod_uf"] = form["ambito"].upper(), form["cod_uf"].upper()
        cpf_cnpj = "".join(ch for ch in form["cpf_cnpj"] if ch.isdigit())
        ibge = "".join(ch for ch in form["cod_municipio_ibge"] if ch.isdigit())

        # Lista TODOS os problemas de uma vez (nada de descobrir um por tentativa)
        problemas = []
        if not form["usuario_app"]:
            problemas.append("usuário do MAPA")
        if cred is None and not senha_input:
            problemas.append("senha (obrigatória no primeiro cadastro)")
        if not form["numero_sif"].isdigit():
            problemas.append("número SIF (somente dígitos)")
        if form["especie"] not in VALORES:
            problemas.append("espécie (escolha uma da lista)")
        if not api and form["cod_uf"] and form["cod_uf"] not in UFS:
            problemas.append("UF do estabelecimento (sigla válida ou deixe em branco)")
        if api:   # dados do estabelecimento exigidos pelo webservice (manual PGA-SIGSIF v1.3)
            if len(cpf_cnpj) not in (11, 14):
                problemas.append("CPF/CNPJ com 11 ou 14 dígitos")
            if form["ambito"] not in ("SIF", "ER"):
                problemas.append("âmbito SIF ou ER")
            if form["cod_uf"] not in UFS:
                problemas.append("UF do estabelecimento")
            if len(ibge) != 7:
                problemas.append("código IBGE do município com 7 dígitos")
        if problemas:
            flash("Corrija: " + "; ".join(problemas) + ".", "warning")
            return render(form, 400)

        try:
            if cred is None:
                cred = MapaCredencial(
                    owner_user_id=current_user.id, usuario_app=form["usuario_app"],
                    numero_sif=form["numero_sif"], especie=form["especie"],
                )
                cred.senha = senha_input  # obrigatório no primeiro cadastro
                db.session.add(cred)
                msg = "Credencial criada com sucesso."
            else:
                cred.usuario_app, cred.numero_sif, cred.especie = form["usuario_app"], form["numero_sif"], form["especie"]
                if senha_input:  # atualiza somente se informada
                    cred.senha = senha_input
                msg = "Credencial atualizada com sucesso."
            # So grava o que veio no formulario: salvar no modo navegador nao apaga o que foi preenchido no modo API
            if "cpf_cnpj" in request.form:
                cred.cpf_cnpj = cpf_cnpj or None
            if "ambito" in request.form:
                cred.ambito = form["ambito"] or None
            if "cod_uf" in request.form:
                cred.cod_uf = form["cod_uf"] or None
            if "cod_municipio_ibge" in request.form:
                cred.cod_municipio_ibge = ibge or None
            if senha_input:
                cred.validado = False

            db.session.commit()
            flash(msg, "success")
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Falha ao salvar MapaCredencial (user=%s)", current_user.id)
            cred = MapaCredencial.query.filter_by(owner_user_id=current_user.id).first()
            flash(escape(_erro_salvar_credencial(exc)), "danger")
            return render(form, 500)

        return redirect(url_for(".credenciais_mapa"))

    # GET: valores atuais da credencial (se houver)
    form = {
        "usuario_app": cred.usuario_app if cred else "", "numero_sif": cred.numero_sif if cred else "",
        "especie": cred.especie if cred else "suino",
        "cpf_cnpj": (cred.cpf_cnpj or "") if cred else "", "ambito": (cred.ambito or "SIF") if cred else "SIF",
        "cod_uf": (cred.cod_uf or ("RS" if api else "")) if cred else ("RS" if api else ""), "cod_municipio_ibge": (cred.cod_municipio_ibge or "") if cred else "",
    }
    return render(form)


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
