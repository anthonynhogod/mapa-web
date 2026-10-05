"""constantes administraveis (diagnostico/parte/destino/condena)

Tira os dicionarios de descricoes, partes e destinos do codigo e move para
tabelas administraveis em Admin > CONSTANTES.

O seed reproduz exatamente os valores que rodavam em app/logic/preview_utils.py,
consolidando as divergencias que existiam contra abate/constantes/diagnosticos.py:
  - 'Rins' entra como alias de 'Rim' (antes 'rins' nao resolvia -> id 0)
  - 'Baco' (id 16) existia so no dicionario legado; entra INATIVO, pois nao e usado
  - 'Utero' (id 25) nao existia em lugar nenhum e virava id 0 silenciosamente

Revision ID: a1c4f7d92e10
Revises: 714ff0b8a088
Create Date: 2026-09-15

"""
import re
import unicodedata
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'a1c4f7d92e10'
down_revision = '714ff0b8a088'
branch_labels = None
depends_on = None


def _norm(txt: str) -> str:
    """Mesma regra de app.utils.format.normalize_str (replicada: migration nao importa app)."""
    if not isinstance(txt, str):
        return ""
    txt = txt.lower().strip()
    txt = unicodedata.normalize('NFKD', txt)
    txt = txt.encode('ascii', 'ignore').decode('utf-8')
    txt = re.sub(r'[^a-z0-9\s]', '', txt)
    txt = re.sub(r'\s+', ' ', txt)
    return txt.strip()


# ---------------------------------------------------------------------------
# Dados do seed
# ---------------------------------------------------------------------------

# descricao_mapa -> [grafias aceitas nas planilhas]
DIAGNOSTICOS = {
    'ABSCESSO (MAMÍFEROS)': ['ABCESSO'],
    'ADERÊNCIA DE PLEURA (SECA)': ['ADERÊNCIA DE PLEURA (SECA)'],
    'ADERÊNCIA DE PLEURA (ÚMIDA)': ['ADERÊNCIA DE PLEURA (ÚMIDA)'],
    'ADERÊNCIA DE PLEURA (PURULENTA)': ['ADERÊNCIA DE PLEURA (PURULENTA)'],
    'ALTERAÇÃO LINFÁTICA INESPECÍFICA': ['ALTERAÇÃO LINFÁTICA INESPECÍFICA'],
    'ALTERAÇÃO RESTRITA': ['ALTERAÇÃO RESTRITA'],
    'ALTERAÇÕES MUSCULARES (RIGIDEZ ATÍPICA)': ['ALTERAÇÕES MUSCULARES (RIGIDEZ ATÍPICA)'],
    'ARTRITE (UMA ARTICULAÇÃO)': ['ARTRITE (UMA ARTICULAÇÃO)'],
    'ARTRITE (MAIS DE UMA ARTICULAÇÃO)': ['ARTRITE (MAIS DE UMA ARTICULAÇÃO)'],
    'CASTRAÇÃO INADEQUADA (SUÍNO)': ['CASTRAÇÃO INADEQUADA'],
    'CAQUEXIA': ['CAQUEXIA'],
    'CANIBALISMO': ['CANIBALISMO'],
    'COLORAÇÃO ANORMAL': ['COLORAÇÃO ANORMAL'],
    # absorve o antigo _CANON_MAP: todas as variantes viram alias do mesmo item
    'CONTAMINAÇÃO GASTROINTESTINAL E BILIAR': [
        'CONTAMINAÇÃO GASTROINTESTINAL/BILIAR',
        'CONTAMINAÇÃO GASTROINTESTINAL E BILIAR',
        'CONT. GASTROINTESTINAL E BILIAR',
        'CONT GASTROINTESTINAL BILIAR',
        'CONTAMINAÇÃO GASTROINTESTINAL BILIAR',
    ],
    'CONTAMINAÇÃO NÃO GASTROINTESTINAL': ['CONTAMINAÇÃO NÃO GASTROINTESTINAL'],
    'CRIPTORQUIDA (SUÍNO)': ['CRIPTORQUIDISMO'],
    'FALHA TECNOLÓGICA': ['FALHAS TECNOLÓGICAS'],
    'HÉRNIA (DETECTADA NO ANTE MORTEM)': ['HÉRNIA (ANTE MORTEM)'],
    'LESÃO DE PELE': ['LESÃO DE PELE'],
    'LESÃO INFLAMATÓRIA': ['LESÃO INFLAMATÓRIA'],
    'LESÃO TRAUMÁTICA': ['LESÃO TRAUMÁTICA'],
    'LESÃO TRAUMÁTICA (DETECTADA NO ANTE MORTEM)': ['LESÃO TRAUMÁTICA (ANTE MORTEM)'],
    'LINFADENITE GRANULOMATOSA': ['LINFADENITE GRANULOMATOSA'],
    'MAGREZA': ['MAGREZA'],
    'MORTO (NO PRÉ ABATE)': ['MORTO NO PRÉ ABATE'],
    'MORTO (NO TRANSPORTE)': ['MORTO NO TRANSPORTE'],
    'NEOPLASIA': ['NEOPLASIA'],
    'PROLAPSO (ANTE MORTEM)': ['PROLAPSO (ANTE MORTEM)'],
    'SEPTICEMIA': ['SEPTICEMIA'],
    'ASPECTO REPUGNANTE (POST MORTEM)': ['ODOR ESTRANHO'],
    'ESTRESSE/FADIGA (NO ANTE MORTEM)': ['ALTERAÇÕES MUSCULARES (STRESS/FADIGA)'],
    'EVISCERACAO RETARDADA': ['EVISCERAÇÃO'],
}

