from typing import Tuple
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import GtaTemp, Registro
from .dto import DadosGTA

def incluir_gta_no_registro(registro: Registro, dados: DadosGTA) -> Tuple[bool, str, str]:
    """
    Retorna: (ok, mensagem, categoria_flash)
    """
    gta = GtaTemp(
        numero=dados.numero,
        serie=dados.serie,
        machos=dados.machos,
        femeas=dados.femeas,
        lote=dados.lote,
        peso=dados.peso,
        tipo=dados.tipo,
        registro=registro,
    )

    # Validação de negócio (PT-BR)
    erro = gta.validate()
    if erro:
        # esperamos (mensagem, categoria)
        msg, cat = erro if isinstance(erro, (list, tuple)) and len(erro) == 2 else (str(erro), "error")
        return False, msg, cat

    db.session.add(gta)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False, "Já existe uma GTA com este número/série para este registro.", "error"

    return True, "GTA incluída com sucesso.", "success"