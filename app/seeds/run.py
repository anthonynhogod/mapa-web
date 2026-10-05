"""Seeds para bancos NOVOS (`flask init-db`). Idempotente: so insere o que falta.

Bancos existentes recebem os mesmos dados via migrations (`flask db upgrade`).
"""
import importlib.util
import os

from app.extensions import db
from app.utils.format import normalize_str


def _carregar_migration_abate():
    """Reaproveita os dados do seed das constantes de abate (fonte unica)."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "migrations", "versions",
        "a1c4f7d92e10_constantes_administraveis.py",
    )
    spec = importlib.util.spec_from_file_location("_seed_abate", os.path.abspath(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _upsert(model, alias_model, fk, nome_field, nome, aliases, **campos):
    chave = normalize_str(nome)
    existente = alias_model.query.filter_by(alias_norm=chave).first()
    if existente:
        return False
    item = model(**{nome_field: nome}, ativo=campos.pop("ativo", True), **campos)
    db.session.add(item)
    db.session.flush()
    vistos = set()
    for alias in list(aliases) + [nome]:
        norm = normalize_str(alias)
        if not norm or norm in vistos:
            continue
        vistos.add(norm)
        if alias_model.query.filter_by(alias_norm=norm).first():
            continue
        db.session.add(alias_model(**{fk: item.id}, alias=alias, alias_norm=norm))
    return True


def seed_abate() -> int:
    from app.models import (
        Diagnostico, DiagnosticoAlias, ParteAfetada, ParteAfetadaAlias,
        Destino, DestinoAlias, CondenaParte,
    )
    mig = _carregar_migration_abate()
    n = 0
    for descricao, aliases in mig.DIAGNOSTICOS.items():
        n += _upsert(Diagnostico, DiagnosticoAlias, "diagnostico_id", "descricao_mapa", descricao, aliases)
    for nome, id_mapa, ativo, aliases, obs in mig.PARTES:
        n += _upsert(ParteAfetada, ParteAfetadaAlias, "parte_id", "nome", nome, aliases,
                     id_mapa=id_mapa, ativo=ativo, obs=obs)
    for nome, id_mapa, ativo, aliases in mig.DESTINOS:
        n += _upsert(Destino, DestinoAlias, "destino_id", "nome", nome, aliases,
                     id_mapa=id_mapa, ativo=ativo)
    for nome, slots in mig.CONDENA_PARTES:
        if not CondenaParte.query.filter_by(nome=nome).first():
            db.session.add(CondenaParte(nome=nome, slots=slots, ativo=True))
            n += 1
    db.session.commit()
    return n


def seed_comercializacao() -> int:
    from app.models import ProdutoVenda, ProdutoVendaAlias, EstadoVenda, EstadoVendaAlias, TipoLancamento
    from app.seeds.comercializacao_data import ESTADOS, PRODUTOS_VENDA, TIPOS_LANCAMENTO
    n = 0
    for codigo, nome, t, a, o, rotulo in TIPOS_LANCAMENTO:
        if not TipoLancamento.query.filter_by(codigo=codigo).first():
            db.session.add(TipoLancamento(codigo=codigo, nome=nome, tipo_transacao_idx=t,
                                          ambito_idx=a, operador_idx=o, rotulo_portal=rotulo))
            n += 1
    for nome, descricao, id_mapa in PRODUTOS_VENDA:
        n += _upsert(ProdutoVenda, ProdutoVendaAlias, "produto_id", "nome", nome, [],
                     descricao_busca=descricao, id_mapa=id_mapa)
    for uf, idx in ESTADOS:
        n += _upsert(EstadoVenda, EstadoVendaAlias, "estado_id", "nome", uf, [], id_mapa=idx)
    db.session.commit()
    return n
