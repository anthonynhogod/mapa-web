// ===============================================================
// comercializacao.js — Mapa de COMERCIALIZAÇÃO (depende do core.js)
//
// Comandos chamados pelo Python (app/logic/comercializacao.py):
//   findRegistroComercializacao(ini, fim)          -> "Nenhum registro encontrado." | msg | "OK"
//   criarRegistroComercializacao(ini, fim, sif)    -> "OK: ..." | "OK: [MODO_INCLUIR] ..." | "Erro: ..."
//   alterarRegistroAtivoComercializacao()
//   verificarRegistroVazio()      aborta se o registro do período já tem estados
//   limparTransacoes()            (retry "limpando portal")
//   incluirEstadoVenda(uf, ufIndex)
//   incluirProdutoVenda(uf, linhaHint, quantidade, descricao, idProduto)
//   finalizarRegistroComercializacao()
//
// Melhorias sobre o legado (incluirEstado/selecionaEstado/incluirEmpresa/salvarRegistro):
//  - ids "j_idt..." resolvidos pela parte estável (id legado só como fallback);
//  - modo (Alterar/Incluir) detectado no DOM, não hardcoded;
//  - linha do estado localizada pelo TEXTO da UF (índice só como dica) e paginação automática;
//  - produto casado por id EXATO (id=17178 não casa com id=1717) e busca em todas as páginas;
//  - erro de UI detectado por severidade; diálogos fechados após falha (não trava o próximo comando);
//  - salvar aguarda o servidor (antes retornava sem esperar).
// ===============================================================
(function () {
  const S = window.SIGSIF;
  if (!S) { throw new Error("[JS_NOT_READY] core.js não foi injetado antes de comercializacao.js"); }

  const FORMS = {
    consultar: "formConsultarMapaComercializacao",
    alterar: "formAlterarMapaComercializacao",
    incluir: "formIncluirMapaComercializacao",
  };

  // Parâmetros do formulário "Incluir estado" (índices das opções dos selects do portal).
  // Sobrescrevíveis via window.SIGSIF_COM_CFG antes da injeção.
  const CFG = Object.assign({}, { tipoTransacao: 1, ambito: 1, tipoOperador: 2 }, window.SIGSIF_COM_CFG || {});

  const UF_NOMES = {
    AC: "ACRE", AL: "ALAGOAS", AM: "AMAZONAS", AP: "AMAPA", BA: "BAHIA", CE: "CEARA",
    DF: "DISTRITO FEDERAL", ES: "ESPIRITO SANTO", GO: "GOIAS", MA: "MARANHAO",
    MG: "MINAS GERAIS", MS: "MATO GROSSO DO SUL", MT: "MATO GROSSO", PA: "PARA",
    PB: "PARAIBA", PE: "PERNAMBUCO", PI: "PIAUI", PR: "PARANA", RJ: "RIO DE JANEIRO",
    RN: "RIO GRANDE DO NORTE", RO: "RONDONIA", RR: "RORAIMA", RS: "RIO GRANDE DO SUL",
    SC: "SANTA CATARINA", SE: "SERGIPE", SP: "SAO PAULO", TO: "TOCANTINS",
  };

  const esc = S.escAttr;
  const ok = (m) => `OK: ${m}`;
  const exc = (code, e) => S.err(code, e);
  const campo = (tail, legacyId) => S.waitEl({ id: legacyId, tail }, { timeout: 10000 });
  const botao = (form, parte, legacyId) =>
    S.srcId({ id: legacyId, css: `[id^="${esc(form)}:"][id*="${parte}"]` });

  // ---------------------------------------------------------------
  // Modo / formulário ativo
  // ---------------------------------------------------------------
  const formExists = (name) => !!(S.byId(name) || document.querySelector(`[id^="${name}:"]`));
  function formAtivo() {
    if (formExists(FORMS.alterar)) return FORMS.alterar;
    if (formExists(FORMS.incluir)) return FORMS.incluir;
    return null;
  }
  const modoDe = (F) => (F === FORMS.alterar ? "Alterar" : F === FORMS.incluir ? "Incluir" : null);

  function setData(el, v) {
    el.value = v;
    S.fireInput(el);
    S.fireChange(el);
  }

  // ---------------------------------------------------------------
  // Tabela de transações (estados)
  // ---------------------------------------------------------------
  const tblButtons = (F, sufixo) =>
    Array.from(document.querySelectorAll(`[id^="${esc(F)}:dtbTransacoes:"][id$=":${sufixo}"]`));
  const BTN_PRODUTO = "commandEventIncluirProduto";

  const tabelaDe = (F) => () => {
    const b = tblButtons(F, BTN_PRODUTO)[0];
    return S.tableOf(b) || document.querySelector(`[id^="${esc(F)}:dtbTransacoes"].ui-datatable`) || null;
  };

  function ufsDaLinha(tr) {
    const achados = new Set();
    tr.querySelectorAll("td").forEach(td => {
      const t = S.norm(td.innerText);
      if (!t) return;
      for (const [uf, nome] of Object.entries(UF_NOMES)) {
        if (t === uf || t === nome || t.startsWith(`${uf} -`) || t.startsWith(`${uf} /`) ||
            (t.length <= 40 && t.includes(nome))) achados.add(uf);
      }
    });
    return achados;
  }

  // Botão "incluir produto" da linha cuja UF bate (único), na página atual.
  function botaoPorTexto(F, uf) {
    const hits = tblButtons(F, BTN_PRODUTO).filter(b => {
      const tr = b.closest("tr");
      return tr && ufsDaLinha(tr).has(uf);
    });
    return hits.length === 1 ? hits[0] : null;
  }

  // Botão pelo índice dica; só aceita se a linha não contradisser a UF pedida.
  function botaoPorIndice(F, uf, hint) {
    if (hint == null || hint < 0) return null;
    const b = S.byId(`${F}:dtbTransacoes:${hint}:${BTN_PRODUTO}`);
    if (!b) return null;
    const tr = b.closest("tr");
    const ufs = tr ? ufsDaLinha(tr) : new Set();
    if (ufs.size && !ufs.has(uf)) return null; // a linha é de outra UF
    return b;
  }

  async function acharBotaoProduto(F, uf, hint) {
    const procura = () => botaoPorTexto(F, uf) || botaoPorIndice(F, uf, hint);
    return await S.searchPages(tabelaDe(F), procura);
  }

  const contarLinhas = (F) => tblButtons(F, BTN_PRODUTO).length;

  // ---------------------------------------------------------------
  // Registro: localizar / criar / abrir para alteração
  // ---------------------------------------------------------------
  window.findRegistroComercializacao = async function findRegistroComercializacao(ini, fim) {
    try {
      const F = FORMS.consultar;
      window.__SIGSIF_COM = { ini, fim };
      const pfTO = S.cfg.pfFlushTimeout || 1500;
      const settle = S.cfg.settleDelay || 80;

      const di = await S.waitEl({
        id: `${F}:periodoComercializacao:periodoComercializacao_startDate_input`,
        tail: ":periodoComercializacao_startDate_input",
      });
      const df = await S.waitEl({
        id: `${F}:periodoComercializacao:periodoComercializacao_endDate_input`,
        tail: ":periodoComercializacao_endDate_input",
      });
      setData(di, ini);
      setData(df, fim);

      const queryId = await S.srcId({
        id: `${F}:j_idt83:mapaComercializacao:commandQuery_mapaComercializacao`,
        css: `[id^="${F}:"][id$=":mapaComercializacao:commandQuery_mapaComercializacao"]`,
      });

      let msg = null;
      for (let t = 0; t < 3; t++) {
        S.clearMessages();
        await S.pf({ s: queryId, p: F, u: `${F} mensagensValidacao` }, "queryMapaComercializacao");
        await S.ajaxIdle(pfTO);
        await S.sleep(settle);
        try { await S.backFirstPage(); } catch (_) {}
        msg = await S.getMensagemErro(pfTO);
        if (msg && /erro/i.test(msg)) return `Erro: [QUERY_COMERCIALIZACAO] ${msg}`;
        if (!msg || !/nenhum registro encontrado/i.test(msg)) return msg || "OK";
        await S.sleep(Math.max(120, settle));
      }
      return msg || "Nenhum registro encontrado.";
    } catch (e) {
      console.error("findRegistroComercializacao:EXCEPTION", e);
      return exc("EXC_FIND_REGISTRO", e);
    }
  };

  async function selecionarRegistroAtivo() {
    const tabela = Array.from(document.querySelectorAll("tbody[id]"))
      .find(el => el.id.includes("datatable") && el.classList.contains("ui-datatable-data"));
    if (tabela) {
      for (const linha of tabela.querySelectorAll("tr")) {
        const rk = linha.getAttribute("data-rk");
        if (rk && !rk.includes("excluido=S")) {
          const radio = linha.querySelector(".ui-radiobutton-box");
          if (radio) { try { radio.click(); return; } catch (_) { /* tenta a próxima */ } }
        }
      }
    }
    throw new Error("Nenhum registro não excluído encontrado.");
  }

  window.alterarRegistroAtivoComercializacao = async function alterarRegistroAtivoComercializacao() {
    try {
      const F = FORMS.consultar;
      await selecionarRegistroAtivo();
      await S.pf({
        s: await S.srcId({
          id: `${F}:j_idt113:j_idt115:commandPrepareUpdate_j_idt115`,
          css: `[id^="${F}:"][id*=":commandPrepareUpdate_"]`,
        }),
        p: F,
        u: "outContent mensagensValidacao",
      }, "prepareUpdate");
      await S.waitFor(() => formExists(FORMS.alterar), { timeout: 10000, label: "formulário de alteração" });
      return ok("Registro ativo aberto para alteração.");
    } catch (e) {
      console.error("alterarRegistroAtivoComercializacao:EXCEPTION", e);
      return exc("EXC_ALTERAR_ATIVO", e);
    }
  };

  // Cria o registro (empresa + período). Se o portal exigir as transações antes do insert,
  // devolve "OK: [MODO_INCLUIR]" e o insert acontece em finalizarRegistroComercializacao().
  window.criarRegistroComercializacao = async function criarRegistroComercializacao(ini, fim, sif) {
    try {
      const FC = FORMS.consultar, FI = FORMS.incluir;
      window.__SIGSIF_COM = { ini, fim };
      S.clearMessages();

      // 1) modo inclusão
      const prepId = await S.srcId({
        id: `${FC}:j_idt83:j_idt85:commandPrepareInsert_j_idt85`,
        css: `[id^="${FC}:"][id*=":commandPrepareInsert_"]`,
      });
      await S.pf({ s: prepId, p: FC, u: "outContent mensagensValidacao" }, "prepareInsert");
      await S.waitFor(() => formExists(FI), { timeout: 10000, label: "formulário de inclusão" });

      // 2) empresa (estabelecimento)
      await S.pf({
        s: await S.srcId({ id: `${FI}:numeroRegistro:commandSearch_numeroRegistro`, css: `[id^="${FI}:"][id$=":commandSearch_numeroRegistro"]` }),
        u: "mensagensValidacao @(.frmDialogQuerySelector) @(.msgDialogQuerySelector)",
      }, "pesquisarEmpresa");
      await PF("dialogPesquisarRegistroMapaComercializacao").show();

      const sifEl = await campo("frmDialog:numeroRegistro:numeroRegistro", "j_idt499:frmDialog:numeroRegistro:numeroRegistro");
      sifEl.value = String(sif);
      const fEmp = S.formOf(sifEl);

      await S.pf({
        s: await botao(fEmp, ":commandDialogQuery_", "j_idt499:frmDialog:j_idt506:j_idt507:commandDialogQuery_j_idt507"),
        p: fEmp,
        u: `${fEmp} ${fEmp}:msgDialog`,
      }, "queryEmpresa");
      try { await S.backFirstPage(); } catch (_) {}
      try { if (typeof resizeDialog === "function") resizeDialog("dialogPesquisarRegistroMapaComercializacao", "dataTableRegistroDialogQueryWidgetVar"); } catch (_) {}
      await S.selectRadio("ui-radiobutton-box");

      await S.pf({
        s: await botao(fEmp, ":commandDialogBind_", "j_idt499:frmDialog:j_idt523:j_idt524:commandDialogBind_j_idt524"),
        u: `${fEmp}:msgDialog @(.dialogPesquisarRegistroMapaComercializacaoSelector)`,
      }, "bindEmpresa");
      try { await PF("dialogPesquisarRegistroMapaComercializacao").hide(); } catch (_) {}

      // 3) período
      const di = await S.waitEl({ id: `${FI}:periodoComercializacao:periodoComercializacao_startDate_input`, tail: ":periodoComercializacao_startDate_input" });
      const df = await S.waitEl({ id: `${FI}:periodoComercializacao:periodoComercializacao_endDate_input`, tail: ":periodoComercializacao_endDate_input" });
      setData(di, ini);
      setData(df, fim);

      // 4) insert
      S.clearMessages();
      await S.pf({
        s: await S.srcId({ id: `${FI}:j_idt288:j_idt289:commandInsert_j_idt289`, css: `[id^="${FI}:"][id*=":commandInsert_"]` }),
        u: "outContent mensagensValidacao",
      }, "insertRegistro");
      await S.ajaxIdle(S.cfg.pfFlushTimeout || 1500);
      await S.sleep(S.cfg.settleDelay || 80);

      const err = await S.readError();
      if (err) {
        if (/transa[cç]/i.test(err) && formExists(FI)) {
          return ok("[MODO_INCLUIR] O portal exige as transações antes do insert; seguindo no formulário de inclusão.");
        }
        return `Erro: [INSERT_REGISTRO] ${err}`;
      }
      return ok("Registro criado.");
    } catch (e) {
      console.error("criarRegistroComercializacao:EXCEPTION", e);
      S.closeDialogs(["dialogPesquisarRegistroMapaComercializacao"]);
      return exc("EXC_INSERT_REGISTRO", e);
    }
  };

  // ---------------------------------------------------------------
  // Pré-checagem / limpeza
  // ---------------------------------------------------------------
  window.verificarRegistroVazio = async function verificarRegistroVazio() {
    try {
      const F = formAtivo();
      if (!F) return exc("SEM_FORMULARIO", "nenhum formulário de comercialização aberto (Alterar/Incluir)");
      if (modoDe(F) === "Incluir") return ok("Registro novo (modo inclusão).");
      const n = contarLinhas(F);
      if (n > 0) {
        const mais = S.paginatorBtn(tabelaDe(F)(), "next") ? "+" : "";
        return `Erro: [REGISTRO_COM_DADOS] O registro do período já possui estado(s) lançado(s) no portal ` +
          `(${n}${mais} na primeira página). Nada foi alterado. Limpe o registro no portal ou reprocesse ` +
          `com "Retry (limpando portal)" em Admin > Threads.`;
      }
      return ok("Registro sem estados lançados.");
    } catch (e) {
      return exc("EXC_VERIFICAR_VAZIO", e);
    }
  };

  // Remove todas as transações do registro (best effort: o id/diálogo de remoção não consta
  // no legado; procura botões de remoção da tabela e confirma o diálogo, se houver).
  window.limparTransacoes = async function limparTransacoes() {
    try {
      const F = formAtivo();
      if (!F) return exc("SEM_FORMULARIO", "nenhum formulário de comercialização aberto");
      if (modoDe(F) === "Incluir") return ok("Registro novo; nada a limpar.");
      const REMOVE = `[id^="${esc(F)}:dtbTransacoes:"][id*=":commandEventRemove"], [id^="${esc(F)}:dtbTransacoes:"][id*=":commandRemove"], [id^="${esc(F)}:dtbTransacoes:"][id*=":commandDelete"]`;
      let removidas = 0;
      for (let guard = 0; guard < 200; guard++) {
        await S.pageTo(tabelaDe(F), "first");
        const b = document.querySelector(REMOVE);
        if (!b) break;
        S.clearMessages();
        await S.pf({ s: b.id, u: `mensagensValidacao ${F}:dtbTransacoes` }, "removerTransacao");
        // possível diálogo de confirmação
        await S.sleep(200);
        const sim = Array.from(document.querySelectorAll(".ui-confirm-dialog button, .ui-confirmdialog-yes, .ui-dialog button"))
          .find(x => S.visible(x) && /^(sim|yes|confirmar|ok)$/i.test((x.innerText || "").trim()));
        if (sim) { sim.click(); await S.ajaxIdle(); }
        await S.ajaxIdle();
        await S.sleep(S.cfg.settleDelay || 80);
        const err = await S.readError(300);
        if (err) return `Erro: [LIMPAR] ${err}`;
        removidas++;
      }
      if (contarLinhas(F) > 0) {
        return exc("LIMPAR_NAO_SUPORTADO", `restaram ${contarLinhas(F)} linha(s); não localizei o botão de remoção. Limpe o registro manualmente no portal.`);
      }
      return ok(`Transações removidas: ${removidas}.`);
    } catch (e) {
      return exc("EXC_LIMPAR", e);
    }
  };

  // ---------------------------------------------------------------
  // Estado (UF) e produto
  // ---------------------------------------------------------------
  function dialogoVisivel(widgetVar) {
    try { const w = PF(widgetVar); return !!(w && w.jq && w.jq.is(":visible")); } catch (_) { return false; }
  }

  window.incluirEstadoVenda = async function incluirEstadoVenda(uf, ufIndex) {
    try {
      const F = formAtivo();
      if (!F) return exc("SEM_FORMULARIO", "nenhum formulário de comercialização aberto");
      S.clearMessages();

      await S.pf({
        s: await S.srcId({ id: `${F}:dtbTransacoes:transacao:commandAdd_transacao`, css: `[id^="${F}:"][id$=":commandAdd_transacao"]` }),
        u: "mensagensValidacao @(.frmDialogQuerySelector) @(.msgDialogQuerySelector) @(.dialogInsertSelector)",
      }, "abrirTransacao");
      await PF("dialogInsertTransacao").show();

      const sel = (id, tail) => ({ id, tail });
      await S.selectAjax(sel("insertTransacao:frmDialog:tipoTransacao_input", "frmDialog:tipoTransacao_input"), { index: CFG.tipoTransacao });
      await S.waitEl(sel("insertTransacao:frmDialog:ambito_input", "frmDialog:ambito_input"));
      await S.selectAjax(sel("insertTransacao:frmDialog:ambito_input", "frmDialog:ambito_input"), { index: CFG.ambito });
      await S.waitEl(sel("insertTransacao:frmDialog:tipoOperador_input", "frmDialog:tipoOperador_input"));
      await S.selectAjax(sel("insertTransacao:frmDialog:tipoOperador_input", "frmDialog:tipoOperador_input"), { index: CFG.tipoOperador });
      await S.waitEl(sel("insertTransacao:frmDialog:uf_ufI:uf_ufI_input", "frmDialog:uf_ufI:uf_ufI_input"));
      // UF: pelo texto da opção (sigla/nome); o índice cadastrado no De->Para é o fallback
      await S.selectAjax(
        sel("insertTransacao:frmDialog:uf_ufI:uf_ufI_input", "frmDialog:uf_ufI:uf_ufI_input"),
        { texts: [uf, UF_NOMES[uf]], index: ufIndex },
      );

      const FD = "insertTransacao:frmDialog";
      await S.pf({
        s: await botao(FD, ":commandDialogAdd_", "insertTransacao:frmDialog:j_idt577:j_idt578:commandDialogAdd_j_idt578"),
        p: FD,
        u: `${FD} ${FD}:msgDialog @(.dialogInsertTransacaoSelector)`,
      }, "addTransacao");

      const err = await S.readError();
      if (err) {
        S.closeDialogs(["dialogInsertTransacao"]);
        return `Erro: [INCLUIR_ESTADO] ${uf}: ${err}`;
      }
      if (dialogoVisivel("dialogInsertTransacao")) { try { PF("dialogInsertTransacao").hide(); } catch (_) {} }
      return ok(`Estado ${uf} incluído.`);
    } catch (e) {
      console.error("incluirEstadoVenda:EXCEPTION", e);
      S.closeDialogs(["dialogInsertTransacao"]);
      return exc("EXC_INCLUIR_ESTADO", e);
    }
  };

  // Seleciona o produto (id EXATO) na tabela de resultados, em qualquer página.
  async function selecionarProduto(idProduto) {
    const re = new RegExp(`(^|[^A-Za-z0-9_])id=${String(idProduto)}(?![0-9])`);
    const tabela = () => {
      const tb = document.querySelector('tbody[id$="PesquisarProdutoPadronizado_data"]');
      return tb ? (S.tableOf(tb) || tb.parentElement) : null;
    };
    const acha = () => {
      for (const linha of document.querySelectorAll("tr[data-rk]")) {
        const rk = linha.getAttribute("data-rk");
        if (rk && re.test(rk)) {
          const radio = linha.querySelector(".ui-radiobutton-box");
          if (radio) return radio;
        }
      }
      return null;
    };
    const radio = await S.searchPages(tabela, acha);
    if (!radio) return false;
    radio.click();
    return true;
  }

  window.incluirProdutoVenda = async function incluirProdutoVenda(uf, hint, quantidade, descricao, idProduto) {
    try {
      const F = formAtivo();
      if (!F) return exc("SEM_FORMULARIO", "nenhum formulário de comercialização aberto");
      S.clearMessages();

      // 1) linha da UF (por texto; índice é só dica) -> abre o diálogo do produto
      const btn = await acharBotaoProduto(F, uf, hint);
      if (!btn) return `Erro: [UF_NAO_ENCONTRADA] linha da UF ${uf} não localizada na tabela de estados (dica de índice=${hint}).`;
      await S.pf({
        s: btn.id,
        u: "mensagensValidacao @(.frmDialogInsertSelector) @(.frmDialogQuerySelector) @(.msgDialogQuerySelector) @(.dialogInsertSelector)",
      }, "abrirProduto");
      await PF("dialogInsertProdutoPadronizado").show();

      // 2) quantidade
      const q = await campo("frmDialog:quantidade_hinput", "j_idt597:frmDialog:quantidade_hinput");
      q.value = quantidade;
      const fIns = S.formOf(q);

      // 3) busca do produto padronizado
      await PF("dialogQueryProdutoPadronizado").show();
      const campoProduto = await campo("frmDialog:produto:produto", "j_idt611:frmDialog:produto:produto");
      campoProduto.value = descricao;
      const fQry = S.formOf(campoProduto);
      await S.pf({
        s: await botao(fQry, ":commandDialogQuery_", "j_idt611:frmDialog:j_idt616:j_idt617:commandDialogQuery_j_idt617"),
        p: fQry,
        u: `${fQry} ${fQry}:msgDialog`,
      }, "queryProduto");
      try { if (typeof resizeDialogQuery === "function") resizeDialogQuery(); } catch (_) {}
      await S.waitEl({
        id: "j_idt611:frmDialog:dtbPesquisarProdutoPadronizado:datatable_dtbPesquisarProdutoPadronizado_data",
        tail: ":datatable_dtbPesquisarProdutoPadronizado_data",
      }, { timeout: 10000, label: "resultado da busca do produto" });

      // 4) seleciona o produto e confirma
      if (!(await selecionarProduto(idProduto))) {
        S.closeDialogs(["dialogQueryProdutoPadronizado", "dialogInsertProdutoPadronizado"]);
        return `Erro: [PRODUTO_NAO_ENCONTRADO] id=${idProduto} ("${descricao}") não apareceu na busca do portal.`;
      }
      await S.pf({
        s: await botao(fQry, ":commandDialogBind_", "j_idt611:frmDialog:j_idt637:j_idt638:commandDialogBind_j_idt638"),
        u: `${fQry}:msgDialog @(.dialogQueryProdutoPadronizadoSelector)`,
      }, "bindProduto");
      try { PF("dialogQueryProdutoPadronizado").hide(); } catch (_) {}

      // 5) adiciona
      await S.pf({
        s: await botao(fIns, ":commandDialogAdd_", "j_idt597:frmDialog:j_idt608:j_idt609:commandDialogAdd_j_idt609"),
        p: fIns,
        u: `${fIns} ${fIns}:msgDialog @(.dialogInsertProdutoPadronizadoSelector)`,
      }, "addProduto");

      const err = await S.readError();
      if (err) {
        S.closeDialogs(["dialogQueryProdutoPadronizado", "dialogInsertProdutoPadronizado"]);
        return `Erro: [INCLUIR_PRODUTO] ${uf}/${descricao}: ${err}`;
      }
      if (dialogoVisivel("dialogInsertProdutoPadronizado")) { try { PF("dialogInsertProdutoPadronizado").hide(); } catch (_) {} }
      return ok(`Produto ${idProduto} incluído em ${uf}.`);
    } catch (e) {
      console.error("incluirProdutoVenda:EXCEPTION", e);
      S.closeDialogs(["dialogQueryProdutoPadronizado", "dialogInsertProdutoPadronizado"]);
      return exc("EXC_INCLUIR_PRODUTO", e);
    }
  };

  // ---------------------------------------------------------------
  // Salvar
  // ---------------------------------------------------------------
  window.finalizarRegistroComercializacao = async function finalizarRegistroComercializacao() {
    try {
      const F = formAtivo();
      if (!F) return exc("SEM_FORMULARIO", "nenhum formulário de comercialização aberto");
      S.clearMessages();

      let srcSpec;
      if (modoDe(F) === "Alterar") {
        srcSpec = { id: `${F}:j_idt359:j_idt360:commandUpdate_j_idt360`, css: `[id^="${F}:"][id*=":commandUpdate_"]` };
      } else {
        // modo inclusão: o período precisa estar preenchido antes do insert
        const per = window.__SIGSIF_COM || {};
        if (per.ini && per.fim) {
          setData(await S.waitEl({ id: `${F}:periodoComercializacao:periodoComercializacao_startDate_input`, tail: ":periodoComercializacao_startDate_input" }), per.ini);
          setData(await S.waitEl({ id: `${F}:periodoComercializacao:periodoComercializacao_endDate_input`, tail: ":periodoComercializacao_endDate_input" }), per.fim);
        }
        srcSpec = { id: `${F}:j_idt288:j_idt289:commandInsert_j_idt289`, css: `[id^="${F}:"][id*=":commandInsert_"]` };
      }

      await S.pf({ s: await S.srcId(srcSpec), u: "outContent mensagensValidacao" }, "salvarRegistro");
      await S.ajaxIdle(S.cfg.pfFlushTimeout || 1500);
      await S.sleep(S.cfg.settleDelay || 80);

      const err = await S.readError();
      if (err) return `Erro: [FINALIZAR] ${err}`;
      return ok("Registro salvo.");
    } catch (e) {
      console.error("finalizarRegistroComercializacao:EXCEPTION", e);
      return exc("EXC_FINALIZAR", e);
    }
  };
})();
