# models.py
import os
import socket
from flask_login import UserMixin
from sqlalchemy import Enum, UniqueConstraint, Index, func, ForeignKey
from app import db
from app.validators.gta_tmp_validator import GtaTmpValidator
from sqlalchemy.dialects.sqlite import JSON  # ou postgresql.JSONB se for Postgres
from enum import Enum as PyEnum 
from sqlalchemy.orm import relationship, declared_attr, declarative_mixin
from cryptography.fernet import Fernet
from datetime import datetime
from flask import current_app

class Empresa(db.Model):
    __tablename__ = "empresa"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(128), nullable=False)
    cnpj = db.Column(db.String(14), nullable=False, unique=True)  # 14 chars, com zeros à esquerda
    endereco = db.Column(db.String(256), nullable=True)

    usuarios = db.relationship("Usuario", back_populates="empresa", cascade="all,delete-orphan")

    def __repr__(self):
        return f"<Empresa id={self.id} cnpj={self.cnpj}>"


class MapaCredencial(db.Model):
    __tablename__ = "mapa_credencial"

    id = db.Column(db.Integer, primary_key=True)

    owner_user_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id", ondelete="CASCADE"),
        nullable=False,
        unique=True
    )

    usuario_app = db.Column(db.String(100), nullable=False)
    _senha_enc = db.Column("senha_enc", db.LargeBinary, nullable=False)
    numero_sif = db.Column(db.String(20), nullable=False)
    especie = db.Column(db.String(50), nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # NOVOS CAMPOS
    validado = db.Column(db.Boolean, nullable=False, server_default="0")  # False por padrão
    validate_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.String(255), nullable=True)

    owner = db.relationship("Usuario", back_populates="credencial_mapa", uselist=False)

    # --- criptografia ---

    @staticmethod
    def _cipher():
        # Busca a chave do app.config; fallback para variável de ambiente (opcional)
        key = None
        try:
            key = current_app.config.get("FERNET_KEY")
        except Exception:
            pass
        if not key:
            import os
            key = os.getenv("FERNET_KEY")
        if not key:
            raise RuntimeError("FERNET_KEY não configurada (app.config ou env FERNET_KEY)")
        if isinstance(key, str):
            key = key.encode()
        return Fernet(key)


    @property
    def senha(self) -> str:
        if not self._senha_enc:
            return ""
        return self._cipher().decrypt(self._senha_enc).decode()

    @senha.setter
    def senha(self, raw: str):
        self._senha_enc = self._cipher().encrypt(raw.encode())

    def mark_validation(self, ok: bool, error: str | None = None):
        self.validado = bool(ok)
        self.validate_at = datetime.utcnow()
        self.last_error = (error or None)[:255] if error else None

    def __repr__(self):
        return f"<MapaCredencial id={self.id} owner={self.owner_user_id} sif={self.numero_sif} especie={self.especie} validado={self.validado}>"

class Role(PyEnum):
    USER  = "USER"
    ADMIN = "ADMIN"

# Em Usuario, torne a relação 1:1
class Usuario(UserMixin, db.Model):
    __tablename__ = "usuario"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    senha = db.Column(db.String(256), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresa.id"), nullable=True)
    role = db.Column(db.Enum(Role), nullable=False, server_default=Role.USER.value)
    is_active = db.Column(db.Boolean, nullable=False, server_default="1")

    empresa = db.relationship("Empresa", back_populates="usuarios")
    registros = db.relationship("Registro", back_populates="usuario", cascade="all,delete-orphan")


    # antes: credenciais_mapa (lista). Agora: 1:1
    credencial_mapa = db.relationship(
        "MapaCredencial",
        back_populates="owner",
        uselist=False,
        cascade="all, delete-orphan"
    )
    
    def get_id(self):
        return str(self.id)

    @property
    def is_admin(self) -> bool:
        try:
            return self.role == Role.ADMIN
        except Exception:
            return False

    # Flask-Login usa 'is_active' como propriedade
    def is_active(self) -> bool:  # type: ignore[override]
        return bool(self.is_active)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), index=True, nullable=True)
    action = db.Column(db.String(64), nullable=False)           # ex.: 'login', 'registro:create', 'upload:gta'
    entity = db.Column(db.String(64), nullable=True)            # ex.: 'Registro', 'GtaUploadTmp'
    entity_id = db.Column(db.Integer, nullable=True)
    meta = db.Column(db.JSON, nullable=True)                    # detalhes úteis
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("Usuario", backref="audits", lazy="joined")


