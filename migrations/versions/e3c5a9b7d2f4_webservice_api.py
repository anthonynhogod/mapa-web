"""webservice do MAPA: ids da API nas constantes, dados do estabelecimento, registro.api_id

Revision ID: e3c5a9b7d2f4
Revises: d2b4f8a6c1e3
Create Date: 2026-10-05

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

from app.seeds.comercializacao_data import TIPOS_LANCAMENTO_API, TIPOS_LANCAMENTO_EXTRAS

revision = 'e3c5a9b7d2f4'
down_revision = 'd2b4f8a6c1e3'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    agora = datetime.utcnow()

    with op.batch_alter_table('mapa_credencial', schema=None) as b:
        b.add_column(sa.Column('cpf_cnpj', sa.String(length=14), nullable=True))
        b.add_column(sa.Column('ambito', sa.String(length=3), nullable=True))
        b.add_column(sa.Column('cod_uf', sa.String(length=2), nullable=True))
        b.add_column(sa.Column('cod_municipio_ibge', sa.String(length=7), nullable=True))

    with op.batch_alter_table('registro', schema=None) as b:
        b.add_column(sa.Column('api_id', sa.Integer(), nullable=True))

    with op.batch_alter_table('diagnostico', schema=None) as b:
        b.add_column(sa.Column('id_api', sa.Integer(), nullable=True))
    with op.batch_alter_table('parte_afetada', schema=None) as b:
        b.add_column(sa.Column('id_api', sa.Integer(), nullable=True))
        b.alter_column('id_mapa', existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table('destino', schema=None) as b:
        b.add_column(sa.Column('id_api', sa.Integer(), nullable=True))
        b.alter_column('id_mapa', existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table('produto_venda', schema=None) as b:
        b.add_column(sa.Column('cod_api', sa.Integer(), nullable=True))
        b.alter_column('id_mapa', existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table('tipo_lancamento', schema=None) as b:
        b.add_column(sa.Column('api_tipo', sa.String(length=10), server_default='VENDA', nullable=False))
        b.add_column(sa.Column('api_nacional', sa.Boolean(), server_default='1', nullable=False))
        b.add_column(sa.Column('api_tipo_operador', sa.String(length=40), server_default='UF', nullable=False))
        b.add_column(sa.Column('api_produto_tipo', sa.String(length=20), nullable=True))
        for c in ('tipo_transacao_idx', 'ambito_idx', 'operador_idx'):
            b.alter_column(c, existing_type=sa.Integer(), nullable=True)

    especie = op.create_table(
        'especie_api',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ativo', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('obs', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('nome', sa.String(length=60), nullable=False),
        sa.Column('id_api', sa.Integer(), nullable=True),
        sa.UniqueConstraint('nome'),
    )
    bind.execute(sa.insert(especie).values(nome='suino', ativo=True, created_at=agora, updated_at=agora))

    tipo = sa.table(
        'tipo_lancamento',
        sa.column('codigo', sa.String), sa.column('nome', sa.String), sa.column('rotulo_portal', sa.String),
        sa.column('api_tipo', sa.String), sa.column('api_nacional', sa.Boolean),
        sa.column('api_tipo_operador', sa.String), sa.column('api_produto_tipo', sa.String),
        sa.column('ativo', sa.Boolean), sa.column('obs', sa.String),
        sa.column('created_at', sa.DateTime), sa.column('updated_at', sa.DateTime),
    )
    for codigo, (at, nac, opr, pt) in TIPOS_LANCAMENTO_API.items():
        bind.execute(sa.update(tipo).where(tipo.c.codigo == codigo).values(
            api_tipo=at, api_nacional=nac, api_tipo_operador=opr, api_produto_tipo=pt))
    for codigo, nome, rotulo, at, nac, opr in TIPOS_LANCAMENTO_EXTRAS:
        bind.execute(sa.insert(tipo).values(
            codigo=codigo, nome=nome, rotulo_portal=rotulo, api_tipo=at, api_nacional=nac,
            api_tipo_operador=opr, api_produto_tipo=None, ativo=False,
            obs='Sem layout de planilha ainda; ative quando houver parser.',
            created_at=agora, updated_at=agora))


def downgrade():
    bind = op.get_bind()
    tipo = sa.table('tipo_lancamento', sa.column('codigo', sa.String))
    bind.execute(sa.delete(tipo).where(tipo.c.codigo.in_([t[0] for t in TIPOS_LANCAMENTO_EXTRAS])))
    op.drop_table('especie_api')
    with op.batch_alter_table('tipo_lancamento', schema=None) as b:
        for c in ('api_produto_tipo', 'api_tipo_operador', 'api_nacional', 'api_tipo'):
            b.drop_column(c)
    with op.batch_alter_table('produto_venda', schema=None) as b:
        b.drop_column('cod_api')
    with op.batch_alter_table('destino', schema=None) as b:
        b.drop_column('id_api')
    with op.batch_alter_table('parte_afetada', schema=None) as b:
        b.drop_column('id_api')
    with op.batch_alter_table('diagnostico', schema=None) as b:
        b.drop_column('id_api')
    with op.batch_alter_table('registro', schema=None) as b:
        b.drop_column('api_id')
    with op.batch_alter_table('mapa_credencial', schema=None) as b:
        for c in ('cod_municipio_ibge', 'cod_uf', 'ambito', 'cpf_cnpj'):
            b.drop_column(c)
