# MAPA Automation

Automação de lançamentos no PGA-SIGSIF (MAPA), com três módulos no mesmo sistema:

| Módulo | Entrada | Lançamento |
|---|---|---|
| **Abate** | GTA + DIF + SIF (upload ou manual) | um dia de abate por registro |
| **Comercialização** | planilha de vendas (`.xlsx`) | um período (mês) por registro, **por estado (UF)** |
| **Produção** | planilha de produção (`.xlsx`, colunas PRODUTO e TOTAL) | um mês (MM/AAAA) por registro, **por produto** — só pelo navegador |

Fluxo comum: upload → validação → preview (pendências de De→Para bloqueiam) → `ExecJob` → worker envia ao MAPA.

**Execução (`EXEC_BACKEND`)**
- `browser` (**padrão por enquanto**): Selenium + scripts JS no portal.
- `api`: envio pelo **webservice REST do PGA-SIGSIF** (manual v1.3), sem navegador. Implementado e testado só contra um servidor simulado; **desligado** até o MAPA liberar o acesso do usuário/IP. O ambiente padrão é **homologação** (`MAPA_API_AMBIENTE=homologacao`); produção só com `producao` explícito.

### Mapa de produção (só navegador)

1. **Admin › Constantes › Produção › Produtos (produção)**: o De→Para é *produto da planilha* → *descrição de busca* (texto digitado na pesquisa de produto padronizado) + *ID do portal* (o `id=NNN` do `data-rk` da linha encontrada). Sem vínculo = pendência que bloqueia o preview. Não há carga inicial: cadastre conforme as pendências aparecem. Cada produto da planilha é um **lançamento único**, mesmo que vários apontem para o mesmo ID do portal (ex.: “MIÚDOS SALGADOS” e “MIÚDOS SALGADOS (MÁSCARA)”): o Ministério exige lançamentos separados, então nada é somado por ID.
2. **Produção › Nova planilha**: usa só `PRODUTO` e `TOTAL`. O mês vem das **colunas de dia** com produção (o intervalo do título pode passar do mês); planilha com mais de um mês é recusada; a competência (`MM/AAAA`) pode ser informada à mão quando a planilha não tem colunas de dia.
3. O job abre a inclusão no portal, preenche estabelecimento (nº do SIF da credencial), período e “Produção = Sim”, lança cada produto e só no último comando confere o nº de linhas e clica em *Confirmar*. **Nada fica gravado antes disso**: um job que falha no meio pode ser reexecutado do zero. O único passo sem volta é o *Adicionar* de cada produto — erro depois dele vira `[ADD_INCERTO]` e nunca é repetido sozinho.
4. Ainda **não** há consulta/alteração de um mapa de produção já existente nem conferência com o relatório do MAPA. O sistema **não verifica** se o mês já existe no portal: espera-se que o portal recuse o *Confirmar* duplicado (o job falharia com a mensagem dele), mas isso **ainda não foi verificado** — confira no portal antes de reexecutar um job que falhou em `[EXC_FINALIZAR]`.
5. O JS foi escrito a partir do HTML salvo da tela de inclusão e testado contra um portal simulado (Chromium + `tests/js/mock_producao.html`); a primeira execução real deve ser feita com o perfil `dev` (navegador visível) e uma planilha de 1–2 produtos. Quantidades são arredondadas a 2 casas (como na comercialização).

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

## Chrome e chromedriver (modo `browser`)

O driver precisa ter a mesma versão principal do Chrome. Para não depender de ninguém lembrar de atualizar:

- Na **inicialização** (`flask run` e ao subir um worker) o sistema abre o Chrome de verdade (headless) com os drivers
  conhecidos (`CHROMEDRIVER_PATH`, `app/runner/drivers/`); se nenhum combina, pede ao **Selenium Manager**
  (embutido no pacote `selenium`) o driver certo, baixa e guarda em cache. Drivers soltos no `PATH` são ignorados primeiro
  (causa clássica de versão errada). Se o Chrome falhar ao abrir durante um lançamento, ele se atualiza e tenta uma vez.
- **Admin › Configurações › Threads** mostra o estado (versões, tentativas) e tem o botão *Verificar e atualizar driver agora*.
  Pela linha de comando: `flask driver-check`.
- Falha de driver/Chrome **não** é mais reportada como "credenciais inválidas": o job falha com a causa real e a
  credencial não é marcada como inválida.
