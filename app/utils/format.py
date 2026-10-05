from typing import Optional
import unicodedata
import re
import math
from datetime import datetime

def para_int(v, default: int = 0) -> int:
    """
    Converte v para int de forma robusta:
      - aceita int/float/str/None;
      - trata NaN;
      - remove caracteres não numéricos em strings (mantém sinal).
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if math.isnan(v):
            return default
        return int(v)
    # string ou coisa convertível a string
    s = str(v).strip()
    if s == "":
        return default
    # mantém apenas dígitos e sinal
    s = re.sub(r"[^\d\-\+]", "", s)
    if s in ("", "+", "-"):
        return default
    try:
        return int(s)
    except Exception:
        # último recurso: tenta via float
        try:
            return int(float(s))
        except Exception:
            return default


def parse_peso_br(txt) -> Optional[float]:
    """
    Converte valores no formato PT-BR ou EN para float:
      '1.234,56' -> 1234.56
      '106,13'   -> 106.13
      '106.13'   -> 106.13
      '106,13 kg'/'106 kg' -> 106.13/106.0
    Regras:
      - remove tudo que não for dígito, vírgula, ponto ou sinal;
      - se há vírgula e ponto -> o ÚLTIMO separador é decimal; o outro é milhar;
      - se só vírgula -> vírgula é decimal;
      - se só ponto -> ponto é decimal.
    """
    if txt is None:
        return None
    s = str(txt).strip()
    s = re.sub(r"[^\d,.\-+]+", "", s)
    if not s:
        return None
    if ("," in s) and ("." in s):
        # o último separador encontrado é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_str(txt: str) -> str:
    if not isinstance(txt, str):
        return ""
    txt = txt.lower().strip()
    txt = unicodedata.normalize('NFKD', txt)
    txt = txt.encode('ascii', 'ignore').decode('utf-8')
    txt = re.sub(r'[^a-z0-9\s]', '', txt)
    txt = re.sub(r'\s+', ' ', txt)
    return txt.strip()


def _parse_date_ptbr(raw: str):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError("Data inválida. Use o formato dd/mm/aaaa.")
