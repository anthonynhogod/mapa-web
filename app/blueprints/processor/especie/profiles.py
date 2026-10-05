from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any
from flask import current_app
import pandas as pd

class SpeciesProfile(ABC):
    """Estratégia de validação por espécie."""
    name: str
    hints: Dict[str, Any] = {}

    @abstractmethod
    def validar_gta(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Retorna um payload with wrap=True: {"records": [...], "meta": {...}}"""
        raise NotImplementedError

    @abstractmethod
    def validar_dif(self, df: pd.DataFrame) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def validar_sif(self, df: pd.DataFrame) -> Dict[str, Any]:
        raise NotImplementedError

_registry: Dict[str, SpeciesProfile] = {}

def register_profile(profile: SpeciesProfile):
    _registry[profile.name.lower()] = profile

def get_profile(especie: str) -> SpeciesProfile:
    key = (especie or '').strip().lower()
    if key in _registry:
        return _registry[key]
    # Fallback para 'suino' se existir
    if 'suino' in _registry:
        current_app.logger.warning(f"Perfil para '{especie}' não encontrado, usando 'suino' como fallback")
        return _registry['suino']
    # Se nenhum perfil, log e raise com mais info
    available = list(_registry.keys())
    current_app.logger.error(f"Nenhum perfil registrado. Disponíveis: {available}")
    raise KeyError(
        f"Perfil de espécie '{especie}' não registrado. "
        f"Perfis disponíveis: {available}"
    )
