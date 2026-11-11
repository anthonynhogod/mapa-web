import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("FERNET_KEY", "ZmFrZS1rZXktZmFrZS1rZXktZmFrZS1rZXktZmFrZTA=")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


@pytest.fixture()
def app():
    from app import create_app
    from app.extensions import db
    from app.seeds.run import seed_comercializacao

    from app.logic.constantes import invalidar_cache
    invalidar_cache()                  # cache de constantes e global ao processo: isola cada teste
    application = create_app()
    # os testes de webservice partem do modo api; os de navegador sobrescrevem para 'browser'
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False, EXEC_BACKEND="api")
    with application.app_context():
        db.create_all()
        seed_comercializacao()
        yield application
        db.session.remove()
        db.drop_all()
    invalidar_cache()
