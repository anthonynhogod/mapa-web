# app/blueprints/processor/services.py
import os
import json
import io
import importlib
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

import pandas as pd
from flask import current_app
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError
from app.blueprints.processor.especie.profiles import get_profile
from app.models import (
    db,
    UploadStatus,
    GtaUploadTmp,
    DifTmp,
    SifTmp,
    Registro,            # <- usamos para descobrir a espécie a partir do registro
)

# ============================
# Exceção de validação
# ============================
class ValidationFailed(Exception):
    """Lançada quando a validação de planilha retorna erros bloqueantes."""
    def __init__(self, *, errors: List[dict], warnings: Optional[List[dict]] = None, message: Optional[str] = None):
        self.errors = errors or []
        self.warnings = warnings or []
        super().__init__(message or "Falha de validação do arquivo.")

# Diretório base configurável
DEFAULT_BASE_UPLOAD_DIR = os.path.join("uploads", "tmp")
def _get_base_upload_dir() -> str:
    return current_app.config.get("UPLOAD_TMP_DIR", DEFAULT_BASE_UPLOAD_DIR)

# FS helpers
def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _save_file(arquivo, pasta_destino, filename):
    _ensure_dir(pasta_destino)
    fullpath = os.path.join(pasta_destino, filename)
    arquivo.save(fullpath)
    return fullpath

def _read_excel(path: str) -> pd.DataFrame:
    # Engine deixado para autodetect; ajuste para engine='openpyxl' se preferir
    return pd.read_excel(path)

# -----------------------------
# Espécie: normalização + loader + registry
# -----------------------------
def _normalize_especie(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in {"suino", "suíno", "suínos", "suinos", "porco", "porcina", "porcinos"}:
        return "suino"
    if v in {"ave", "aves", "avícola", "avicola"}:
        return "ave"   # sua pasta é 'ave'
    if v in {"bovino", "bovinos", "bov.", "bovino(a)"}:
        return "bovino"
    return v or "suino"

def _load_species_module(especie: str):
    """Importa o módulo validate da espécie, com fallback para suino."""
    especie = _normalize_especie(especie)
    attempts = [f"app.blueprints.processor.especie.{especie}.validate"]
    last_exc = None
    for mpath in attempts:
        try:
            return importlib.import_module(mpath)
        except ModuleNotFoundError as exc:
            last_exc = exc
            continue
    raise ImportError(
        f"Não foi possível carregar o módulo de validação de '{especie}'. "
        f"Tentativas: {attempts}. Último erro: {last_exc}"
    )

# DTO de retorno
@dataclass
class UploadResultDTO:
    id: int
    replaced: bool
    filetype: str
    filename: str
    status: UploadStatus
    warnings_count: int = 0
    errors_count: int = 0
    meta: Optional[Dict[str, Any]] = None  # opcional, para UI avançada

# -----------------------------
# Pipeline de validação por espécie/tipo
# -----------------------------
def _validate_by_species(filetype: str, fullpath: str, registro_id: int) -> Dict[str, Any]:
    df = _read_excel(fullpath)
    reg = db.session.get(Registro, registro_id)
    especie = _normalize_especie(getattr(reg, 'especie', None) if reg else None)
    _load_species_module(especie)   # garante que o validate.py registre o profile
    profile = get_profile(especie)
    func_map = {
        'gta': profile.validar_gta,
        'dif': profile.validar_dif,
        'sif': profile.validar_sif,
    }
    if filetype not in func_map:
        raise NotImplementedError(
            f"Tipo de arquivo '{filetype}' não suportado para a espécie '{especie}'."
        )
    # CHAMAR a função e obter o payload (dict)
    payload = func_map[filetype](df)
    # Completar meta (defensivo)
    if isinstance(payload, dict):
        meta = payload.setdefault("meta", {})
        meta.setdefault("species", getattr(profile, "name", especie))
        meta.setdefault("hints", getattr(profile, "hints", {}))
    else:
        raise TypeError(
            f"O validador '{filetype}' para '{especie}' retornou {type(payload)}; esperado 'dict'."
        )
    return payload

# -----------------------------
# Serviço principal (delete+insert atômico)
# -----------------------------
def process_upload(*, file, filename: str, filetype: str, model: str, registro_id: int, user_id: int) -> UploadResultDTO:
    """
    Salva o arquivo em uploads/tmp/<registro_id>/<filetype>/<filename>,
    valida dinamicamente por espécie e grava na tabela temporária correspondente.
    """
    # 1) FS
    safe_name = secure_filename(filename or getattr(file, "filename", "upload.xlsx"))
    fullpath = io.BytesIO(file.read())

    # 2) Model alvo por tipo
    MODEL_MAP = {
        "gta": GtaUploadTmp,
        "sif": SifTmp,
        "dif": DifTmp,
    }
    if filetype not in MODEL_MAP:
        raise ValueError(f"Tipo de arquivo inválido: {filetype}")
    ModelCls = MODEL_MAP[filetype]

    # 3) Validação dinâmica por espécie (gera payload com meta.errors/warnings)
    payload_obj = _validate_by_species(filetype=filetype, fullpath=fullpath, registro_id=registro_id)
    meta = payload_obj.get("meta", {}) if isinstance(payload_obj, dict) else {}
    errors = meta.get("errors", []) or []
    warnings = meta.get("warnings", []) or []
    especie = meta.get("species", "suino")

    # 4) Bloqueio por erros
    if errors:
        raise ValidationFailed(errors=errors, warnings=warnings, message="A planilha contém erros de validação.")

    # 5) Persistência (delete+insert por registro_id)
    replaced = False
    sess = db.session
    try:
        with sess.begin_nested():
            existing = (
                sess.query(ModelCls)
                .filter_by(registro_id=registro_id)
                .one_or_none()
            )
            if existing is not None:
                sess.delete(existing)
                sess.flush()
                replaced = True

            rec = ModelCls(
                registro_id=registro_id,
                filename=safe_name,                      # <= NOT NULL
                model=(model or filetype),               # <= NOT NULL
                payload=payload_obj,                     # JSON/objeto Python
                status=UploadStatus.VALIDATED,           # RECEIVED se preferir
                uploaded_by=user_id,                     # <= evita NULL
            )
            # se o modelo tiver 'species' ou 'especie', preenche também
            if hasattr(ModelCls, "species"):
                rec.species = especie
            elif hasattr(ModelCls, "especie"):
                rec.especie = especie

            sess.add(rec)
            sess.flush()
            new_id = rec.id

        sess.commit()

        return UploadResultDTO(
            id=new_id,
            replaced=replaced,
            filetype=filetype,
            filename=safe_name,
            status=UploadStatus.VALIDATED,
            warnings_count=len(warnings),
            errors_count=0,
            meta=meta,
        )

    except IntegrityError as e:
        sess.rollback()
        raise RuntimeError("Conflito ou violação de NOT NULL ao registrar upload temporário.") from e
    except Exception:
        sess.rollback()
