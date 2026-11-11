"""mapa de producao: De->Para de produtos (produto_producao + apelidos)

Sem dados iniciais: os vinculos (produto da planilha -> descricao de busca + id do portal) sao
cadastrados em Admin > Constantes > Produtos (producao).

Revision ID: a9d3e5f7b1c2
Revises: f7a1c9d3b5e2
Create Date: 2026-11-10

"""
from alembic import op
import sqlalchemy as sa

revision = 'a9d3e5f7b1c2'
down_revision = 'f7a1c9d3b5e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'produto_producao',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('obs', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('nome', sa.String(length=160), nullable=False),
        sa.Column('descricao_busca', sa.String(length=180), nullable=False),
        sa.Column('id_mapa', sa.Integer(), nullable=True),
        sa.UniqueConstraint('nome'),
    )
    op.create_table(
        'produto_producao_alias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('produto_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=160), nullable=False),
        sa.Column('alias_norm', sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(['produto_id'], ['produto_producao.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alias_norm'),
    )
    op.create_index('ix_produto_producao_alias_produto_id', 'produto_producao_alias', ['produto_id'])
    op.create_index('ix_produto_producao_alias_alias_norm', 'produto_producao_alias', ['alias_norm'])


def downgrade():
    op.drop_index('ix_produto_producao_alias_alias_norm', table_name='produto_producao_alias')
    op.drop_index('ix_produto_producao_alias_produto_id', table_name='produto_producao_alias')
    op.drop_table('produto_producao_alias')
    op.drop_table('produto_producao')
