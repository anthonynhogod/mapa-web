# app/logic/constantes.py
"""
Resolução de constantes (diagnósticos, partes afetadas, destinos) a partir
do banco, substituindo os antigos dicionários hardcoded.

Regra central: NÃO EXISTE FALLBACK. Um termo que não tem vínculo cadastrado
nunca é "adivinhado" nem repassado cru para o portal — ele vira uma pendência
que bloqueia a validação do registro.
"""
from threading import RLock

from app.utils.format import normalize_str


# ---------------------------------------------------------------------------
# Pendências
# ---------------------------------------------------------------------------

class ConstanteNaoMapeada(Exception):
    """Levantada quando um termo não possui vínculo cadastrado."""

    def __init__(self, tipo: str, valor: str):
        self.tipo = tipo            # 'diagnostico' | 'parte' | 'destino' | 'produto_venda' | 'estado_venda' | 'produto_producao'
        self.valor = (valor or "").strip()
        super().__init__(f"{tipo} sem vínculo cadastrado: {self.valor!r}")


ROTULOS_PENDENCIA = {
    "diagnostico": "Diagnóstico", "parte": "Parte afetada", "destino": "Destino",
    "produto_venda": "Produto (vendas)", "estado_venda": "Estado (vendas)",
    "produto_producao": "Produto (produção)",
    "diagnostico_api": "Diagnóstico sem ID da API", "parte_api": "Parte afetada sem ID da API",
    "destino_api": "Destino sem ID da API", "produto_api": "Produto sem cod_produto da API",
    "especie_api": "Espécie sem ID da API", "credencial": "Credenciais MAPA incompletas",
}

# Para cada tipo: o que aconteceu e onde resolver (mostrado ao operador no preview e no veto)
COMO_RESOLVER = {
    "diagnostico": "Não há diagnóstico do MAPA vinculado a este termo. Cadastre-o (ou inclua como apelido de um existente) em Admin › Constantes › Diagnósticos, ou corrija a planilha.",
    "parte": "Parte afetada sem vínculo. Cadastre em Admin › Constantes › Partes afetadas (ou como apelido).",
    "destino": "Destino sem vínculo. Cadastre em Admin › Constantes › Destinos (ou como apelido).",
    "produto_venda": "Produto sem vínculo. Cadastre em Admin › Constantes › Produtos (vendas) com a descrição de busca e o ID do portal (ou como apelido de um existente).",
    "estado_venda": "UF sem vínculo. Cadastre em Admin › Constantes › Estados (vendas) com o índice da UF no portal.",
    "produto_producao": "Produto da produção sem vínculo. Cadastre em Admin › Constantes › Produtos (produção) com a descrição de busca e o ID do portal (ou como apelido de um existente).",
    "diagnostico_api": "O diagnóstico está cadastrado, mas sem ID da API. Informe em Admin › Constantes › Diagnósticos ou use Admin › API do MAPA › Sincronizar.",
    "parte_api": "A parte está cadastrada, mas sem ID da API. Informe em Admin › Constantes › Partes afetadas ou sincronize em Admin › API do MAPA.",
    "destino_api": "O destino está cadastrado, mas sem ID da API. Informe em Admin › Constantes › Destinos ou sincronize em Admin › API do MAPA.",
    "produto_api": "O produto está cadastrado, mas sem cod_produto da API. Informe em Admin › Constantes › Produtos (vendas) ou sincronize em Admin › API do MAPA.",
    "especie_api": "A espécie não tem ID da API. Informe em Admin › API do MAPA › Espécies.",
    "credencial": "Complete o cadastro em Configurações › Credenciais MAPA.",
}