class Registro(db.Model):
    __tablename__ = "registro"
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(Enum("AT", "FZ", "PT","ER", name="registro_status"), default="AT", nullable=False)
    especie = db.Column(db.String(64), nullable=False)
    obs = db.Column(db.String(256), nullable=True)
    recurso = db.Column(db.String(16), nullable=False, default="web")

    # Modulo do lancamento: "abate" (padrao) ou "comercializacao".
    # Na comercializacao, `data` guarda o inicio do periodo e `periodo_fim` o fim.
    tipo = db.Column(db.String(20), nullable=False, default="abate", server_default="abate", index=True)
    periodo_ini = db.Column(db.Date, nullable=True)
    periodo_fim = db.Column(db.Date, nullable=True)

    data_registro = db.Column(db.DateTime, nullable=False, server_default=func.now())
    data_inicio = db.Column(db.DateTime, nullable=True)
    data_fim = db.Column(db.DateTime, nullable=True)

    user_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), index=True)

    usuario = db.relationship("Usuario", back_populates="registros")

    # >>> Coleção no lado do Registro (N) com nome **gtas**
    gtas = db.relationship(
        "GtaTemp",
        back_populates="registro",
        cascade="all,delete-orphan",
        # opcional: carregamento otimizado
        lazy="selectin",
    )
    gta_uploads = relationship(
        "GtaUploadTmp",
        back_populates="registro",
        cascade="all, delete-orphan",
        single_parent=True,     
    )

    dif_uploads = relationship(
        "DifTmp",
        back_populates="registro",
        cascade="all, delete-orphan",
        single_parent=True,
    )

    sif_uploads = relationship(
        "SifTmp",
        back_populates="registro",
        cascade="all, delete-orphan",
        single_parent=True,
    )

    vendas_uploads = relationship(
        "VendasTmp",
        back_populates="registro",
        cascade="all, delete-orphan",
        single_parent=True,
    )

    exec_jobs = relationship(
        "ExecJob",
        back_populates="registro",
        cascade="all, delete-orphan",
        single_parent=False,
    )

    @property
    def is_comercializacao(self) -> bool:
        return self.tipo == "comercializacao"

    @property
    def periodo_label(self) -> str:
        """Texto de exibicao: dia (abate) ou periodo (comercializacao)."""
        if self.is_comercializacao and self.periodo_ini and self.periodo_fim:
            return f"{self.periodo_ini:%d/%m/%Y} a {self.periodo_fim:%d/%m/%Y}"
        return f"{self.data:%d/%m/%Y}" if self.data else "-"

    def __repr__(self):
        return f"<Registro id={self.id} tipo={self.tipo} status={self.status} data={self.data}>"

def get_last_register_by_id(registro_id, user_id):
    return Registro.query.filter_by(id=registro_id, user_id=user_id).first_or_404()

def get_all_register_by_id(user_id):
    return Registro.query.filter_by(user_id=user_id).all()

