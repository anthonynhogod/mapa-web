from dataclasses import dataclass
from typing import Tuple, List
from flask import Request
from app.utils.format import para_int, parse_peso_br

@dataclass
class DadosGTA:
    numero: int
    serie: str
    machos: int
    femeas: int
    lote: int
    peso: float
    tipo: str = "M"

    @classmethod
    def from_request(cls, req: Request) -> Tuple["DadosGTA | None", List[str]]:
        f = req.form
        numero = para_int(f.get("ngta"))
        serie = (f.get("serie") or "").strip().upper()
        machos = para_int(f.get("machos"))
        femeas = para_int(f.get("femeas"))
        lote = para_int(f.get("lote"))
        peso = parse_peso_br(f.get("peso"))
        tipo = (f.get("tipo") or "M").strip().upper()

        erros = []
        if numero is None: erros.append("Número da GTA inválido.")
        if machos is None: erros.append("Quantidade de machos inválida.")
        if femeas is None: erros.append("Quantidade de fêmeas inválida.")
        if lote   is None: erros.append("Lote inválido.")
        if peso   is None: erros.append("Peso inválido.")

        if erros:
            return None, erros

        return cls(
            numero=numero, serie=serie, machos=machos,
            femeas=femeas, lote=lote, peso=peso, tipo=tipo), []
