"""Cliente do webservice REST do PGA-SIGSIF (Mapas Estatisticos).

Manual de utilizacao dos servicos v1.3 (08/02/2021):
  - raiz: https://homolog.agricultura.gov.br/pga_sigsif/servicos  (homologacao)
          https://sistemas.agricultura.gov.br/pga_sigsif/servicos (producao)
  - autenticacao: "Authorization: Basic base64(usuario:senha)" com a senha em md5
  - envio: POST cria, PUT atualiza (precisa do `id` devolvido no POST); corpo JSON
  - respostas: 200 ok | 400 erro na estrutura | 403 acesso negado | 500 erro ao processar

Regras deste cliente:
  - ambiente padrao = HOMOLOGACAO (producao so quando configurado explicitamente);
  - GET repete em falha de rede/5xx; POST/PUT NUNCA repetem sozinhos (nao sao idempotentes e
    um timeout nao diz se o servidor gravou);
  - a senha nunca vai para log nem para mensagens de erro.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

import requests

log = logging.getLogger(__name__)

RAIZ = {
    "homologacao": "https://homolog.agricultura.gov.br/pga_sigsif/servicos",
    "producao": "https://sistemas.agricultura.gov.br/pga_sigsif/servicos",
}
CATALOGOS = {
    "especies": "especies",
    "diagnosticos": "diagnosticos",
    "destinos": "destino-condenacoes",
    "partes": "partes-afetadas",
    "paises": "paises",
    "produtos": "produtos",
}


class ApiError(Exception):
    """Falha de comunicacao ou resposta de erro do webservice."""

    def __init__(self, mensagem: str, *, status: int = 0, corpo: Any = None, metodo: str = "", caminho: str = ""):
        super().__init__(mensagem)
        self.status = status
        self.corpo = corpo
        self.metodo = metodo
        self.caminho = caminho

    @property
    def acesso_negado(self) -> bool:
        return self.status == 403

    @property
    def transitorio(self) -> bool:
        """Rede/timeout/5xx: vale tentar de novo uma consulta (GET)."""
        return self.status == 0 or self.status >= 500


def basic_auth(usuario: str, senha: str, md5: bool = True) -> str:
    s = hashlib.md5(senha.encode("utf-8")).hexdigest() if md5 else senha
    return "Basic " + base64.b64encode(f"{usuario}:{s}".encode("utf-8")).decode("ascii")


def resolver_raiz(ambiente: str, url_override: Optional[str] = None) -> str:
    if url_override:
        return url_override.rstrip("/")
    amb = (ambiente or "homologacao").strip().lower()
    if amb in ("prod", "producao", "produção", "production"):
        return RAIZ["producao"]
    return RAIZ["homologacao"]


def _corpo(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000]


def resumir_corpo(corpo: Any, limite: int = 1500) -> str:
    """Texto curto e legivel do corpo de erro (para job.errors / tela)."""
    if isinstance(corpo, (dict, list)):
        txt = json.dumps(corpo, ensure_ascii=False)
    else:
        txt = str(corpo or "").strip()
    return txt if len(txt) <= limite else txt[:limite] + "…"


class MapaApiClient:
    def __init__(self, usuario: str, senha: str, *, ambiente: str = "homologacao", url: Optional[str] = None,
                 md5: bool = True, timeout: float = 60.0, tentativas_get: int = 3,
                 session: Optional[requests.Session] = None, sleep: Callable[[float], None] = time.sleep):
        self.raiz = resolver_raiz(ambiente, url)
        self.ambiente = "producao" if self.raiz == RAIZ["producao"] else (
            "homologacao" if self.raiz == RAIZ["homologacao"] else "personalizado")
        self._auth = basic_auth(usuario, senha, md5)
        self.timeout = timeout
        self.tentativas_get = max(1, int(tentativas_get))
        self.session = session or requests.Session()
        self._sleep = sleep
        self._cache: Dict[str, Any] = {}

    # -- baixo nivel ------------------------------------------------------------------
    def _req(self, metodo: str, caminho: str, *, json_body: Any = None) -> Any:
        url = f"{self.raiz}/{caminho.lstrip('/')}"
        headers = {"Authorization": self._auth, "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = self.session.request(metodo, url, headers=headers, timeout=self.timeout,
                                        data=None if json_body is None else json.dumps(json_body, ensure_ascii=False).encode("utf-8"))
        except requests.RequestException as e:
            raise ApiError(f"Falha de rede em {metodo} {caminho}: {type(e).__name__}", metodo=metodo, caminho=caminho) from e

        corpo = _corpo(resp)
        if resp.status_code == 200 or 200 <= resp.status_code < 300:
            return corpo
        msgs = {
            400: "Erro na estrutura dos dados (400)",
            403: "Acesso negado (403): confira usuario, senha/md5 e o perfil do servico",
            500: "Erro ao processar os dados no MAPA (500)",
        }
        raise ApiError(f"{msgs.get(resp.status_code, f'HTTP {resp.status_code}')}: {resumir_corpo(corpo)}",
                       status=resp.status_code, corpo=corpo, metodo=metodo, caminho=caminho)

    def get(self, caminho: str) -> Any:
        ultimo = None
        for i in range(1, self.tentativas_get + 1):
            try:
                return self._req("GET", caminho)
            except ApiError as e:
                ultimo = e
                if not e.transitorio or i == self.tentativas_get:
                    raise
                log.warning("GET %s falhou (%s); tentativa %s/%s", caminho, e, i, self.tentativas_get)
                self._sleep(1.5 * i)
        raise ultimo  # pragma: no cover

    def post(self, caminho: str, corpo: Any) -> Any:
        return self._req("POST", caminho, json_body=corpo)

    def put(self, caminho: str, corpo: Any) -> Any:
        return self._req("PUT", caminho, json_body=corpo)

    # -- alto nivel -------------------------------------------------------------------
    def catalogo(self, nome: str, *, usar_cache: bool = True) -> Any:
        if nome not in CATALOGOS:
            raise KeyError(nome)
        if usar_cache and nome in self._cache:
            return self._cache[nome]
        dados = self.get(CATALOGOS[nome])
        self._cache[nome] = dados
        return dados

    def validar_credenciais(self) -> None:
        """Levanta ApiError se o usuario nao autentica (GET /especies e leve e restrito)."""
        self.get(CATALOGOS["especies"])

    def enviar(self, servico: str, corpo: Any, *, api_id: Optional[int] = None) -> Any:
        """POST (cria) ou, com `api_id`, PUT (atualiza). `servico`: abate | comercializacao | producao."""
        if api_id:
            return self.put(servico, _com_id(corpo, api_id))
        return self.post(servico, corpo)


def _com_id(corpo: Any, api_id: int) -> Any:
    if isinstance(corpo, dict):
        return {"id": int(api_id), **corpo}
    if isinstance(corpo, list):
        return [{"id": int(api_id), **linha} if isinstance(linha, dict) else linha for linha in corpo]
    return corpo


def extrair_id(resposta: Any) -> Optional[int]:
    """Procura o id do mapa na resposta (objeto ou 'lista de registros')."""
    def pick(o):
        if isinstance(o, dict):
            for k in ("id", "Id", "ID"):
                if isinstance(o.get(k), (int, str)) and str(o[k]).isdigit():
                    return int(o[k])
        return None
    if isinstance(resposta, list):
        for item in resposta:
            v = pick(item)
            if v:
                return v
        return None
    return pick(resposta)