# nome, id_mapa, ativo, aliases, obs
PARTES = [
    ('Carcaça', 1, True, [], None),
    ('Meia Carcaça', 2, True, [], None),
    ('Quarto Dianteiro', 3, True, [], None),
    ('Quarto Traseiro', 4, True, [], None),
    ('Cabeça', 5, True, [], None),
    ('Língua', 7, True, [], None),
    ('Pulmão', 8, True, [], None),
    ('Coração', 9, True, [], None),
    ('Intestino', 11, True, [], None),
    ('Estômago', 11, True, [], 'Compartilha o id 11 com Intestino (confirmado).'),
    ('Fígado', 12, True, [], None),
    ('Rim', 13, True, ['Rins'], 'Alias "Rins" adicionado: antes nao resolvia e virava id 0.'),
    ('Cauda (Rabo)', 14, True, ['Cauda', 'Rabo'], None),
    ('Baço', 16, False, [], 'Existia so no dicionario legado e nao e usado. Inativo.'),
    ('Útero', 25, True, [], 'Nao existia em nenhum dicionario: virava id 0 silenciosamente.'),
]

DESTINOS = [
    ('Condenação', 6, True, []),
    ('Condenação Parcial', 7, True, []),
    ('Condenação Total', 8, True, []),
    ('Cozimento', 10, True, []),
    ('Esterilização', 12, True, []),
    ('Liberado', 16, True, []),
    ('Tratamento Pelo Frio', 21, True, []),
    ('Salga', 39, True, []),
]

CONDENA_PARTES = [
    ('Cabeça', [1, 2, 3]),
    ('Útero', [6, 7, 8]),
    ('Intestino', [11, 12, 13]),
    ('Língua', [16, 17, 18]),
    ('Coração', [23, 21, 22]),
    ('Pulmão', [28, 26, 27]),
    ('Fígado', [33, 31, 32]),
    ('Carcaça', [38, 36, 37]),
    ('Rins', [43, 41, 42]),
]


