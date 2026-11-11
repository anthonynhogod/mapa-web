"""produto_producao.ingrediente: desempata linhas da busca com o mesmo id (MIX x Unico)

Revision ID: b7e1c4d9a2f6
Revises: a9d3e5f7b1c2
Create Date: 2026-11-11

"""
from alembic import op
import sqlalchemy as sa

revision = 'b7e1c4d9a2f6'
down_revision = 'a9d3e5f7b1c2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('produto_producao', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ingrediente', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('produto_producao', schema=None) as batch_op:
        batch_op.drop_column('ingrediente')