class ColetorPendencias:
    """
    Acumula tudo que não resolveu, em vez de estourar no primeiro erro.
    Assim o operador vê a lista inteira de uma vez e não corrige um por vez.
    """

    def __init__(self):
        self._itens = {}  # (tipo, valor_norm) -> {tipo, valor, ocorrencias}

    def registrar(self, tipo: str, valor: str) -> None:
        valor = (valor or "").strip()
        chave = (tipo, normalize_str(valor))
        item = self._itens.get(chave)
        if item:
            item["ocorrencias"] += 1
        else:
            self._itens[chave] = {"tipo": tipo, "valor": valor, "ocorrencias": 1}

    @property
    def vazio(self) -> bool:
        return not self._itens

    def listar(self):
        """Pendências ordenadas por tipo e depois por valor."""
        ordem = {"diagnostico": 0, "parte": 1, "destino": 2,
                 "produto_venda": 3, "estado_venda": 4, "produto_producao": 3,
                 "diagnostico_api": 5, "parte_api": 6, "destino_api": 7, "produto_api": 8,
                 "especie_api": 9, "credencial": 10}
        itens = sorted(
            self._itens.values(),
            key=lambda i: (ordem.get(i["tipo"], 9), i["valor"].lower()),
        )
        return [{**i, "rotulo": ROTULOS_PENDENCIA.get(i["tipo"], i["tipo"]),
                 "como": COMO_RESOLVER.get(i["tipo"], "")} for i in itens]

    def __len__(self):
        return len(self._itens)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_lock = RLock()
_cache = {"diagnostico": None, "parte": None, "destino": None, "condena": None,
          "produto_venda": None, "estado_venda": None, "produto_producao": None,
          "diagnostico_api": None, "parte_api": None, "destino_api": None, "especie_api": None}


def invalidar_cache(tipo: str = None) -> None:
    """Chamado após qualquer gravação no CRUD de constantes."""
    with _lock:
        if tipo:
            _cache[tipo] = None
            if f"{tipo}_api" in _cache:
                _cache[f"{tipo}_api"] = None
        else:
            for k in _cache:
                _cache[k] = None


def _mapa_diagnosticos():
    with _lock:
        if _cache["diagnostico"] is None:
            from app.models import Diagnostico, DiagnosticoAlias
            from app.extensions import db

            rows = (
                db.session.query(DiagnosticoAlias.alias_norm, Diagnostico.descricao_mapa)
                .join(Diagnostico, DiagnosticoAlias.diagnostico_id == Diagnostico.id)
                .filter(Diagnostico.ativo.is_(True))
                .all()
            )
            _cache["diagnostico"] = {norm: descr for norm, descr in rows}
        return _cache["diagnostico"]


def _mapa_id(tipo: str):
    with _lock:
        if _cache[tipo] is None:
            from app.extensions import db
            if tipo == "parte":
                from app.models import ParteAfetada as Ent, ParteAfetadaAlias as Ali
                fk = Ali.parte_id
            else:
                from app.models import Destino as Ent, DestinoAlias as Ali
                fk = Ali.destino_id

            rows = (
                db.session.query(Ali.alias_norm, Ent.id_mapa)
                .join(Ent, fk == Ent.id)
                .filter(Ent.ativo.is_(True))
                .all()
            )
            _cache[tipo] = {norm: idm for norm, idm in rows}
        return _cache[tipo]


# ---------------------------------------------------------------------------
# API de resolução
# ---------------------------------------------------------------------------

def resolve_diagnostico(nome: str, coletor: ColetorPendencias = None):
    """
    Retorna a descrição EXATA do MAPA. Sem vínculo -> pendência.
    Com coletor, devolve None e registra; sem coletor, levanta a exceção.
    """
    chave = normalize_str(nome or "")
    valor = _mapa_diagnosticos().get(chave)
    if valor is not None:
        return valor
    if coletor is not None:
        coletor.registrar("diagnostico", nome)
        return None
    raise ConstanteNaoMapeada("diagnostico", nome)


def resolve_parte(nome: str, coletor: ColetorPendencias = None):
    """Retorna o id_mapa da parte afetada. Sem vínculo -> pendência."""
    chave = normalize_str(nome or "")
    valor = _mapa_id("parte").get(chave)
    if valor is not None:
        return valor
    if coletor is not None:
        coletor.registrar("parte", nome)
        return None
    raise ConstanteNaoMapeada("parte", nome)


def resolve_destino(nome: str, coletor: ColetorPendencias = None):
    """Retorna o id_mapa do destino. Sem vínculo -> pendência."""
    chave = normalize_str(nome or "")
    valor = _mapa_id("destino").get(chave)
    if valor is not None:
        return valor
    if coletor is not None:
        coletor.registrar("destino", nome)
        return None
    raise ConstanteNaoMapeada("destino", nome)


