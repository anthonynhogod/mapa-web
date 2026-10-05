"""modulo comercializacao: registro.tipo/periodo, vendas_tmp e De->Para (produto/estado)

Revision ID: c0a1e7b5d3f2
Revises: a1c4f7d92e10
Create Date: 2026-10-05

"""
import re
import unicodedata
from datetime import datetime

from alembic import op
import sqlalchemy as sa

from app.seeds.comercializacao_data import ESTADOS, PRODUTOS_VENDA

revision = 'c0a1e7b5d3f2'
down_revision = 'a1c4f7d92e10'
branch_labels = None
depends_on = None


def _norm(txt: str) -> str:
    """Mesma regra de app.utils.format.normalize_str."""
    if not isinstance(txt, str):
        return ""
    txt = txt.lower().strip()
    txt = unicodedata.normalize('NFKD', txt)
    txt = txt.encode('ascii', 'ignore').decode('utf-8')
    txt = re.sub(r'[^a-z0-9\s]', '', txt)
    txt = re.sub(r'\s+', ' ', txt)
    return txt.strip()


def upgrade():
    bind = op.get_bind()
    agora = datetime.utcnow()

    # ---- registro: tipo do modulo + periodo ----
    with op.batch_alter_table('registro', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tipo', sa.String(length=20), server_default='abate', nullable=False))
        batch_op.add_column(sa.Column('periodo_ini', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('periodo_fim', sa.Date(), nullable=True))
        batch_op.create_index(batch_op.f('ix_registro_tipo'), ['tipo'], unique=False)

    # ---- planilha de vendas temporaria ----
    op.create_table(
        'vendas_tmp',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('registro_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('RECEIVED', 'VALIDATED', 'INVALID', 'PROCESSED', name='upload_status'), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['registro_id'], ['registro.id'], name=op.f('fk_vendas_tmp_registro_id_registro'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['usuario.id'], name=op.f('fk_vendas_tmp_uploaded_by_usuario')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_vendas_tmp')),
        sa.UniqueConstraint('registro_id', name='uq_vendas_registro'),
    )
    op.create_index(op.f('ix_vendas_tmp_registro_id'), 'vendas_tmp', ['registro_id'], unique=False)

    # ---- De -> Para ----
    def const_cols():
        return [
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
            sa.Column('obs', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        ]

    produto = op.create_table(
        'produto_venda',
        *const_cols(),
        sa.Column('nome', sa.String(length=160), nullable=False),
        sa.Column('descricao_busca', sa.String(length=180), nullable=False),
        sa.Column('id_mapa', sa.Integer(), nullable=False),
        sa.UniqueConstraint('nome'),
    )
    produto_alias = op.create_table(
        'produto_venda_alias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('produto_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=160), nullable=False),
        sa.Column('alias_norm', sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(['produto_id'], ['produto_venda.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alias_norm'),
    )
    op.create_index('ix_produto_venda_alias_produto_id', 'produto_venda_alias', ['produto_id'])

    estado = op.create_table(
        'estado_venda',
        *const_cols(),
        sa.Column('nome', sa.String(length=60), nullable=False),
        sa.Column('id_mapa', sa.Integer(), nullable=False),
        sa.UniqueConstraint('nome'),
    )
    estado_alias = op.create_table(
        'estado_venda_alias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('estado_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=60), nullable=False),
        sa.Column('alias_norm', sa.String(length=60), nullable=False),
        sa.ForeignKeyConstraint(['estado_id'], ['estado_venda.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alias_norm'),
    )
    op.create_index('ix_estado_venda_alias_estado_id', 'estado_venda_alias', ['estado_id'])

    # ---- seed (reproduz o constantes.py legado) ----
    for nome, descricao, id_mapa in PRODUTOS_VENDA:
        res = bind.execute(sa.insert(produto).values(
            nome=nome, descricao_busca=descricao, id_mapa=id_mapa, ativo=True,
            created_at=agora, updated_at=agora,
        ))
        bind.execute(sa.insert(produto_alias).values(
            produto_id=res.inserted_primary_key[0], alias=nome, alias_norm=_norm(nome),
        ))

    for uf, idx in ESTADOS:
        res = bind.execute(sa.insert(estado).values(
            nome=uf, id_mapa=idx, ativo=True, created_at=agora, updated_at=agora,
        ))
        bind.execute(sa.insert(estado_alias).values(
            estado_id=res.inserted_primary_key[0], alias=uf, alias_norm=_norm(uf),
        ))


def downgrade():
    op.drop_index('ix_estado_venda_alias_estado_id', table_name='estado_venda_alias')
    op.drop_table('estado_venda_alias')
    op.drop_table('estado_venda')
    op.drop_index('ix_produto_venda_alias_produto_id', table_name='produto_venda_alias')
    op.drop_table('produto_venda_alias')
    op.drop_table('produto_venda')
    op.drop_index(op.f('ix_vendas_tmp_registro_id'), table_name='vendas_tmp')
    op.drop_table('vendas_tmp')
    with op.batch_alter_table('registro', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_registro_tipo'))
        batch_op.drop_column('periodo_fim')
        batch_op.drop_column('periodo_ini')
        batch_op.drop_column('tipo')
