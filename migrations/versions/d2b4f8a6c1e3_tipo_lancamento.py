"""comercializacao: tipos de lancamento (venda/recebimento/expedicao) e registro.lancamento

Revision ID: d2b4f8a6c1e3
Revises: c0a1e7b5d3f2
Create Date: 2026-10-05

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

from app.seeds.comercializacao_data import TIPOS_LANCAMENTO

revision = 'd2b4f8a6c1e3'
down_revision = 'c0a1e7b5d3f2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    agora = datetime.utcnow()

    with op.batch_alter_table('registro', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lancamento', sa.String(length=30), nullable=True))

    tipo = op.create_table(
        'tipo_lancamento',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('obs', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('codigo', sa.String(length=30), nullable=False),
        sa.Column('nome', sa.String(length=60), nullable=False),
        sa.Column('tipo_transacao_idx', sa.Integer(), nullable=False),
        sa.Column('ambito_idx', sa.Integer(), nullable=False),
        sa.Column('operador_idx', sa.Integer(), nullable=False),
        sa.Column('rotulo_portal', sa.String(length=60), nullable=False),
        sa.UniqueConstraint('codigo'),
    )
    for codigo, nome, t, a, o, rotulo in TIPOS_LANCAMENTO:
        bind.execute(sa.insert(tipo).values(
            codigo=codigo, nome=nome, tipo_transacao_idx=t, ambito_idx=a, operador_idx=o,
            rotulo_portal=rotulo, ativo=True, created_at=agora, updated_at=agora,
        ))

    # registros de comercializacao ja criados eram todos vendas
    reg = sa.table('registro', sa.column('tipo', sa.String), sa.column('lancamento', sa.String))
    bind.execute(sa.update(reg).where(reg.c.tipo == 'comercializacao').values(lancamento='venda'))


def downgrade():
    op.drop_table('tipo_lancamento')
    with op.batch_alter_table('registro', schema=None) as batch_op:
        batch_op.drop_column('lancamento')