def canonicalizar_diagnostico(nome: str) -> str:
    """
    Chave de AGRUPAMENTO (usada no merge DIF+SIF).

    Se o termo tem vínculo, agrupa pela descrição oficial — assim variantes
    de grafia somam no mesmo diagnóstico. Se não tem, devolve a normalização
    do texto original: o termo sobrevive intacto até a validação, que é onde
    ele deve ser barrado (e não silenciosamente fundido com outro).
    """
    chave = normalize_str(nome or "")
    oficial = _mapa_diagnosticos().get(chave)
    return normalize_str(oficial) if oficial else chave


def mapa_condena_partes():
    """Equivalente ao antigo partes_condenas: {nome_norm: [slots]}."""
    with _lock:
        if _cache["condena"] is None:
            from app.models import CondenaParte
            from app.extensions import db

            rows = db.session.query(CondenaParte.nome, CondenaParte.slots).filter(
                CondenaParte.ativo.is_(True)
            ).all()
            _cache["condena"] = {normalize_str(n): s for n, s in rows}
        return _cache["condena"]



# ---------------------------------------------------------------------------
# Comercializacao (De -> Para de produto e estado)
# ---------------------------------------------------------------------------

def _mapa_produtos_venda():
    """{alias_norm: {"descricao": str, "id": int, "nome": str}} dos produtos ativos."""
    with _lock:
        if _cache["produto_venda"] is None:
            from app.models import ProdutoVenda, ProdutoVendaAlias
            from app.extensions import db

            rows = (
                db.session.query(
                    ProdutoVendaAlias.alias_norm,
                    ProdutoVenda.descricao_busca, ProdutoVenda.id_mapa, ProdutoVenda.nome,
                    ProdutoVenda.cod_api,
                )
                .join(ProdutoVenda, ProdutoVendaAlias.produto_id == ProdutoVenda.id)
                .filter(ProdutoVenda.ativo.is_(True))
                .all()
            )
            _cache["produto_venda"] = {
                norm: {"descricao": descr, "id": int(idm) if idm is not None else None,
                       "nome": nome, "cod_api": int(cod) if cod is not None else None}
                for norm, descr, idm, nome, cod in rows
            }
        return _cache["produto_venda"]


def _mapa_estados_venda():
    """{alias_norm: {"uf": str, "index": int}} dos estados ativos."""
    with _lock:
        if _cache["estado_venda"] is None:
            from app.models import EstadoVenda, EstadoVendaAlias
            from app.extensions import db

            rows = (
                db.session.query(EstadoVendaAlias.alias_norm, EstadoVenda.nome, EstadoVenda.id_mapa)
                .join(EstadoVenda, EstadoVendaAlias.estado_id == EstadoVenda.id)
                .filter(EstadoVenda.ativo.is_(True))
                .all()
            )
            _cache["estado_venda"] = {norm: {"uf": nome, "index": int(idm)} for norm, nome, idm in rows}
        return _cache["estado_venda"]


def resolve_produto_venda(nome: str, coletor: ColetorPendencias = None):
    """Produto da planilha -> {"descricao", "id", "nome"} do portal. Sem vinculo -> pendencia."""
    valor = _mapa_produtos_venda().get(normalize_str(nome or ""))
    if valor is not None:
        return valor
    if coletor is not None:
        coletor.registrar("produto_venda", nome)
        return None
    raise ConstanteNaoMapeada("produto_venda", nome)


def _mapa_produtos_producao():
    """{alias_norm: {"descricao": str, "id": int|None, "nome": str}} dos produtos de producao ativos."""
    with _lock:
        if _cache["produto_producao"] is None:
            from app.models import ProdutoProducao, ProdutoProducaoAlias
            from app.extensions import db

            rows = (
                db.session.query(
                    ProdutoProducaoAlias.alias_norm,
                    ProdutoProducao.descricao_busca, ProdutoProducao.id_mapa, ProdutoProducao.nome,
                    ProdutoProducao.ingrediente,
                )
                .join(ProdutoProducao, ProdutoProducaoAlias.produto_id == ProdutoProducao.id)
                .filter(ProdutoProducao.ativo.is_(True))
                .all()
            )
            _cache["produto_producao"] = {
                norm: {"descricao": descr, "id": int(idm) if idm is not None else None, "nome": nome,
                       "ingrediente": (ing or "").strip() or None}
                for norm, descr, idm, nome, ing in rows
            }
        return _cache["produto_producao"]


