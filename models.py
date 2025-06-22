from db import db 
from flask_login import UserMixin

class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(30), unique=True, nullable=False)
    senha = db.Column(db.String(), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)

class Admin(db.Model):
    __tablename__ = "admins"

    id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), primary_key=True)
    usuario = db.relationship("Usuario")

class Empresa(db.Model):
    __tablename__ = "empresas"

    id = db.Column(db.ForeignKey("usuarios.id"), primary_key=True)
    email = db.Column(db.String(128), nullable=False)
    senha = db.Column(db.String(), nullable=False)
    tipo = db.Column(db.String(10), default="Empresa", nullable=False)
    cnpj = db.Column(db.String(20))
    endereco = db.Column(db.String(128))
    telefone = db.Column(db.String(20))

    usuario = db.relationship("Usuario")
    produtos = db.relationship("Produto", backref="empresa")

class Cliente(db.Model):
    __tablename__ = "clientes"

    id = db.Column(db.ForeignKey("usuarios.id"), primary_key=True)
    email = db.Column(db.String(128), nullable=False)
    senha = db.Column(db.String(), nullable=False)
    endereco_id = db.Column(db.Integer, db.ForeignKey("enderecos_cliente.id"))
    telefone = db.Column(db.String(20))
    nome = db.Column(db.String(64))

    usuario = db.relationship("Usuario")
    endereco = db.relationship("EnderecoCliente", backref="clientes")
    pedidos = db.relationship("PedidoCliente", backref="cliente")
    avaliacoes = db.relationship("AvaliacaoProduto", backref="cliente")

class EnderecoCliente(db.Model):
    __tablename__ = "enderecos_cliente"

    id = db.Column(db.Integer, primary_key=True)
    rua = db.Column(db.String(128))
    comp = db.Column(db.String(64))
    bairro = db.Column(db.String(64))
    cidade = db.Column(db.String(64))
    numero = db.Column(db.String(10))

class Produto(db.Model):
    __tablename__ = "produtos"

    id = db.Column(db.Integer, primary_key=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    nome = db.Column(db.String(128), nullable=False)
    id_ingrediente = db.Column(db.Integer)  # opcional se for lista separada
    preco = db.Column(db.Float)
    imagem = db.Column(db.String(256))
    avaliacao_id = db.Column(db.Integer)

    ingredientes = db.relationship("IngredienteProduto", backref="produto")
    avaliacoes = db.relationship("AvaliacaoProduto", backref="produto")

class IngredienteProduto(db.Model):
    __tablename__ = "ingredientes_produto"

    id = db.Column(db.Integer, primary_key=True)
    id_produto = db.Column(db.Integer, db.ForeignKey("produtos.id"), nullable=False)
    nome = db.Column(db.String(128))
    quantidade = db.Column(db.String(64))

class AvaliacaoProduto(db.Model):
    __tablename__ = "avaliacoes_produto"

    id = db.Column(db.Integer, primary_key=True)
    id_produto = db.Column(db.Integer, db.ForeignKey("produtos.id"), nullable=False)
    id_cliente = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    conteudo = db.Column(db.Text)
    estrelas = db.Column(db.Integer)

class PedidoCliente(db.Model):
    __tablename__ = "pedidos_cliente"

    id = db.Column(db.Integer, primary_key=True)
    id_cliente = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    observacao = db.Column(db.Text)

    produtos = db.relationship("ProdutoPedido", backref="pedido")

class ProdutoPedido(db.Model):
    __tablename__ = "produtos_pedido"

    id = db.Column(db.Integer, primary_key=True)
    id_produto = db.Column(db.Integer, db.ForeignKey("produtos.id"), nullable=False)
    id_pedido = db.Column(db.Integer, db.ForeignKey("pedidos_cliente.id"), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False)

    produto = db.relationship("Produto")

class DadosPagamento(db.Model):
    __tablename__ = "dados_pagamento"

    id = db.Column(db.Integer, primary_key=True)
    # Adicione campos conforme o método de pagamento (cartão, boleto, etc.)
