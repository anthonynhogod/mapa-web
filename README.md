# MAPA Automation

Automação de lançamentos no PGA-SIGSIF (MAPA), com dois módulos no mesmo sistema:

| Módulo | Entrada | Lançamento |
|---|---|---|
| **Abate** | GTA + DIF + SIF (upload ou manual) | um dia de abate por registro |
| **Comercialização** | planilha de vendas (`.xlsx`) | um período (mês) por registro, **por estado (UF)** |

Fluxo comum: upload → validação → preview (pendências de De→Para bloqueiam) → `ExecJob` → worker envia ao MAPA.

**Execução (`EXEC_BACKEND`)**
- `browser` (**padrão por enquanto**): Selenium + scripts JS no portal.
- `api`: envio pelo **webservice REST do PGA-SIGSIF** (manual v1.3), sem navegador. Implementado e testado só contra um servidor simulado; **desligado** até o MAPA liberar o acesso do usuário/IP. O ambiente padrão é **homologação** (`MAPA_API_AMBIENTE=homologacao`); produção só com `producao` explícito.

### Webservice (modo `api`, desligado por padrão: `EXEC_BACKEND=api` para ligar)

1. **Credenciais MAPA** (Configurações): além de usuário/senha/SIF, informe CPF/CNPJ, âmbito (SIF/ER), UF e IBGE do estabelecimento. Botão **Testar conexão** (`GET /especies`).
2. **Admin › API do MAPA**: mostra o ambiente, baixa os catálogos (`/especies`, `/diagnosticos`, `/partes-afetadas`, `/destino-condenacoes`, `/paises`, `/produtos`) e **sincroniza** os ids da API com o De→Para (só preenche vazios e só quando o nome casa de forma inequívoca; nunca sobrescreve).
   - Ids da API ficam em: Diagnósticos/Partes/Destinos (`ID na API`), Produtos (vendas) (`cod_produto na API`) e Espécies. Sem id = **pendência** que bloqueia o preview.
   - Alternativa por linha de comando: `python tools/explorar_api.py` (somente leitura).
3. **Preview** mostra o JSON exato que será enviado (aba *Comandos*). Abate: lista de linhas planas (GTA × lote × diagnóstico × parte × destino). Comercialização: `{data_inicio, data_fim, estabelecimento, transacoes[{tipo, nacional, tipo_operador, cod_uf, produtos[{cod_produto, quantidade}]}]}`.
4. O worker faz `POST` e guarda o `id` devolvido em `registro.api_id`; reenviar o mesmo registro faz `PUT` com esse id. **POST/PUT nunca são repetidos automaticamente**; um timeout em POST marca o job como *resultado desconhecido* (confira no portal antes de reenviar).

**Pontos a confirmar em homologação** (o manual não os define): formato do corpo do `POST /abate` (assumi lista de linhas planas); tipo do produto (`COMPRA/PRODUCAO/PROPRIA/DEVOLUCAO`, hoje omitido) e campo `estoque`; série da GTA com 1 letra (o sistema só avisa); formato de data; se enviar outro *tipo de lançamento* do mesmo período (venda × recebimento) soma ou substitui no mapa existente.

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

- **Tipos de lançamento** (Admin › Constantes › *Tipos de lançamento*): venda, recebimento e expedição dividem o **mesmo registro do período** no portal; muda a combinação de opções do formulário "Incluir estado" (índices dos selects) e o rótulo do tipo na tabela de transações. Só **venda** vem cadastrado (índices do fluxo legado); os demais são cadastrados no admin. Cada tipo precisa de um parser de planilha em `app/blueprints/comercializacao/parsers.py` (hoje só `venda`; os outros aparecem no upload como "layout ainda não suportado").
- **Já existe lançamento do mesmo tipo no período** → o job **aborta** com `[REGISTRO_COM_DADOS]`, sem alterar nada (lançamentos de outros tipos não contam). Se o portal não mostrar o tipo na tabela, qualquer estado conta. Para reprocessar: Admin › Threads › **Retry (limpando portal)**, que remove só as transações do mesmo tipo (ou todas, se o tipo não for distinguível na tabela).
- **Produtos diferentes que apontam para o mesmo item do portal** (ex.: LINGUIÇA TOSCANA e FRESCAL congeladas → 18152) são **lançados separadamente**, sem somar.
- Linha repetida (mesmo produto e UF) na planilha é somada com aviso; quantidades são arredondadas a 2 casas; quantidade que arredonda para 0 é ignorada com aviso.
- Estabelecimento: vem do **nº SIF das Credenciais MAPA** do usuário (antes estava fixo em `167`).

Tipos de lançamento e a API: `venda` = `tipo VENDA` + `nacional true` + `tipo_operador UF`. Os dois recebimentos já estão cadastrados **inativos** (`recebimento_autorizado` = COMPRA + `RECEBIMENTO_AUTORIZADO`; `recebimento_poa` = COMPRA + `ESTABELECIMENTO_POA`) até existir o layout de planilha de cada um.

### De → Para (Admin › Constantes)

- **Produtos (vendas)**: nome na planilha → descrição usada na busca do portal + ID do produto (+ apelidos de grafia).
- **Estados (vendas)**: UF → índice da opção no select de UF do portal (+ apelidos).

Os dados iniciais vêm do `constantes.py` legado (migration `c0a1e7b5d3f2` / `flask init-db`). A comparação ignora acento/caixa/pontuação; sem vínculo = pendência (sem "chute").

## Scripts JS — modo `browser` (`app/runner/scripts/`)

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
