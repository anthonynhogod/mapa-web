# app/blueprints/api/auth.py
from __future__ import annotations
from functools import wraps
from typing import Iterable, Optional, Set, Dict, Any

from flask import request, jsonify, current_app
from flask_login import current_user
()
try:
    import jwt  # PyJWT (opcional)
except ImportError:  # pragma: no cover
    jwt = None


# ---------- respostas de erro JSON ----------
def _json_error(message: str, status: int = 401, **extra):
    payload = {"error": message, "status": status}
    if extra:
        payload.update(extra)
    resp = jsonify(payload)
    resp.status_code = status
    return resp


# ---------- extração e validação do token ----------
def get_bearer_token() -> Optional[str]:
    """Lê o header Authorization: Bearer <token>"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth.split(" ", 1)[1].strip() or None


class AuthError(Exception):
    pass


def validate_static_token(token: str, require_scopes: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Validação por lista branca de tokens em config['API_TOKENS']"""
    tokens = current_app.config.get("API_TOKENS")
    if not tokens:
        raise AuthError("Nenhum token configurado.")
    if token not in tokens:
        raise AuthError("Token inválido.")
    scopes = set()  # você pode mapear escopos por token se quiser
    if require_scopes and not set(require_scopes).issubset(scopes):
        raise AuthError("Escopo insuficiente para o token estático.")
    return {"method": "static", "sub": None, "scopes": scopes}


def validate_jwt_token(token: str, require_scopes: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Validação de JWT (HS256 por padrão). Requer PyJWT instalado."""
    if not jwt:
        raise AuthError("Suporte a JWT não está disponível (PyJWT não instalado).")

    secret = current_app.config.get("JWT_SECRET")
    if not secret:
        raise AuthError("JWT_SECRET não configurado.")

    audience = current_app.config.get("JWT_AUDIENCE")
    algorithms = current_app.config.get("JWT_ALGORITHMS", ["HS256"])
    options = {"require": ["exp"]}

    try:
        payload = jwt.decode(token, secret, algorithms=algorithms, audience=audience, options=options)
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expirado.")
    except jwt.InvalidTokenError:
        raise AuthError("Token JWT inválido.")

    scopes: Set[str] = set(payload.get("scopes", []) or [])
    if require_scopes and not set(require_scopes).issubset(scopes):
        raise AuthError("Escopo insuficiente.")

    return {
        "method": "jwt",
        "sub": payload.get("sub"),
        "scopes": scopes,
        "payload": payload,
    }


def validate_token(token: str, require_scopes: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    Tenta validar primeiro por token estático (se API_TOKENS estiver definido),
    depois JWT (se PyJWT + JWT_SECRET estiverem configurados).
    """
    # 1) Static
    tokens = current_app.config.get("API_TOKENS")
    if tokens:
        try:
            return validate_static_token(token, require_scopes=require_scopes)
        except AuthError:
            # cai para JWT se falhar
            pass

    # 2) JWT
    jwt_secret = current_app.config.get("JWT_SECRET")
    if jwt_secret:
        return validate_jwt_token(token, require_scopes=require_scopes)

    # nada configurado
    raise AuthError("Autenticação Bearer não configurada no servidor.")


# ---------- Decorators ----------
def bearer_or_login_required(require_scopes: Optional[Iterable[str]] = None):
    """
    Permite acesso se:
      a) houver Bearer token válido (estático ou JWT); OU
      b) estiver logado (Flask-Login) e API_ALLOW_SESSION=True.
    Em caso de Bearer presente porém inválido → 401.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = get_bearer_token()
            if token:
                try:
                    request.auth = validate_token(token, require_scopes=require_scopes)
                    return fn(*args, **kwargs)
                except AuthError as e:
                    return _json_error(str(e), 401)

            # Fallback para sessão
            if current_app.config.get("API_ALLOW_SESSION", True) and current_user.is_authenticated:
                request.auth = {"method": "session", "user_id": current_user.id, "scopes": set()}
                return fn(*args, **kwargs)

            return _json_error("Não autorizado.", 401)
        return wrapper
    return decorator


def token_required(require_scopes: Optional[Iterable[str]] = None):
    """
    Exige obrigatoriamente Bearer token válido (sem fallback de sessão).
    Use para endpoints que devem ser consumidos só como API.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = get_bearer_token()
            if not token:
                return _json_error("Bearer token ausente.", 401)
            try:
                request.auth = validate_token(token, require_scopes=require_scopes)
            except AuthError as e:
                return _json_error(str(e), 401)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------- Utilitário opcional: emitir JWT (se PyJWT estiver disponível) ----------
def issue_jwt(sub: str | int, scopes: Optional[Iterable[str]] = None, expires_minutes: int = 60) -> str:
    """
    Gera um JWT HS256 (padrão) com claims mínimos.
    Requer PyJWT instalado e JWT_SECRET no config.
    """
    if not jwt:
        raise RuntimeError("PyJWT não instalado. pip install PyJWT")
    import datetime as dt

    now = dt.datetime.utcnow()
    payload = {
        "sub": str(sub),
        "iat": now,
        "nbf": now,
        "exp": now + dt.timedelta(minutes=expires_minutes),
        "scopes": list(scopes or []),
        "iss": current_app.config.get("JWT_ISSUER", "mapa-api"),
    }
    aud = current_app.config.get("JWT_AUDIENCE")
    if aud:
        payload["aud"] = aud

    secret = current_app.config["JWT_SECRET"]
    alg = current_app.config.get("JWT_ALGORITHM", "HS256")
    token = jwt.encode(payload, secret, algorithm=alg)
    # PyJWT >= 2 já retorna str
    return token

import click

def register_cli(app):
    @app.cli.command("issue-jwt")
    @click.option("--sub", required=True, help="Subject (usuário/ID)")
    @click.option("--scope", multiple=True, help="Escopos (pode repetir)")
    @click.option("--minutes", default=60, show_default=True, help="Validade em minutos")
    def issue_jwt_cmd(sub, scope, minutes):
        """Emite um JWT HS256 usando JWT_SECRET do config."""
        with app.app_context():
            token = issue_jwt(sub=sub, scopes=scope, expires_minutes=minutes)
            click.echo(token)