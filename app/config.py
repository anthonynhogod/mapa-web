# app/config.py
import os
from typing import Set, Optional

def _to_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip() in {"1", "true", "True", "YES", "yes", "on", "ON"}


def _parse_tokens(raw_tokens: Optional[str], fallback_one: Optional[str] = None) -> Set[str]:
    """
    Converte "a,b, c" -> {"a","b","c"}.
    Se raw_tokens for None e fallback_one existir, retorna {fallback_one}.
    """
    if raw_tokens:
        return {t.strip() for t in raw_tokens.split(",") if t.strip()}
    return {fallback_one} if fallback_one else set()

class BaseConfig:
    # Segurança
    SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")

    # SQLAlchemy
    # Obs.: deixamos None por padrão e resolvemos o fallback em __init__.py
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", None)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Auth API
    API_TOKENS = _parse_tokens(
        os.getenv("API_TOKENS") or os.getenv("API_TOKEN"),
        fallback_one="dev-token-123",
    )
    API_ALLOW_SESSION = _to_bool(os.getenv("API_ALLOW_SESSION", "1"))

    # JWT / Cripto
    FERNET_KEY = os.getenv("FERNET_KEY")
    JWT_SECRET = os.getenv("JWT_SECRET")   # defina no .flaskenv em dev
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_AUDIENCE = os.getenv("JWT_AUDIENCE")

    # Runner / Selenium / Navegador
    CHROMEDRIVER_PATH = os.getenv("CHROMEDRIVER_PATH")
    SCRIPTS_JS_PATH = os.getenv("SCRIPTS_JS_PATH")
    NAV_MODE = os.getenv("NAV_MODE", "HIDE")
    NAV_SPEED_PROFILE = os.getenv("NAV_SPEED_PROFILE", "balanced")
    NAV_TAP_UI = os.getenv("NAV_TAP_UI", "auto")
    # Execucao dos lancamentos: "api" (webservice do MAPA) | "browser" (Selenium, legado)
    EXEC_BACKEND = os.getenv("EXEC_BACKEND", "api").strip().lower()
    # Webservice PGA-SIGSIF. Padrao = HOMOLOGACAO; producao so com MAPA_API_AMBIENTE=producao.
    MAPA_API_AMBIENTE = os.getenv("MAPA_API_AMBIENTE", "homologacao").strip().lower()
    MAPA_API_URL = os.getenv("MAPA_API_URL") or None          # sobrescreve a raiz (testes/proxy)
    MAPA_API_MD5 = _to_bool(os.getenv("MAPA_API_MD5", "1"), True)   # senha em md5 no Basic (manual 2.1)
    MAPA_API_TIMEOUT = float(os.getenv("MAPA_API_TIMEOUT", "60"))
    # Formato de data dos corpos: 'iso' (yyyy-mm-dd) ou 'br' (dd/mm/aaaa). O swagger da
    # comercializacao usa ISO; a tabela de parametros do abate usa dd/mm/aaaa.
    MAPA_API_DATA_COMERCIALIZACAO = os.getenv("MAPA_API_DATA_COMERCIALIZACAO", "iso")
    MAPA_API_DATA_ABATE = os.getenv("MAPA_API_DATA_ABATE", "br")

    # CORS (mesma configuração atual)
    CORS_RESOURCES = {r"/api/*": {"origins": "*"}}

    # Ajustes de log podem ser feitos no __init__ (mantido lá)

    # Hook opcional para ajustes finais dependentes do app
    @staticmethod
    def init_app(app):
        """
        Espaço para configurar qualquer integração que precise do app.
        (Neste caso não é necessário nada; o fallback de DB fica no __init__.py)
        """
        pass


class DevelopmentConfig(BaseConfig):
    FLASK_DEBUG = True


class ProductionConfig(BaseConfig):
    FLASK_DEBUG = False


class TestingConfig(BaseConfig):
    TESTING = True
    # Banco em memória para testes
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class ConfigFactory:
    """
    Resolve a configuração pelo ambiente.
    Usa as variáveis APP_ENV / FLASK_ENV, nesta ordem,
    com fallback para 'development'.
    """
    @staticmethod
    def get(env: Optional[str] = None):
        env = (env or os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").lower()
        if env in {"prod", "production"}:
            return ProductionConfig
        if env in {"test", "testing"}:
            return TestingConfig
        # default
        return DevelopmentConfig