def resolve_produto_producao(nome: str, coletor: ColetorPendencias = None):
    """Produto da planilha de producao -> {"descricao", "id", "nome", "ingrediente"} do portal. Sem vinculo -> pendencia."""
    valor = _mapa_produtos_producao().get(normalize_str(nome or ""))
    if valor is not None:
        return valor
    if coletor is not None:
        coletor.registrar("produto_producao", nome)
        return None
    raise ConstanteNaoMapeada("produto_producao", nome)


def resolve_estado_venda(uf: str, coletor: ColetorPendencias = None):
    """UF da planilha -> {"uf", "index"} (indice da opcao no select do portal)."""
    valor = _mapa_estados_venda().get(normalize_str(uf or ""))
    if valor is not None:
        return valor
    if coletor is not None:
        coletor.registrar("estado_venda", uf)
        return None
    raise ConstanteNaoMapeada("estado_venda", uf)



# ---------------------------------------------------------------------------
# Webservice: ids da API (GET /diagnosticos, /partes-afetadas, /destino-condenacoes, /especies)
# ---------------------------------------------------------------------------

def _mapa_id_api(tipo: str):
    """{alias_norm: id_api|None} das constantes ativas. None = existe o vinculo, falta o id da API."""
    with _lock:
        chave = f"{tipo}_api"
        if _cache[chave] is None:
            from app.extensions import db
            from app import models as m
            if tipo == "diagnostico":
                q = db.session.query(m.DiagnosticoAlias.alias_norm, m.Diagnostico.id_api).join(
                    m.Diagnostico, m.DiagnosticoAlias.diagnostico_id == m.Diagnostico.id
                ).filter(m.Diagnostico.ativo.is_(True))
            elif tipo == "parte":
                q = db.session.query(m.ParteAfetadaAlias.alias_norm, m.ParteAfetada.id_api).join(
                    m.ParteAfetada, m.ParteAfetadaAlias.parte_id == m.ParteAfetada.id
                ).filter(m.ParteAfetada.ativo.is_(True))
            elif tipo == "destino":
                q = db.session.query(m.DestinoAlias.alias_norm, m.Destino.id_api).join(
                    m.Destino, m.DestinoAlias.destino_id == m.Destino.id
                ).filter(m.Destino.ativo.is_(True))
            else:  # especie
                q = db.session.query(m.EspecieApi.nome, m.EspecieApi.id_api).filter(m.EspecieApi.ativo.is_(True))
                _cache[chave] = {normalize_str(n): (int(i) if i is not None else None) for n, i in q.all()}
                return _cache[chave]
            _cache[chave] = {n: (int(i) if i is not None else None) for n, i in q.all()}
        return _cache[chave]


def _resolve_api(tipo: str, nome: str, coletor: ColetorPendencias = None):
    """id da API, ou None com pendencia (sem coletor, levanta). Distingue 'sem vinculo' de 'sem id da API'."""
    mapa = _mapa_id_api(tipo)
    chave = normalize_str(nome or "")
    if chave in mapa:
        if mapa[chave] is not None:
            return mapa[chave]
        tipo_pend = f"{tipo}_api"
    else:
        tipo_pend = tipo if tipo != "especie" else "especie_api"
    if coletor is not None:
        coletor.registrar(tipo_pend, nome)
        return None
    raise ConstanteNaoMapeada(tipo_pend, nome)


def resolve_diagnostico_api(nome, coletor=None):
    return _resolve_api("diagnostico", nome, coletor)


def resolve_parte_api(nome, coletor=None):
    return _resolve_api("parte", nome, coletor)


def resolve_destino_api(nome, coletor=None):
    return _resolve_api("destino", nome, coletor)


def resolve_especie_api(nome, coletor=None):
    return _resolve_api("especie", nome, coletor)
