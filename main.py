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
    
@app.route("/produtos/novo", methods=["GET", "POST"])
@login_required
def novo_produto():
    if current_user.tipo != "admin":
        return "Acesso negado.", 403

    if request.method == "POST":
        nome_produto = request.form["nomeProduto"]
        preco = float(request.form["precoProduto"])
        imagem = request.form["imagemProduto"]

        novo = Produto(
            nome=nome_produto,
            preco=preco,
            imagem=imagem,
            id_empresa=current_user.id
        )
        db.session.add(novo)
        db.session.commit()

    return render_template("novo_produto.html")

@app.route("/produtos", methods=["GET", "POST"])
def produtos():
    if request.method == "POST" and current_user.tipo == "admin":
        nome_produto = request.form["nomeProduto"]
        produto = Produto(nome=nome_produto, id_empresa=current_user.id)
        db.session.add(produto)
        db.session.commit()
        return redirect(url_for("produtos"))

    if current_user.is_authenticated and current_user.tipo == "admin":
        produtos = Produto.query.filter_by(id_empresa=current_user.id).all()
    else:
        produtos = Produto.query.all()

    return render_template("produtos.html", produtos=produtos)

@app.route("/produtos/<int:id>")
def detalhe_produto(id):
    produto = Produto.query.get_or_404(id)
    return render_template("detalhe_produto.html", produto=produto)

@app.route("/produtos/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_produto(id):
    produto = Produto.query.get_or_404(id)
    if current_user.id != produto.id_empresa:
        return "Acesso negado", 403


    if request.method == "POST":
        produto.nome = request.form["nomeProduto"]
        produto.preco = float(request.form["precoProduto"])
        produto.imagem = request.form["imagemProduto"]
        db.session.commit()
        return redirect(url_for("produtos"))


    return render_template("editar_produto.html", produto=produto)

@app.route("/produtos/<int:id>/excluir", methods=["POST"])
@login_required
def excluir_produto(id):
    produto = Produto.query.get_or_404(id)
    if current_user.id != produto.id_empresa:
        return "Acesso negado", 403

    db.session.delete(produto)
    db.session.commit()
    return redirect(url_for("produtos"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)