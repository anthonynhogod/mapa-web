#!/usr/bin/env python3
"""Explora o webservice REST do PGA-SIGSIF (Mapas Estatisticos) em HOMOLOGACAO.

Somente leitura (GET). Salva os catalogos em ./api_catalogos/ para alimentar o De->Para
(especies, diagnosticos, partes afetadas, destinos de condenacao, paises, produtos) e tenta
achar o Swagger (o manual v1.3 diz que os atributos foram "construidos no Swagger").

Uso:
    export MAPA_USER='seu-usuario'  MAPA_PASSWORD='sua-senha'
    python tools/explorar_api.py                 # homologacao (padrao)
    python tools/explorar_api.py --producao      # so leitura tambem, mas em producao
    python tools/explorar_api.py --senha-crua    # envia a senha sem md5

Autenticacao: "Authorization: Basic base64(usuario:senha)" com a senha em md5 (manual, 2.1).
Sem --senha-crua o script testa as duas variantes em /especies e usa a que responder 200.
Nada alem de GET e executado; a senha nao e gravada em disco.
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

RAIZ = {
    "homologacao": "https://homolog.agricultura.gov.br/pga_sigsif/servicos",
    "producao": "https://sistemas.agricultura.gov.br/pga_sigsif/servicos",
}
CATALOGOS = ["especies", "diagnosticos", "destino-condenacoes", "partes-afetadas", "paises", "produtos"]
SWAGGERS = ["swagger.json", "openapi.json", "v2/api-docs", "v3/api-docs", "swagger", "api-docs"]


def basic(usuario: str, senha: str, md5: bool) -> str:
    s = hashlib.md5(senha.encode("utf-8")).hexdigest() if md5 else senha
    return "Basic " + base64.b64encode(f"{usuario}:{s}".encode("utf-8")).decode("ascii")


def get(url: str, auth: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"Authorization": auth, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # rede/TLS
        return 0, str(e).encode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--producao", action="store_true", help="usa o ambiente de producao (padrao: homologacao)")
    ap.add_argument("--senha-crua", action="store_true", help="nao aplica md5 na senha")
    ap.add_argument("--saida", default="api_catalogos")
    a = ap.parse_args()

    usuario, senha = os.getenv("MAPA_USER"), os.getenv("MAPA_PASSWORD")
    if not usuario or not senha:
        print("Defina MAPA_USER e MAPA_PASSWORD no ambiente.", file=sys.stderr)
        return 2
    base = RAIZ["producao" if a.producao else "homologacao"]
    os.makedirs(a.saida, exist_ok=True)

    variantes = [False] if a.senha_crua else [True, False]
    auth = None
    for md5 in ([False] if a.senha_crua else variantes):
        cand = basic(usuario, senha, md5=md5)
        st, corpo = get(f"{base}/especies", cand)
        print(f"auth {'md5' if md5 else 'senha crua'}: HTTP {st}")
        if st == 200:
            auth = cand
            print(f"-> usando senha {'em md5' if md5 else 'crua'}")
            break
    if auth is None:
        print("Nenhuma variante autenticou (403 = acesso negado; 0 = rede/TLS). Verifique usuario, perfil e ambiente.")
        return 1

    for nome in CATALOGOS:
        st, corpo = get(f"{base}/{nome}", auth)
        caminho = os.path.join(a.saida, f"{nome}.json")
        try:
            dados = json.loads(corpo)
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
            n = len(dados) if isinstance(dados, list) else "objeto"
            amostra = dados[0] if isinstance(dados, list) and dados else dados
            print(f"{nome}: HTTP {st}, {n} registro(s); exemplo: {json.dumps(amostra, ensure_ascii=False)[:200]}")
        except ValueError:
            with open(caminho + ".raw", "wb") as f:
                f.write(corpo)
            print(f"{nome}: HTTP {st}, resposta nao-JSON salva em {caminho}.raw")

    for sw in SWAGGERS:
        st, corpo = get(f"{base}/{sw}", auth, timeout=30)
        if st == 200 and corpo:
            caminho = os.path.join(a.saida, "swagger_" + sw.replace("/", "_"))
            with open(caminho, "wb") as f:
                f.write(corpo)
            print(f"swagger encontrado: {base}/{sw} -> {caminho}")
            break
    else:
        print("swagger: nao encontrado nos caminhos comuns")
    print(f"\nPronto. Envie o conteudo de ./{a.saida}/ (sem credenciais) para eu montar o De->Para.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
