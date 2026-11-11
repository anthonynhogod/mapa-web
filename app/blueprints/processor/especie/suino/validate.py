import json
import pandas as pd
from app.blueprints.processor.especie.profiles import SpeciesProfile
# Reutiliza os validadores já existentes no pacote dto
from .dto import GtaValidator, DifValidator, SifSelectionMapper

class SuinoProfile(SpeciesProfile):
    name = 'suino'
    hints = {'gta': {'has_sex_breakdown': True, 'peso_required': True}}

    def validar_gta(self, df: pd.DataFrame):
        v = GtaValidator(df)
        return json.loads(v.to_json(strict=False, best_effort_when_empty=True, wrap=True, indent=2))

    def validar_dif(self, df: pd.DataFrame):
        v = DifValidator(df)
        return json.loads(v.to_json(strict=False, best_effort_when_empty=True, wrap=True))

    def validar_sif(self, df: pd.DataFrame):
        m = SifSelectionMapper(df)
        return json.loads(m.to_json(destino_default='liberado', emergencia_default=0,
                                    incluir_total=False, strict=False,
                                    best_effort_when_empty=False, wrap=True))


from app.blueprints.processor.especie.profiles import register_profile
register_profile(SuinoProfile())
