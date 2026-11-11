
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash
from app.extensions import db
from app.models import Usuario
from app.utils.audit import log_action 


bp = Blueprint("auth", __name__)

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        senha = (request.form.get("senha") or "").strip()

        user = Usuario.query.filter_by(nome=nome).first()

        if user and check_password_hash(user.senha, senha):
            login_user(user)
            try:
                log_action("auth:login")
            except Exception:
                pass

            # Sem lógica de next → decide pelo papel
            if getattr(user, "is_admin", False):
                return redirect(url_for("admin.index"))   # Admin → dashboard
            else:
                return redirect(url_for("public.main"))   # Usuário → home

        flash("Credenciais inválidas.", "erro")
        return redirect(url_for("auth.login"))

    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    log_action("auth:logout")
    logout_user()
    return redirect(url_for("public.index"))