class GtaTemp(GtaTmpValidator, db.Model):
    __tablename__ = "gta_tmp"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    serie  = db.Column(db.String(4), nullable=False)
    machos = db.Column(db.Integer, nullable=False)
    femeas = db.Column(db.Integer, nullable=False)
    lote   = db.Column(db.Integer, nullable=False)
    peso   = db.Column(db.Float, nullable=False)
    tipo   = db.Column(Enum("A", "M", "P", name="tipo_registro"), default="A", nullable=False)

    registro_id = db.Column(db.Integer, db.ForeignKey("registro.id"), index=True, nullable=False)
    registro = db.relationship("Registro", back_populates="gtas")

    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("numero", "serie", "registro_id", name="uq_gta_tmp_num_serie_reg"),
        Index("ix_gta_tmp_tipo", "tipo"),
    )

    def __repr__(self):
        return f"<GtaTemp id={self.id} num={self.numero}/{self.serie} tipo={self.tipo}>"

    # Helpers para parse de PT-BR -> float
    @staticmethod
    def parse_peso_ptbr(txt: str):
        if txt is None:
            return None
        s = txt.strip().replace(".", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
        

class UploadStatus(str, PyEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    INVALID = "invalid"
    PROCESSED = "processed"

@declarative_mixin
class _BaseTmp:
    id          = db.Column(db.Integer, primary_key=True)
    registro_id = db.Column(
        db.Integer,
        db.ForeignKey("registro.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    filename    = db.Column(db.String(255), nullable=False)
    model       = db.Column(db.String(64), nullable=False)
    payload     = db.Column(db.JSON, nullable=True)
    status      = db.Column(
        Enum(UploadStatus, name="upload_status"),
        default=UploadStatus.RECEIVED,
        nullable=False,
    )
    uploaded_by = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)
    created_at  = db.Column(db.DateTime, server_default=func.now(), nullable=False)
    updated_at  = db.Column(
        db.DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    @declared_attr
    def user(cls):
        return relationship("Usuario")

class GtaUploadTmp(_BaseTmp, db.Model):
    __tablename__ = "gta_upload_tmp"

    # Coleção específica no Registro
    registro = relationship("Registro", back_populates="gta_uploads")

    __table_args__ = (
        UniqueConstraint("registro_id", name="uq_gta_upload_registro"),
    )

class DifTmp(_BaseTmp, db.Model):
    __tablename__ = "dif_tmp"

    registro = relationship("Registro", back_populates="dif_uploads")

    __table_args__ = (
        UniqueConstraint("registro_id", name="uq_dif_registro"),
    )

class SifTmp(_BaseTmp, db.Model):
    __tablename__ = "sif_tmp"

    registro = relationship("Registro", back_populates="sif_uploads")

    __table_args__ = (
        UniqueConstraint("registro_id", name="uq_sif_registro"),
    )

class VendasTmp(_BaseTmp, db.Model):
    """Planilha de vendas (comercializacao) validada, aguardando confirmacao."""
    __tablename__ = "vendas_tmp"

    registro = relationship("Registro", back_populates="vendas_uploads")

    __table_args__ = (
        UniqueConstraint("registro_id", name="uq_vendas_registro"),
    )

class ExecJob(db.Model):
    __tablename__ = "exec_job"
    id = db.Column(db.Integer, primary_key=True)
    registro_id = db.Column(db.Integer, db.ForeignKey("registro.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False, index=True)

    # Snapshot da escolha e do plano de execução do preview:
    gta_source = db.Column(db.String(16), nullable=False)  # "upload" | "manual"
    commands = db.Column(JSON, nullable=False)             # lista de strings (JS)
    meta = db.Column(JSON, nullable=True)                  # extras: counts, totais, etc.

    # Execução
    status = db.Column(db.String(16), nullable=False, default="ESPERA")  # ESPERA|EXECUTANDO|SUCESSO|FALHOU|PARTIAL
    progress = db.Column(db.Integer, nullable=False, default=0)          # 0..len(commands)
    errors = db.Column(JSON, nullable=True)                              # lista de erros pontuais
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, server_default=func.now(), onupdate=func.now())

    registro = relationship("Registro", back_populates="exec_jobs")

class WorkerSession(db.Model):
    __tablename__ = "workers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)  # e.g., "worker-0"
    status = db.Column(db.String(20), default="EXECUTANDO")  # EXECUTANDO, STOPPING, PARADO
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    host = db.Column(db.String(100), default=socket.gethostname)
    pid = db.Column(db.Integer, default=os.getpid)
    profile = db.Column(db.String(20))  # e.g., "prod", "test", "dev"
    threads = db.Column(db.Integer, default=1)  # Sempre 1 por worker (para compatibilidade)


# =========================================================
# CONSTANTES (tabelas administráveis) — substituem os
# dicionários hardcoded de descrições/partes/destinos.
# =========================================================

class _ConstMixin:
    """Campos comuns às tabelas de constantes."""
    id = db.Column(db.Integer, primary_key=True)
    ativo = db.Column(db.Boolean, nullable=False, server_default="1")
    obs = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)


class Diagnostico(_ConstMixin, db.Model):
    """
    Diagnóstico como ele existe no sistema do MAPA.
    'descricao_mapa' é o texto EXATO usado na busca do portal.
    """
    __tablename__ = "diagnostico"

    descricao_mapa = db.Column(db.String(180), nullable=False, unique=True)

    aliases = relationship(
        "DiagnosticoAlias",
        back_populates="diagnostico",
        cascade="all,delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Diagnostico id={self.id} {self.descricao_mapa!r}>"


class DiagnosticoAlias(db.Model):
    """
    Como o diagnóstico aparece nas planilhas (DIF/SIF/Condena).
    'alias_norm' guarda normalize_str(alias) e carrega a unicidade,
    impedindo cadastrar duas grafias que resolvam para a mesma chave.
    """
    __tablename__ = "diagnostico_alias"

    id = db.Column(db.Integer, primary_key=True)
    diagnostico_id = db.Column(
        db.Integer,
        db.ForeignKey("diagnostico.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = db.Column(db.String(180), nullable=False)
    alias_norm = db.Column(db.String(180), nullable=False, unique=True, index=True)

    diagnostico = relationship("Diagnostico", back_populates="aliases")

    def __repr__(self):
        return f"<DiagnosticoAlias {self.alias!r} -> {self.diagnostico_id}>"


class ParteAfetada(_ConstMixin, db.Model):
    """Parte afetada + o ID numérico esperado pelo portal (incluirParte)."""
    __tablename__ = "parte_afetada"

    nome = db.Column(db.String(120), nullable=False, unique=True)
    id_mapa = db.Column(db.Integer, nullable=False)

    aliases = relationship(
        "ParteAfetadaAlias",
        back_populates="parte",
        cascade="all,delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<ParteAfetada id={self.id} {self.nome!r} id_mapa={self.id_mapa}>"


class ParteAfetadaAlias(db.Model):
    __tablename__ = "parte_afetada_alias"

    id = db.Column(db.Integer, primary_key=True)
    parte_id = db.Column(
        db.Integer,
        db.ForeignKey("parte_afetada.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = db.Column(db.String(120), nullable=False)
    alias_norm = db.Column(db.String(120), nullable=False, unique=True, index=True)

    parte = relationship("ParteAfetada", back_populates="aliases")


class Destino(_ConstMixin, db.Model):
    """Destino + o ID numérico esperado pelo portal (incluirParte)."""
    __tablename__ = "destino"

    nome = db.Column(db.String(120), nullable=False, unique=True)
    id_mapa = db.Column(db.Integer, nullable=False)

    aliases = relationship(
        "DestinoAlias",
        back_populates="destino",
        cascade="all,delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Destino id={self.id} {self.nome!r} id_mapa={self.id_mapa}>"


class DestinoAlias(db.Model):
    __tablename__ = "destino_alias"

    id = db.Column(db.Integer, primary_key=True)
    destino_id = db.Column(
        db.Integer,
        db.ForeignKey("destino.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = db.Column(db.String(120), nullable=False)
    alias_norm = db.Column(db.String(120), nullable=False, unique=True, index=True)

    destino = relationship("Destino", back_populates="aliases")


class CondenaParte(_ConstMixin, db.Model):
    """
    Mapeamento de partes da planilha de Condenas para os índices
    de coluna usados na montagem da stack (antigo partes_condenas).
    """
    __tablename__ = "condena_parte"

    nome = db.Column(db.String(120), nullable=False, unique=True)
    slots = db.Column(JSON, nullable=False)  # ex.: [1, 2, 3]

    def __repr__(self):
        return f"<CondenaParte id={self.id} {self.nome!r} slots={self.slots}>"



# =========================================================
# COMERCIALIZACAO — regras "De -> Para" administraveis
# =========================================================

class ProdutoVenda(_ConstMixin, db.Model):
    """
    Produto como aparece na planilha de vendas (De) e o que o portal precisa (Para):
      - 'nome'           : grafia oficial na planilha (ex.: 'BACON RESFRIADO FATIADO')
      - 'descricao_busca': texto digitado no campo de busca do produto padronizado
      - 'id_mapa'        : id do produto no portal (casa com data-rk 'id=NNN' da tabela)
    Produtos diferentes PODEM apontar para o mesmo id_mapa (o portal aceita).
    """
    __tablename__ = "produto_venda"

    nome = db.Column(db.String(160), nullable=False, unique=True)
    descricao_busca = db.Column(db.String(180), nullable=False)
    id_mapa = db.Column(db.Integer, nullable=False)

    aliases = relationship(
        "ProdutoVendaAlias",
        back_populates="produto",
        cascade="all,delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<ProdutoVenda id={self.id} {self.nome!r} -> {self.id_mapa}>"


class ProdutoVendaAlias(db.Model):
    __tablename__ = "produto_venda_alias"

    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produto_venda.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = db.Column(db.String(160), nullable=False)
    alias_norm = db.Column(db.String(160), nullable=False, unique=True, index=True)

    produto = relationship("ProdutoVenda", back_populates="aliases")


class EstadoVenda(_ConstMixin, db.Model):
    """UF da planilha (De) e indice da opcao no select de UF do portal (Para)."""
    __tablename__ = "estado_venda"

    nome = db.Column(db.String(60), nullable=False, unique=True)   # sigla, ex.: 'RS'
    id_mapa = db.Column(db.Integer, nullable=False)                # indice no select (1..27)

    aliases = relationship(
        "EstadoVendaAlias",
        back_populates="estado",
        cascade="all,delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<EstadoVenda id={self.id} {self.nome!r} idx={self.id_mapa}>"


class EstadoVendaAlias(db.Model):
    __tablename__ = "estado_venda_alias"

    id = db.Column(db.Integer, primary_key=True)
    estado_id = db.Column(
        db.Integer,
        db.ForeignKey("estado_venda.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = db.Column(db.String(60), nullable=False)
    alias_norm = db.Column(db.String(60), nullable=False, unique=True, index=True)

    estado = relationship("EstadoVenda", back_populates="aliases")
