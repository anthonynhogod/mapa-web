# app/security.py
from functools import wraps
from flask import abort, request
from app.extensions import login_manager
from flask_login import current_user

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            # sessao perdida (expirou, cookie apagado...): nao e "sem permissao". Chamadas de polling (fetch) recebem 401
            # para a pagina avisar; navegacao normal vai para o login.
            if request.headers.get("X-Requested-With") == "fetch":
                abort(401)
            return login_manager.unauthorized()
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def scope_by_user(qs, model, user_field="user_id"):
    """
    Se for admin, retorna 'qs' sem alterar.
    Se for user normal, restringe a registros do próprio usuário.
    """
    if not getattr(current_user, "is_admin", False):
        field = getattr(model, user_field)
        qs = qs.filter(field == current_user.id)
    return qs