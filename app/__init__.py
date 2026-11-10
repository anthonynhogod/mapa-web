# MAPA/app/__init__.py
from flask import Flask, request, url_for
from .extensions import db, migrate, login_manager
from app.executor.cli import init_app as init_worker_cli
import os
import logging
logging.basicConfig(level=logging.DEBUG)

# (logs ruidosos – mantidos)
for name in (
    "selenium.webdriver.remote.remote_connection",
    "urllib3.connectionpool",
    "urllib3",
    "selenium",
):
    logging.getLogger(name).setLevel(logging.WARNING)

# Desliga logs do webdriver-manager
os.environ.setdefault("WDM_LOG_LEVEL", "0")

from app.utils.pagination import render_paginator_text
from .config import ConfigFactory

def create_app():
    app = Flask(__name__, instance_relative_config=True)

    # Carrega a configuração por ambiente
    # (APP_ENV ou FLASK_ENV; default = development)
    config_cls = ConfigFactory.get()
    app.config.from_object(config_cls)

    # Garante que o diretório de instância existe
    os.makedirs(app.instance_path, exist_ok=True)

    # Fallback de banco SQLite no instance_path se não houver DATABASE_URL
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        db_path = os.path.join(app.instance_path, "db.sqlite")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    if not app.config.get("FERNET_KEY") and not app.config.get("TESTING"):
        app.logger.warning(
            "FERNET_KEY não configurada: não será possível salvar nem ler as credenciais do MAPA. "
            "Defina no .flaskenv (veja .flaskenv.example)."
        )

    # Extensões
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True, compare_type=True, compare_server_default=True)

    @app.template_filter("br")
    def _br(valor, casas=2):
        """1234567.5 -> '1.234.567,50' (formato brasileiro)."""
        try:
            return f"{float(valor):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return valor

    @app.template_global()
    def url_for_with(**kwargs):
        """
        Gera URL preservando rota atual + view_args + querystring,
        permitindo sobrescrever (page, etc.).
        Ex.: {{ url_for_with(page=2) }}
        """
        args = request.args.to_dict(flat=True)
        view_args = dict(request.view_args or {})
        args.update(kwargs)
        return url_for(request.endpoint, **view_args, **args)

    init_worker_cli(app)

    # Confere/atualiza o chromedriver em segundo plano (so em `flask run`/worker, modo navegador)
    from app.runner.drivers import verificar_na_inicializacao
    verificar_na_inicializacao(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        from .models import Usuario
        return db.session.get(Usuario, int(user_id))

    # Blueprints
    from .blueprints.public.routes import bp as public_bp
    from .blueprints.auth.routes import bp as auth_bp
    from .blueprints.registros.routes import bp as registros_bp
    from .blueprints.processor.routes import bp as processor_bp
    from .blueprints.api.routes import bp as api_bp
    from .blueprints.admin.routes import bp as admin_bp
    from .blueprints.comercializacao.routes import bp as comercializacao_bp
    from .blueprints.producao.routes import bp as producao_bp
    from .blueprints.verificacao.routes import bp as verificacao_bp
    from .blueprints.api.auth import register_cli

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(registros_bp, url_prefix="/registros")
    app.register_blueprint(processor_bp, url_prefix="/processor")
    app.register_blueprint(comercializacao_bp, url_prefix="/comercializacao")
    app.register_blueprint(producao_bp, url_prefix="/producao")
    app.register_blueprint(verificacao_bp, url_prefix="/verificacao")
    app.register_blueprint(api_bp, url_prefix="/api/v1/")

    # CLI para emitir JWT
    register_cli(app)

    # CORS conforme config
    from flask_cors import CORS
    CORS(app, resources=app.config.get("CORS_RESOURCES", {r"/api/*": {"origins": "*"}}))

    from .cli import users_promote, users_create, init_db, driver_check
    app.cli.add_command(users_promote)
    app.cli.add_command(users_create)
    app.cli.add_command(init_db)
    app.cli.add_command(driver_check)

    return app
