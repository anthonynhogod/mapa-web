# app/utils/audit.py
from app.extensions import db
from app.models import AuditLog
from flask_login import current_user

def log_action(action: str, entity: str = None, entity_id: int = None, meta: dict = None):
    try:
        ev = AuditLog(
            user_id=getattr(current_user, "id", None),
            action=action,
            entity=entity,
            entity_id=entity_id,
            meta=meta or {}
        )
        db.session.add(ev)
        db.session.commit()
    except Exception:
        db.session.rollback()