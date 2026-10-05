"""Serializacao segura de valores Python para literais JavaScript.

Os comandos enviados ao navegador sao texto executado via execute_async_script.
Interpolar strings cruas (f"'{valor}'") quebra com apostrofos/aspas e permite
injecao; aqui tudo vira literal JS valido.
"""
import json
import math


def js_str(valor) -> str:
    """String Python -> literal JS entre aspas duplas (seguro p/ </script>, U+2028/9)."""
    txt = json.dumps("" if valor is None else str(valor), ensure_ascii=False)
    return (
        txt.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def js_num(valor) -> str:
    """Numero Python -> literal JS. Rejeita NaN/inf (nunca devem chegar ao portal)."""
    if isinstance(valor, bool):
        raise ValueError("booleano nao e numero")
    f = float(valor)
    if math.isnan(f) or math.isinf(f):
        raise ValueError(f"numero invalido: {valor!r}")
    return str(int(f)) if f == int(f) else repr(f)