- Rede instável (proxy que bloqueia "de vez em quando", F5 resolve)? O download **repete** (Selenium Manager 3×; fallback em
  Python 5× por arquivo), usa o **proxy configurado no Windows/variáveis HTTPS_PROXY** e, se tudo falhar, o relatório diz a causa
  real (proxy recusou, certificado HTTPS, timeout, HTTP 403...). O fallback em Python baixa o driver do Chrome for Testing para
  `app/runner/drivers/auto/<versão>/` e reaproveita nas próximas vezes. Alternativa manual: baixe o `chromedriver` da versão do seu
  Chrome em https://googlechromelabs.github.io/chrome-for-testing/ e coloque em `app/runner/drivers/`.
- Rede restrita? O download usa `googlechromelabs.github.io` e `storage.googleapis.com`. Libere esses hosts, ou defina
  `SE_PROXY`, `SE_DRIVER_MIRROR_URL`, `SE_CACHE_PATH` (variáveis do Selenium Manager; veja `selenium-manager --help`), ou
  copie um `chromedriver` compatível para `app/runner/drivers/`. `DRIVER_AUTOCHECK=0` desliga a verificação na inicialização.

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

### Conferência com o relatório do MAPA (Comercialização › Verificar lançamentos)

Envie o `.xlsx` exportado do MAPA (`mapaComercializacao`) e compare com o que foi enviado:

- **Por célula = descrição do MAPA × UF de destino.** O MAPA já agrupa produtos de mesma descrição, então esse é o menor nível
  comparável. Totais só por UF e só por produto **não bastam** (erros se compensam); aqui eles são *derivados* das células.
- **Base = o que foi enviado**: o plano (produto, descrição, id, quantidade por UF) é gravado em `job.meta["plano"]` ao confirmar
  o lançamento e no retry. Sem plano (jobs antigos) usa os comandos executados e, por último, a planilha reprocessada com o
  De→Para de hoje (com aviso). Também dá para comparar com uma planilha de vendas avulsa.
- **Situações:** confere · arredondamento (≤ 0,01 por item) · quantidade diferente · falta no MAPA · a mais no MAPA · nome sem
  correspondência (descrição que só existe de um lado: nome diferente ou nunca lançado). Cada divergência traz os produtos/ids que a
  compõem e dicas (duplicidade, item omitido).
- Só entram linhas `Operação = UF`, sem origem de recebimento e em quilogramas; o que ficou de fora é contado e avisado. O SIF do
  relatório é comparado com o da credencial (sem zeros à esquerda). Resultado fica salvo, com exportação `.xlsx` e selo na lista de registros.

#### Conferir direto no portal, complementar e conferência automática

O relatório exportado agrupa por descrição; a tela de alteração do portal **não**: a tabela de produtos (`dtbProduto`) lista cada
linha lançada. O sistema lê essa tabela e compara **linha a linha** com o plano enviado.

- **Ler do portal e conferir** (Verificar lançamentos): um job do worker abre o registro do período, lê as transações e todas as
  páginas de produtos (`lerRegistroComercializacao()` em `comercializacao.js`) e mostra *faltam* / *quantidade diferente* / *sobram*.
  É **somente leitura**: não altera nada, **não cria registro** (falha com `[REGISTRO_INEXISTENTE]`) e não mexe no status do registro.
- **Complementar o que falta**: lê o portal de novo, inclui **só** as linhas que ainda faltam (e a transação da UF, se não existir),
  salva e lê outra vez. Se depois disso ainda faltar linha, o job **falha** mesmo com os comandos tendo retornado OK. É idempotente:
  se nada falta, só lê. **Não exclui e não altera quantidade** (o portal só tem "Excluir" por linha, e o que habilita esse botão ainda
  precisa ser validado no portal real).
- **Conferência automática** (`AUTO_CONFERIR=1`, padrão): depois de cada lançamento de comercialização concluído, enfileira a leitura
  acima; o resultado aparece no selo da lista de registros.
- Observação: as linhas do portal não trazem o id do produto; produtos com mesma descrição se distinguem pela quantidade (empates
  são tratados como multiconjunto e avisados). O portal mostra o tipo da transação como "Expedição" (`tipoTransacao=V`), enquanto o
  cadastro usa o rótulo "Venda": a detecção por rótulo não casa e o sistema trata qualquer transação como existente (conservador).

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
