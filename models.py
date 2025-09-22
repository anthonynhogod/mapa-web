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


class EnderecoCliente(db.Model):
    __tablename__ = "enderecos_cliente"

    id = db.Column(db.Integer, primary_key=True)
    rua = db.Column(db.String(128))
    comp = db.Column(db.String(64))
    bairro = db.Column(db.String(64))
    cidade = db.Column(db.String(64))
    numero = db.Column(db.String(10))

