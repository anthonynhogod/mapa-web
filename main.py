from flask import Flask, render_template, redirect, request, url_for
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from db import db
from models import *
import hashlib

app = Flask(__name__)
app.secret_key = "batata"
lm = LoginManager(app)
lm.login_view = "login"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
db.init_app(app)

def hash(text):
    hash_obj = hashlib.sha256(text.encode("utf-8"))
    return hash_obj.hexdigest()

@lm.user_loader
def user_loader(id):
    usuario = db.session.query(Usuario).filter_by(id=id).first()
    return usuario

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    elif request.method == "POST":
        nome = request.form["nomeForm"]
        senha = request.form["senhaForm"]
        
        user = db.session.query(Usuario).filter_by(nome=nome, senha=hash(senha)).first()
        if not user:
            return "Nome ou senha incorretos."
        else:
            login_user(user)
            return redirect(url_for("home"))
        
@app.route("/registrar", methods=["GET", "POST"])
def registrar():
    if request.method == "GET":
        return render_template("registrar.html")
    elif request.method == "POST":
        nome = request.form["nomeForm"]
        senha = request.form["senhaForm"]
        tipo = request.form["tipoForm"]  # "admin" ou "cliente"

        novo_usuario = Usuario(nome=nome, senha=hash(senha), tipo=tipo)
        db.session.add(novo_usuario)
        db.session.commit()
        
        login_user(novo_usuario)
        return redirect(url_for("home"))
 
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)