"""
class GtaTmpValidator:
    def __init__(self, **kwargs):

        atributos = ["numero", "serie", "machos", "femeas", "lote", "peso", "tipo"]
        "", ".join(str(item) for item in my_list_mixed)"
        if [key.lower().strip() for key in kwargs.keys()] != atributos:
            raise Exception(f"Inclua todos os atributos padrão:\n{atributos}\nEx: GtaTmpValidator({"='', ".join(str(atr) for atr in atributos)},='')") 
        self.__dict__.update(kwargs)
"""
from typing import Optional, Tuple

class GtaTmpValidator:
    """
    Mixin que adiciona validação de regras de negócio.
    Não define __init__ nem campos — seguro para herdar no SQLAlchemy.
    """

    def validate(self) -> Optional[Tuple[str, str]]:
        """
        Retorna:
          - None se válido
          - (mensagem, categoria) se inválido (pronto para flash(*tupla))
        """
        # Serie
        if not self.serie or not self.serie.isalpha():
            return (f"Inclua apenas letras no campo Série: {self.serie}", "error")
        if len(self.serie) > 4:
            return ("Série deve ter no máximo 4 caracteres.", "error")

        # Número
        try:
            if int(self.numero) <= 0:
                return ("Número da GTA deve ser maior que zero.", "error")
        except Exception:
            return (f"Inclua apenas números no campo Número da GTA: {self.numero}", "error")

        # Quantidades
        if (self.machos or 0) <= 0 and (self.femeas or 0) <= 0:
            return ("Quantidade de machos e fêmeas zeradas; inclua pelo menos um.", "error")
        if (self.machos or 0) >= 150 or (self.femeas or 0) >= 150:
            return ("Número máximo de suínos por sexo ultrapassado; máximo 150.", "error")

        # Lote
        if (self.lote or 0) < 0:
            return ("Lote não pode ser negativo.", "error")

        # Tipo
        if self.tipo not in ("M", "A", "P"):
            return ("Inclua um tipo de inclusão válido (M, A, P).", "error")

        # Peso
        if self.peso is None:
            return ("Peso inválido.", "error")
        if self.peso < 0:
            return ("Peso não pode ser negativo.", "error")
        if self.peso > 150:
            return ("Peso médio máximo ultrapassado; inclua no máximo 150,00.", "error")

        return None