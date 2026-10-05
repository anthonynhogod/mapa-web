from flask_migrate import Migrate
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from models import *
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.exc import IntegrityError
from forms import model_to_form
from datetime import datetime

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///db.sqlite"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "supersecretkey"

# Inicializa o db com o app
db.init_app(app)

# Inicializa o Flask-Migrate com flags amigáveis ao SQLite
migrate = Migrate(
    app, db,
    render_as_batch=True,
    compare_type=True,
    compare_server_default=True
)

# Garanta que os modelos sejam importados (registrados no metadata)
with app.app_context():
    import models  # noqa: F401  # garante que todas as tabelas entrem no metadata

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, user_id)


@app.route('/', methods=['GET', 'POST'])
def index():
    return render_template("index.html")

@app.route('/sobre', methods=['GET', 'POST'])
def sobre():
    return render_template("sobre.html")

@app.route('/registrar', methods=["GET", "POST"])
def registrar():
    if request.method == "POST":
        nome = request.form.get("nome")
        senha = request.form.get("senha")
        tipo = request.form.get("tipo")
        
        if Usuario.query.filter_by(nome=nome).first():
            return render_template("registrar.html", error="nome already taken!")

        hashed_senha = generate_password_hash(senha, method="pbkdf2:sha256")

        new_user = Usuario(nome=nome, senha=hashed_senha, tipo=tipo)
        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for("login"))
    
    return render_template("registrar.html")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        nome = request.form.get("nome")
        senha = request.form.get("senha")

        user = Usuario.query.filter_by(nome=nome).first()

        if user and check_password_hash(user.senha, senha):
            login_user(user)
            return redirect(url_for("main"))
        else:
            return render_template("login.html", error="Invalid nome or senha")

    return render_template("login.html")


@app.route('/main', methods=['GET', 'POST'])
@login_required
def main():
    return render_template("main.html")

@app.route('/dados-api', methods=['GET', 'POST'])
@login_required
def dados_api():
    return render_template("dados-api.html")

#===================================================================================
#                                   REGISTROS
#===================================================================================

@app.route('/novo-registro', methods=['GET', 'POST'])
@login_required
def novo_registro():
    if request.method == "POST":
        dia = request.form.get("dia")
        especie = request.form.get("especie")
        obs = request.form.get("obs")
        
        try:
            data_obj = datetime.strptime(dia, "%Y-%m-%d").date()
        except ValueError:
            flash("Data inválida.", "error")
            return redirect(url_for("novo_registro"))

        if Registro.query.filter_by(data=dia, especie=especie, user_id=current_user.id).first():
            flash("Já existe um registro para esse dia e espécie!", "error")
            return redirect(url_for("novo_registro"))

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
        return redirect(url_for("novo_registro"))
    return render_template("novo-registro.html")

@app.route("/registros_prontos", methods=["GET"])
@login_required
def registros_prontos():
    registros = Registro.query.filter_by(user_id=current_user.id).all()
    return render_template("registros-prontos.html", registros=registros)

@app.route("/registros/<int:registro_id>", methods=["GET"])
@login_required
def registro_visualizar(registro_id):
    registro = (Registro.query
                .filter_by(id=registro_id, user_id=current_user.id)
                .first_or_404())
    return render_template("registro-detalhe.html", registro=registro)

@app.route("/registros/<int:registro_id>/editar", methods=["GET", "POST"])
@login_required
def registro_editar(registro_id):
    registro = (Registro.query
                .filter_by(id=registro_id, user_id=current_user.id)
                .first_or_404())
    if request.method == "POST":
        # Exemplo de atualização; ajuste aos seus campos
        registro.especie = request.form.get("especie", registro.especie)
        registro.obs = request.form.get("obs", registro.obs)
        db.session.commit()
        flash("Registro atualizado com sucesso.", "success")
        return redirect(url_for("registros_prontos"))
    return render_template("registro-editar.html", registro=registro)

@app.route("/registros/<int:registro_id>/dados", methods=["GET", "POST"])
@login_required
def registro_incluir_dados(registro_id):
    registro = (Registro.query
                .filter_by(id=registro_id, user_id=current_user.id)
                .first_or_404())
    if registro.status != "AT":
        abort(400, description="Registro não está em aberto para inclusão de dados.")
    if request.method == "POST":
        # TODO: Salvar os dados específicos que você vai incluir
        # Ex.: criar registros relacionados, atualizar status, etc.
        # registro.status = "FZ"  # se fizer sentido finalizar
        db.session.commit()
        flash("Dados incluídos com sucesso.", "success")
        return redirect(url_for("registros_prontos"))
    return render_template("registro-incluir-dados.html", registro=registro)


