"""Espécies suportadas pelo sistema (valor gravado em Registro.especie / MapaCredencial.especie)."""

ESPECIES = [("suino", "Suíno")]
VALORES = {v for v, _ in ESPECIES}


def rotulo(valor: str) -> str:
    return dict(ESPECIES).get((valor or "").strip().lower(), valor or "")
