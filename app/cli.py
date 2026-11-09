# app/cli.py (acrescente ao final)
import click
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash
from app.extensions import db
from app.models import Usuario, Role

@click.command("users-promote")
@click.option("--nome", required=True)
@with_appcontext
def users_promote(nome):
    u = Usuario.query.filter_by(nome=nome).first()
    if not u:
        click.echo("Usuário não encontrado.")
        return
    u.role = Role.ADMIN
    db.session.commit()
    click.echo(f"Usuário '{nome}' promovido a ADMIN.")

@click.command("users-create")
@click.option("--nome", required=True)
@click.option("--senha", required=True)
@click.option("--role", default="user", type=click.Choice(["user","admin"]))
@with_appcontext
def users_create(nome, senha, role):
    if Usuario.query.filter_by(nome=nome).first():
        click.echo("Já existe usuário com esse nome.")
        return
    u = Usuario(
        nome=nome,
        senha=generate_password_hash(senha, method="pbkdf2:sha256"),
        role=Role.ADMIN if role=="admin" else Role.USER,
        is_active=True
    )
    db.session.add(u)
    db.session.commit()
    click.echo(f"Criado {role} '{nome}'.")

@click.command("delete-user")
@with_appcontext
def delete(id):
    user = Usuario.query.filter_id(id=id).first()
    db.session.delete(user)
    db.session.commit()
    click.echo(f"Usuário deletado {user.id} '{user.nome}'.")


@click.command("init-db")
@with_appcontext
def init_db():
    """Cria o schema num banco NOVO, carrega as constantes (abate e comercializacao)
    e marca as migrations como aplicadas. Em banco existente use `flask db upgrade`."""
    from flask_migrate import stamp
    from app.seeds.run import seed_abate, seed_comercializacao

    db.create_all()
    n1 = seed_abate()
    n2 = seed_comercializacao()
    stamp()
    click.echo(f"Banco inicializado. Constantes: abate={n1}, comercializacao={n2}.")


@click.command("driver-check")
@click.option("--atualizar/--so-verificar", default=True, help="Baixa o driver certo se o atual nao servir (padrao).")
@with_appcontext
def driver_check(atualizar):
    """Confere se o chromedriver combina com o Chrome instalado e, se nao, atualiza."""
    from app.runner import drivers
    from flask import current_app
    st = drivers.garantir(forcar=True, explicito=current_app.config.get("CHROMEDRIVER_PATH"))
    for t in st["tentativas"]:
        click.echo(f"  [{'ok' if t['ok'] else '--'}] {t['origem']}: {t['detalhe']}")
    click.echo(("OK: " if st["ok"] else "FALHOU: ") + st["mensagem"])
    raise SystemExit(0 if st["ok"] else 1)
