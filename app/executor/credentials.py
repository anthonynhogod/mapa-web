# app/executor/credentials.py
from datetime import datetime, timedelta
from app import db
from app.models import MapaCredencial
from app.runner.web.navegador import Navegador
import traceback
from flask import current_app
# TTL opcional para revalidar de tempos em tempos
def _ttl_days() -> int:
    try:
        from flask import current_app as _ca
        return int(_ca.config.get("CREDENTIAL_TTL_DAYS", "7"))
    except Exception:
        return 7

def _needs_revalidate(cred: MapaCredencial) -> bool:
    if not cred.validado:
        return True
    if not cred.validate_at:
        return True
    ttl = _ttl_days()
    if ttl <= 0:
        return False
    return cred.validate_at < (datetime.utcnow() - timedelta(days=ttl))

def validate_credentials(cred: MapaCredencial, data_ref: str) -> tuple[bool, str | None]:
    nav = None
    try:
        nav = Navegador(
            mode="HIDE",  # validação leve em headless
            chromedriver_path=current_app.config.get("CHROMEDRIVER_PATH"),
        )
        ok, err = nav.authenticate(cred.usuario_app, cred.senha)
        return (True, None) if ok else (False, err)
    except Exception as e:
        return False, str(e)
    finally:
        try:
            if nav: nav.quit()
        except Exception:
            pass

def ensure_valid_credential(cred: MapaCredencial, data_ref: str) -> tuple[bool, str | None]:
    """
    Garante que a credencial está válida; se precisar, revalida e persiste.
    """
    if not _needs_revalidate(cred):
        return True, None

    ok, err = validate_credentials(cred, data_ref)
    cred.mark_validation(ok, err)
    db.session.commit()
    return ok, err