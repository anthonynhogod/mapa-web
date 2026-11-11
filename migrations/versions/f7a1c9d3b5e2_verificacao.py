"""verificacao: conferencia do lancamento de comercializacao com o relatorio do MAPA

Revision ID: f7a1c9d3b5e2
Revises: e3c5a9b7d2f4
Create Date: 2026-11-09

"""
from alembic import op
import sqlalchemy as sa

revision = 'f7a1c9d3b5e2'
down_revision = 'e3c5a9b7d2f4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'verificacao',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('usuario.id', ondelete='CASCADE'), nullable=False),
        sa.Column('registro_id', sa.Integer(), sa.ForeignKey('registro.id', ondelete='SET NULL'), nullable=True),
        sa.Column('job_id', sa.Integer(), nullable=True),
        sa.Column('origem_base', sa.String(length=30), nullable=False),
        sa.Column('arquivo_mapa', sa.String(length=255), nullable=False),
        sa.Column('arquivo_base', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('resumo', sa.JSON(), nullable=False),
        sa.Column('resultado', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table('verificacao', schema=None) as batch_op:
        batch_op.create_index('ix_verificacao_user_id', ['user_id'])
        batch_op.create_index('ix_verificacao_registro_id', ['registro_id'])
        batch_op.create_index('ix_verificacao_created_at', ['created_at'])


def downgrade():
    with op.batch_alter_table('verificacao', schema=None) as batch_op:
        batch_op.drop_index('ix_verificacao_created_at')
        batch_op.drop_index('ix_verificacao_registro_id')
        batch_op.drop_index('ix_verificacao_user_id')
    op.drop_table('verificacao')