from flask import render_template, request, redirect, url_for, abort, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from models import db, GtaTemp, Registro  # GtaTemp já herda do GtaValidationMixin

@app.route("/registros/<int:registro_id>/dados/gta", methods=["GET", "POST"])
@login_required
def registro_incluir_gta(registro_id):
    registro = (
        Registro.query
        .filter_by(id=registro_id, user_id=current_user.id)
        .first_or_404()
    )

    if registro.status != "AT":
        abort(400, description="Registro não está em aberto para inclusão de dados.")

    if request.method == "POST":
        # --- 1) Coleta dos valores brutos
        ngta_raw   = (request.form.get("ngta") or "").strip()
        serie_raw  = (request.form.get("serie") or "").strip().upper()
        machos_raw = (request.form.get("machos") or "").strip()
        femeas_raw = (request.form.get("femeas") or "").strip()
        lote_raw   = (request.form.get("lote") or "").strip()
        peso_raw   = (request.form.get("peso") or "").strip()
        tipo_raw   = (request.form.get("tipo") or "M").strip().upper()

        # --- 2) Conversões mínimas (não são regras de negócio; só tipo)
        def to_int_or_none(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        # Se você colocou o helper dentro do modelo:
        def parse_peso_ptbr(txt: str):
            if txt is None:
                return None
            s = txt.strip().replace(".", "").replace(",", ".")
            try:
                return float(s)
            except ValueError:
                return None

        numero = to_int_or_none(ngta_raw)
        machos = to_int_or_none(machos_raw)
        femeas = to_int_or_none(femeas_raw)
        lote   = to_int_or_none(lote_raw)
        peso   = parse_peso_ptbr(peso_raw)  # ou: GtaTemp.parse_peso_ptbr(peso_raw)

        # Falhas de PARSE são tratadas aqui (antes das regras do mixin)
        if numero is None:
            flash("Número da GTA inválido.", "error")
            return render_template("registro-incluir-gta.html", registro=registro), 400
        if machos is None or femeas is None or lote is None or peso is None:
            flash("Campos numéricos inválidos (machos, fêmeas, lote ou peso).", "error")
            return render_template("registro-incluir-gta.html", registro=registro), 400

        # --- 3) Monta a entidade (a validação de negócio fica no mixin .validate())
        gta = GtaTemp(
            numero=numero,
            serie=serie_raw,
            machos=machos,
            femeas=femeas,
            lote=lote,
            peso=peso,
            tipo=tipo_raw,
            registro=registro,   # relationship já existente
        )

        # --- 4) Regras de negócio (mixin)
        err = gta.validate()  # -> None se ok, ou (mensagem, categoria) se inválido
        if err:
            flash(*err)
            return render_template("registro-incluir-gta.html", registro=registro), 400

        # --- 5) Persistência
        db.session.add(gta)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Já existe uma GTA com este número/série para este registro.", "error")
            return render_template("registro-incluir-gta.html", registro=registro), 409

        flash("GTA incluída com sucesso.", "success")
        # PRG: evita reenvio no refresh
        return redirect(url_for("registro_incluir_gta", registro_id=registro.id))

    # GET: renderiza a tela
    return render_template("registro-incluir-gta.html", registro=registro)


@app.route("/registros/<int:registro_id>/excluir", methods=["POST"])
@login_required
def registro_excluir(registro_id):
    registro = (Registro.query
                .filter_by(id=registro_id, user_id=current_user.id)
                .first_or_404())
    db.session.delete(registro)
    db.session.commit()
    flash("Registro excluído.", "success")
    return redirect(url_for("registros_prontos"))

#===================================================================================

@app.route('/consultar-dia', methods=['POST'])
def consultar_dia():
    data = request.get_json()

    dia = data.get('dia')
    especie = data.get('especie')
    obs = data.get('obs')

    # Simulação de validação
    if not dia or not especie:
        return jsonify({
            "status": "erro",
            "mensagem": "Todos os campos devem ser preenchidos."
        }), 400

    # Se tudo estiver certo
    return jsonify({
        "status": "ok",
        "mensagem": "Dia validado com sucesso."
    }), 200

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route('/historico', methods=['GET', 'POST'])
@login_required
def historico():
    return render_template("historico.html")


@app.route('/testes', methods=['GET', 'POST'])
def testes():
    
    FormClass = model_to_form(Usuario)
    form = FormClass()

    if form.validate_on_submit():
        novo_usuario = Usuario(
            nome=form.nome.data,
            senha=form.senha.data
        )
        db.session.add(novo_usuario)
        db.session.commit()
        return redirect('/sucesso')

    return render_template("testes.html", form=form)

# Logout route
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0")