def upgrade():
    bind = op.get_bind()
    agora = datetime.utcnow()

    diagnostico = op.create_table(
        'diagnostico',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('descricao_mapa', sa.String(length=180), nullable=False),
        sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('obs', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('descricao_mapa'),
    )

    diagnostico_alias = op.create_table(
        'diagnostico_alias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('diagnostico_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=180), nullable=False),
        sa.Column('alias_norm', sa.String(length=180), nullable=False),
        sa.ForeignKeyConstraint(['diagnostico_id'], ['diagnostico.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alias_norm'),
    )
    op.create_index('ix_diagnostico_alias_diagnostico_id', 'diagnostico_alias', ['diagnostico_id'])

    parte = op.create_table(
        'parte_afetada',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=120), nullable=False),
        sa.Column('id_mapa', sa.Integer(), nullable=False),
        sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('obs', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nome'),
    )

    parte_alias = op.create_table(
        'parte_afetada_alias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('parte_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=120), nullable=False),
        sa.Column('alias_norm', sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(['parte_id'], ['parte_afetada.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alias_norm'),
    )
    op.create_index('ix_parte_afetada_alias_parte_id', 'parte_afetada_alias', ['parte_id'])

    destino = op.create_table(
        'destino',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=120), nullable=False),
        sa.Column('id_mapa', sa.Integer(), nullable=False),
        sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('obs', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nome'),
    )

    destino_alias = op.create_table(
        'destino_alias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('destino_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=120), nullable=False),
        sa.Column('alias_norm', sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(['destino_id'], ['destino.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alias_norm'),
    )
    op.create_index('ix_destino_alias_destino_id', 'destino_alias', ['destino_id'])

    condena = op.create_table(
        'condena_parte',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=120), nullable=False),
        sa.Column('slots', sa.JSON(), nullable=False),
        sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('obs', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nome'),
    )

    # ---------------- seed ----------------

    for descricao, aliases in DIAGNOSTICOS.items():
        res = bind.execute(
            sa.insert(diagnostico).values(
                descricao_mapa=descricao, ativo=True,
                created_at=agora, updated_at=agora,
            )
        )
        did = res.inserted_primary_key[0]
        # a propria descricao oficial sempre resolve para ela mesma
        vistos = set()
        for alias in list(aliases) + [descricao]:
            chave = _norm(alias)
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            bind.execute(sa.insert(diagnostico_alias).values(
                diagnostico_id=did, alias=alias, alias_norm=chave,
            ))

    for nome, id_mapa, ativo, aliases, obs in PARTES:
        res = bind.execute(sa.insert(parte).values(
            nome=nome, id_mapa=id_mapa, ativo=ativo, obs=obs,
            created_at=agora, updated_at=agora,
        ))
        pid = res.inserted_primary_key[0]
        vistos = set()
        for alias in list(aliases) + [nome]:
            chave = _norm(alias)
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            bind.execute(sa.insert(parte_alias).values(
                parte_id=pid, alias=alias, alias_norm=chave,
            ))

    for nome, id_mapa, ativo, aliases in DESTINOS:
        res = bind.execute(sa.insert(destino).values(
            nome=nome, id_mapa=id_mapa, ativo=ativo,
            created_at=agora, updated_at=agora,
        ))
        dsid = res.inserted_primary_key[0]
        vistos = set()
        for alias in list(aliases) + [nome]:
            chave = _norm(alias)
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            bind.execute(sa.insert(destino_alias).values(
                destino_id=dsid, alias=alias, alias_norm=chave,
            ))

    for nome, slots in CONDENA_PARTES:
        bind.execute(sa.insert(condena).values(
            nome=nome, slots=slots, ativo=True,
            created_at=agora, updated_at=agora,
        ))


def downgrade():
    op.drop_table('condena_parte')
    op.drop_index('ix_destino_alias_destino_id', table_name='destino_alias')
    op.drop_table('destino_alias')
    op.drop_table('destino')
    op.drop_index('ix_parte_afetada_alias_parte_id', table_name='parte_afetada_alias')
    op.drop_table('parte_afetada_alias')
    op.drop_table('parte_afetada')
    op.drop_index('ix_diagnostico_alias_diagnostico_id', table_name='diagnostico_alias')
    op.drop_table('diagnostico_alias')
    op.drop_table('diagnostico')
