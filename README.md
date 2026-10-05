# MAPA Automation

Automação de lançamentos no PGA-SIGSIF (MAPA), com dois módulos no mesmo sistema:

| Módulo | Entrada | Lançamento |
|---|---|---|
| **Abate** | GTA + DIF + SIF (upload ou manual) | um dia de abate por registro |
| **Comercialização** | planilha de vendas (`.xlsx`) | um período (mês) por registro, **por estado (UF)** |

Fluxo comum: upload → validação → preview (pendências de De→Para bloqueiam) → `ExecJob` → worker (Selenium) executa os comandos JS no portal.

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .flaskenv.example .flaskenv        # preencha FERNET_KEY, JWT_SECRET etc. (não commitar)

flask init-db                          # banco NOVO: cria o schema, carrega as constantes e marca as migrations
# banco já existente (ex.: o db.sqlite antigo):  flask db upgrade

flask users-create --nome admin --senha '...' --role admin
flask run                              # app
flask worker run --threads 1 --profile dev   # executor (outro terminal)
```

O driver do Chrome é resolvido sozinho (Selenium Manager) quando `CHROMEDRIVER_PATH` está vazio ou aponta para um arquivo que não existe.

## Módulo Comercialização

1. **Comercialização › Nova planilha**: envie o `.xlsx`. O sistema acha o cabeçalho (`PRODUTO`, `ESTADO`, `QUANTIDADE`, `CÓDIGO` opcional) em qualquer linha e lê o período do título (`... Vendas - 01/03/2026 até 31/03/2026`). Se o título não tiver o período, informe as datas no formulário.
2. **Preview**: lançamentos agrupados por UF + os comandos gerados. Qualquer produto/UF sem vínculo vira **pendência** e nada é enviado ao portal.
3. **Confirmar & Executar** cria o job; o worker abre (ou cria) o registro do período no portal e lança estado por estado.

Regras de negócio adotadas:

- **Registro do período já tem estados no portal** → o job **aborta** com `[REGISTRO_COM_DADOS]`, sem alterar nada. Para reprocessar: Admin › Threads › **Retry (limpando portal)** (troca o 1º comando por `limparTransacoes()`).
- **Produtos diferentes que apontam para o mesmo item do portal** (ex.: LINGUIÇA TOSCANA e FRESCAL congeladas → 18152) são **lançados separadamente**, sem somar.
- Linha repetida (mesmo produto e UF) na planilha é somada com aviso; quantidades são arredondadas a 2 casas; quantidade que arredonda para 0 é ignorada com aviso.
- Estabelecimento: vem do **nº SIF das Credenciais MAPA** do usuário (antes estava fixo em `167`).

### De → Para (Admin › Constantes)

- **Produtos (vendas)**: nome na planilha → descrição usada na busca do portal + ID do produto (+ apelidos de grafia).
- **Estados (vendas)**: UF → índice da opção no select de UF do portal (+ apelidos).

Os dados iniciais vêm do `constantes.py` legado (migration `c0a1e7b5d3f2` / `flask init-db`). A comparação ignora acento/caixa/pontuação; sem vínculo = pendência (sem "chute").

## Scripts JS (`app/runner/scripts/`)

- `core.js` — helpers comuns. Resolve elementos pela parte **estável** do id (sufixo / dentro do diálogo visível); o id `j_idt…` antigo é só fallback. `pf()` com timeout, paginação de DataTable, leitura de erro de UI por severidade, fechamento de diálogos após falha.
- `scripts.js` — Abate (mesmas funções/retornos de antes, agora sobre o `core.js`).
- `comercializacao.js` — Comercialização: detecta modo Alterar/Incluir, acha a linha da UF pelo texto (índice é só dica), casa produto por id exato em todas as páginas, salva aguardando o servidor.

Fallbacks no `Navegador`: driver (caminho → env → embutido → Selenium Manager), login com 3 tentativas, URL tolerante a query/jsessionid, reinjeção de scripts quando a página os perde, retry só de erro transitório na comercialização (não repete comando não idempotente), literais JS escapados (`app/logic/js_literal.py`) também no abate.

## Testes

```bash
pip install pytest playwright   # Chromium do Playwright já deve estar instalado
pytest
```

Os testes de JS rodam `core.js`/`scripts.js`/`comercializacao.js` no Chromium contra um **portal simulado** (`tests/js/`), com ids legados e ids trocados. **Não substituem** uma execução no portal real.
