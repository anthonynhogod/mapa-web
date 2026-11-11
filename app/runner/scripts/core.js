// ===============================================================
// core.js — helpers comuns do PGA-SIGSIF (injetado ANTES do script do módulo)
//
// Problema que resolve: o JSF/PrimeFaces gera ids como "j_idt673:frmDialog:..."
// cujo número muda a cada deploy do portal. Aqui os ids são RESOLVIDOS pela parte
// estável (sufixo/prefixo/dentro do diálogo visível) e o id "legado" é só fallback.
//
// Ordem de resolução de um elemento (S.findEl):
//   1) candidato visível que também é o id legado
//   2) primeiro candidato visível (por sufixo `tail` ou seletor `css`)
//   3) id legado
//   4) primeiro candidato oculto
//
// Reinjetar este arquivo é seguro (idempotente).
// ===============================================================
(function () {
  const S = (window.SIGSIF = window.SIGSIF || {});
  S.version = "2.0";

  // -------------------------------
  // Config global de tempos/esperas
  // -------------------------------
  window.SIGSIF_CFG = Object.assign({
    pfFlushTimeout: 500,     // agressivo por padrão (pode elevar p/ 800–1500 se necessário)
    settleDelay: 40,         // 40–80ms costuma ser suficiente
    pfRequestTimeout: 30000, // teto de uma chamada PrimeFaces.ab (evita travar até o timeout do Selenium)
  }, window.SIGSIF_CFG || {});
  S.cfg = window.SIGSIF_CFG;

  // ====== DEBUG E HELPERS GERAIS ======
  window.jQuery && (window.jQuery.fx.off = true);
  if (window.__SIGSIF_DEBUG__ === undefined) window.__SIGSIF_DEBUG__ = true; // false para silenciar
  window.dbg = S.dbg = function dbg(...a) { if (window.__SIGSIF_DEBUG__) console.log("[SIGSIF]", ...a); };
  window.onceDelay = S.sleep = function sleep(ms) { return new Promise(r => setTimeout(r, ms)); };
  window.fireChange = S.fireChange = function fireChange(el) {
    try { el.dispatchEvent(new Event("change", { bubbles: true })); } catch (e) {}
  };
  S.fireInput = function fireInput(el) {
    try { el.dispatchEvent(new Event("input", { bubbles: true })); } catch (e) {}
  };

  // Mensagem de erro com código estável. Prefixos tratados pelo Python (Navegador):
  //   [TIMEOUT]  -> transitório (pode repetir)
  //   demais     -> negócio/estrutura (não repetir cegamente)
  S.err = function err(code, detail) {
    return `Erro: [${code}] ${detail && (detail.message || detail.stack || detail) || "erro desconhecido"}`;
  };

  // -------------------------------
  // Texto / seletores
  // -------------------------------
  S.norm = function norm(t) {
    return String(t == null ? "" : t).normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/\s+/g, " ").trim().toUpperCase();
  };
  const esc = (v) => String(v).replace(/["\\]/g, "\\$&");
  S.escAttr = esc;
  S.byId = (id) => document.getElementById(id);

  S.visible = function visible(el) {
    if (!el) return false;
    if (el.offsetParent !== null) return true;
    try { return !!(el.getClientRects && el.getClientRects().length); } catch (e) { return false; }
  };

  // Diálogo PrimeFaces visível mais recente (topo da pilha).
  S.topDialog = function topDialog() {
    const ds = Array.from(document.querySelectorAll(".ui-dialog")).filter(S.visible);
    return ds.length ? ds[ds.length - 1] : null;
  };

  // spec: { id (legado), tail (sufixo estável do id), css, within (id|Element) }
  S.findAll = function findAll(spec) {
    let root = document;
    if (spec.within) {
      root = typeof spec.within === "string" ? (document.getElementById(spec.within) || document) : spec.within;
    }
    let cands = [];
    try {
      if (spec.tail) cands = cands.concat(Array.from(root.querySelectorAll(`[id$="${esc(spec.tail)}"]`)));
      if (spec.css) cands = cands.concat(Array.from(root.querySelectorAll(spec.css)));
    } catch (e) { dbg("findAll selector inválido", spec, e); }
    return Array.from(new Set(cands));
  };

  S.findEl = function findEl(spec) {
    const legacy = spec.id ? document.getElementById(spec.id) : null;
    const cands = S.findAll(spec);
    const vis = cands.filter(S.visible);
    if (legacy && vis.includes(legacy)) return legacy;
    if (vis.length) return vis[0];
    if (legacy) return legacy;
    return cands[0] || null;
  };

  S.waitFor = function waitFor(fn, { timeout = 10000, interval = 100, label = "condição" } = {}) {
    return new Promise((resolve, reject) => {
      const t0 = Date.now();
      (function poll() {
        let v = null;
        try { v = fn(); } catch (e) { v = null; }
        if (v) return resolve(v);
        if (Date.now() - t0 >= timeout) return reject(new Error(`[TIMEOUT] ${label} não satisfeita em ${timeout}ms`));
        setTimeout(poll, interval);
      })();
    });
  };

  S.waitEl = function waitEl(spec, { timeout = 10000, interval = 100, label } = {}) {
    return S.waitFor(() => S.findEl(spec), {
      timeout, interval,
      label: label || `elemento ${spec.id || spec.tail || spec.css}`,
    });
  };

  // Compat: espera por id exato (comportamento original, agora com código [TIMEOUT])
  window.waitForElement = S.waitForElement = function waitForElement(id, maxRetries = 40, delay = 250) {
    return S.waitFor(() => document.getElementById(id), {
      timeout: maxRetries * delay, interval: delay, label: `waitForElement ID=${id}`,
    }).catch(e => { throw new Error(`waitForElement: ID=${id} não encontrado (${maxRetries} tentativas) ${e.message}`); });
  };

  // Id (string) a usar como `s:` do PrimeFaces.ab, resolvido com fallback ao legado.
  S.srcId = async function srcId(spec, opts) {
    try {
      const el = await S.waitEl(spec, Object.assign({ timeout: 8000 }, opts || {}));
      return el.id;
    } catch (e) {
      if (spec.id) { dbg("srcId: usando id legado", spec.id); return spec.id; }
      throw e;
    }
  };

  // Id do <form> que contém o elemento (ex.: "j_idt664:frmDialog").
  S.formOf = function formOf(el) {
    const f = el && (el.form || (el.closest && el.closest("form")));
    return f ? f.id : null;
  };

  // -------------------------------
  // PrimeFaces.ab com timeout e erro
  // -------------------------------
  S.queueEmpty = function queueEmpty() {
    try { return !!(PrimeFaces && PrimeFaces.ajax && PrimeFaces.ajax.Queue && PrimeFaces.ajax.Queue.isEmpty()); }
    catch (_) { return true; }
  };

  S.ajaxIdle = function ajaxIdle(budget) {
    const to = typeof budget === "number" ? budget : (S.cfg.pfFlushTimeout || 1500);
    return new Promise((resolve) => {
      const t0 = Date.now();
      (function spin() {
        if (S.queueEmpty() || Date.now() - t0 > to) return resolve();
        setTimeout(spin, 80);
      })();
    });
  };

  async function pfRequest(cfg, label = "pfRequest", opts = {}) {
    const timeout = opts.timeout || S.cfg.pfRequestTimeout;
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = (fn, v) => { if (done) return; done = true; clearTimeout(timer); fn(v); };
      const timer = setTimeout(() => {
        // O servidor pode ter respondido sem disparar onco (ex.: partial-response sem oncomplete).
        if (S.queueEmpty()) { dbg(label, "sem onco, fila vazia: seguindo"); finish(resolve, { timedOut: true }); }
        else finish(reject, new Error(`[TIMEOUT] PF:${label} sem resposta em ${timeout}ms`));
      }, timeout);
      try {
        cfg = Object.assign({}, cfg || {});
        const oncoUser = cfg.onco || cfg.oncomplete;
        const oncoDone = function (xhr, status, args) {
          try { if (typeof oncoUser === "function") oncoUser(xhr, status, args); }
          catch (e) { dbg(label, "onco do chamador falhou", e); }
          finish(resolve, { xhr, status, args });
        };
        delete cfg.oncomplete;
        cfg.onco = oncoDone;
        const onerrUser = cfg.onerror;
        cfg.onerror = function (xhr, status, err) {
          try { if (typeof onerrUser === "function") onerrUser(xhr, status, err); } catch (_) {}
          finish(reject, new Error(`[PF_ERROR] PF:${label} ${status || ""} ${(err && err.message) || err || ""}`.trim()));
        };
        PrimeFaces.ab(cfg);
      } catch (err) {
        finish(reject, new Error(`PF:${label} falhou: ${err && err.message || err}`));
      }
    });
  }
  window.pfRequest = S.pf = pfRequest;

  // -----------------------------
  // Buffer compartilhado de mensagens (growl/PF + erros JS não tratados)
  // -----------------------------
  window.__PF_MESSAGES = window.__PF_MESSAGES || [];
  window.__pushPfMessage = window.__pushPfMessage || function (sev, txt) {
    const t = (txt || "").trim();
    if (!t) return;
    window.__PF_MESSAGES.push({ ts: Date.now(), sev, text: t });
    window.__PF_MESSAGES = window.__PF_MESSAGES.slice(-50);
  };

  (function () {
    const push = window.__pushPfMessage;
    if (window.__sigsifGrowlHooked) return;
    try {
      if (window.PrimeFaces && PrimeFaces.widget && PrimeFaces.widget.Growl && PrimeFaces.widget.Growl.prototype.show) {
        const orig = PrimeFaces.widget.Growl.prototype.show;
        PrimeFaces.widget.Growl.prototype.show = function (msgs) {
          try { (msgs || []).forEach(m => push(m.severity || "info", m.detail || m.summary || "")); } catch (e) {}
          return orig.apply(this, arguments);
        };
      }
      if (window.PrimeFaces && PrimeFaces.showMessage) {
        const orig2 = PrimeFaces.showMessage;
        PrimeFaces.showMessage = function (cfg) {
          try { push((cfg && cfg.severity) || "info", (cfg && (cfg.detail || cfg.summary)) || ""); } catch (e) {}
          return orig2.apply(this, arguments);
        };
      }
      window.__sigsifGrowlHooked = true;
    } catch (e) {}
    const _old = window.getMensagemErroEstatica;
    window.getMensagemErroEstatica = function () {
      const t = (typeof _old === "function") ? _old() : null;
      if (t) return t;
      return window.__PF_MESSAGES.length ? window.__PF_MESSAGES[window.__PF_MESSAGES.length - 1].text : null;
    };
  })();

  // Hook para erros JS não tratados (exceções e promises rejeitadas)
  (function () {
    const push = window.__pushPfMessage;
    if (window.__jsErrorHooksInstalled) return;
    window.__jsErrorHooksInstalled = true;
    window.addEventListener("error", function (ev) {
      try {
        const base = (ev && (ev.message || (ev.error && ev.error.message))) || "Erro desconhecido";
        const where = (ev && ev.filename) ? ` (${ev.filename}:${ev.lineno || "?"}:${ev.colno || "?"})` : "";
        push("js-error", `${base}${where}`);
      } catch (e) {}
    });
    window.addEventListener("unhandledrejection", function (ev) {
      try {
        const reason = ev && ev.reason;
        push("js-error", String((reason && (reason.message || reason)) || "Promise rejeitada sem motivo informado"));
      } catch (e) {}
    });
  })();

  // --- Coleta mensagens de UI (growl, messages e avisos inline em tabela) ---
  window.collectUiMessages = S.collectUiMessages = function collectUiMessages() {
    const chunks = [];
    const selectors = [
      ".ui-messages-error", ".ui-messages-warn", ".ui-messages-info", ".ui-messages",
      ".ui-growl-message", 'div[id$="mensagensValidacao"]',
      ".ui-message", ".ui-message-error", ".ui-message-warn",
    ];
    for (const sel of selectors) {
      document.querySelectorAll(sel).forEach(el => {
        const t = (el.innerText || "").trim();
        if (t) chunks.push(t);
      });
    }
    document.querySelectorAll("td[colspan], td").forEach(td => {
      const t = (td.innerText || "").trim();
      if (/nenhum registro encontrado/i.test(t)) chunks.push("Nenhum registro encontrado.");
    });
    (window.__PF_MESSAGES || [])
      .filter(m => m.sev === "js-error")
      .forEach(m => chunks.push(`[JS] ${m.text}`));
    return Array.from(new Set(chunks)).join("\n");
  };

  window.getMensagemErroEstatica = function getMensagemErroEstatica() {
    return window.collectUiMessages() || null;
  };

  // Usa SIGSIF_CFG.pfFlushTimeout como default quando não informado
  window.getMensagemErro = S.getMensagemErro = function getMensagemErro(timeoutMs, stepMs = 100) {
    const budget = (typeof timeoutMs === "number" ? timeoutMs : (S.cfg && S.cfg.pfFlushTimeout) || 800);
    return new Promise(resolve => {
      const t0 = Date.now();
      (function poll() {
        const msg = window.collectUiMessages();
        if (msg) return resolve(msg);
        if (Date.now() - t0 >= budget) return resolve(null);
        setTimeout(poll, Math.max(16, stepMs));
      })();
    });
  };

  // Mensagens COM severidade (só o que o portal marcou como erro/fatal).
  const ERR_SEL = ".ui-messages-error, .ui-messages-fatal, .ui-message-error, .ui-growl-error, .ui-growl-message-error";
  S.uiErrors = function uiErrors() {
    const out = [];
    document.querySelectorAll(ERR_SEL).forEach(el => {
      const t = (el.innerText || "").trim();
      if (t) out.push(t);
    });
    (window.__PF_MESSAGES || []).filter(m => m.sev === "js-error").forEach(m => out.push(`[JS] ${m.text}`));
    return Array.from(new Set(out)).join("\n");
  };

  // Limpa mensagens antigas p/ não confundir o resultado do próximo comando com resíduo.
  S.clearMessages = function clearMessages() {
    document.querySelectorAll(".ui-messages, .ui-growl-item-container, .ui-message-error").forEach(el => {
      if (el.classList.contains("ui-messages")) el.innerHTML = ""; else el.remove();
    });
    window.__PF_MESSAGES = [];
  };

  // Erro de UI após uma ação (já com o onco/ajax concluído): lê o DOM e, se nada aparecer,
  // dá só um respiro curto (`settleDelay`) antes de concluir que não houve erro — não gasta
  // o orçamento inteiro de flush em cada comando bem-sucedido. Retorna string ou null.
  S.readError = async function readError(timeout) {
    const budget = typeof timeout === "number" ? timeout : (S.cfg.settleDelay || 40);
    const t0 = Date.now();
    for (;;) {
      const e = S.uiErrors();
      if (e) return e;
      if (Date.now() - t0 >= budget) break;
      await S.sleep(60);
    }
    const msg = window.collectUiMessages();
    return msg && /erro/i.test(msg) ? msg : null;
  };

  // Voltar para a primeira página do paginator (quando existir)
  window.backFirstPage = S.backFirstPage = async function backFirstPage() {
    try {
      const btn = document.querySelector(".ui-paginator-first");
      if (btn) { btn.click(); await S.sleep(150); }
    } catch (_) {}
  };

  // -------------------------------
  // Seleção / formulário
  // -------------------------------
  // Radio do resultado de uma busca. Prefere o diálogo visível (antes pegava o 1º do documento).
  window.selectRadio = S.selectRadio = async function selectRadio(classe, indexOp = 0, maxRetries = 20, delay = 300) {
    const cls = classe || "ui-radiobutton-box";
    const el = await S.waitFor(() => {
      const dlg = S.topDialog();
      const inDlg = dlg ? dlg.getElementsByClassName(cls)[indexOp] : null;
      return inDlg || document.getElementsByClassName(cls)[indexOp];
    }, { timeout: maxRetries * delay, interval: delay, label: `selectRadio classe=${cls}` });
    try { el.click(); } catch (e) {}
    // alguns diálogos exigem o clique no <input> real do radio
    const dlg = S.topDialog() || document;
    const input = document.querySelector('input[name="j_idt709:frmDialog:resultado:datatable_resultado_radio"]')
      || dlg.querySelector('input[type="radio"][name$=":resultado:datatable_resultado_radio"]');
    if (input) { try { input.click(); } catch (e) {} }
  };

  // Seleciona opção de <select> nativo por texto (normalizado) -> valor -> índice; dispara change.
  // Retorna o índice escolhido ou -1.
  S.selectOption = function selectOption(select, { texts = [], value = null, index = null } = {}) {
    if (!select || !select.options) return -1;
    const opts = Array.from(select.options);
    const alvo = texts.map(S.norm).filter(Boolean);
    let i = -1;
    if (alvo.length) i = opts.findIndex(o => alvo.includes(S.norm(o.text)) || alvo.includes(S.norm(o.value)));
    if (i < 0 && value != null) i = opts.findIndex(o => String(o.value) === String(value));
    if (i < 0 && index != null && Number.isInteger(Number(index))) {
      const k = Number(index);
      if (k >= 0 && k < opts.length) i = k;
    }
    if (i < 0) return -1;
    select.value = opts[i].value;
    S.fireChange(select);
    return i;
  };

  // Escolhe a opção de um select e dispara o change do PrimeFaces (ajax), aguardando o retorno.
  // `spec`: id (string) ou {id, tail, css}.
  S.selectAjax = async function selectAjax(spec, pick, updateSel = "@(.frmDialogInsertSelector)") {
    const sp = typeof spec === "string" ? { id: spec } : spec;
    const sel = await S.waitEl(sp, { label: `select ${sp.id || sp.tail}` });
    const i = S.selectOption(sel, pick);
    if (i < 0) throw new Error(`[SELECT_OPCAO] opção não encontrada em ${sel.id} (pedido=${JSON.stringify(pick)}, opções=${sel.options.length})`);
    const base = sel.id.replace(/_input$/, "");
    await S.pf({ s: base, e: "change", p: base, u: updateSel }, `select:${base}`);
    return i;
  };

  // -------------------------------
  // DataTable: paginação e esperas
  // -------------------------------
  S.tableOf = (el) => (el && el.closest ? el.closest(".ui-datatable") : null);

  S.paginatorBtn = function paginatorBtn(container, which) {
    const root = container || document;
    return root.querySelector(`.ui-paginator-${which}:not(.ui-state-disabled)`);
  };

  // Clica num botão do paginator e espera a tabela ser atualizada.
  S.pageTo = async function pageTo(containerOrGetter, which) {
    // a tabela é re-renderizada pelo PrimeFaces: aceita função para re-localizar a cada passo
    const get = typeof containerOrGetter === "function" ? containerOrGetter : () => containerOrGetter;
    const container = get();
    const btn = S.paginatorBtn(container, which);
    if (!btn) return false;
    const body = container ? container.querySelector("tbody") : null;
    const mudou = body ? new Promise((resolve) => {
      const mo = new MutationObserver(() => { mo.disconnect(); resolve(true); });
      mo.observe(body, { childList: true, subtree: true });
      setTimeout(() => { mo.disconnect(); resolve(false); }, 4000);
    }) : Promise.resolve(false);
    btn.click();
    await mudou;
    await S.ajaxIdle();
    await S.sleep(S.cfg.settleDelay || 80);
    return true;
  };

  // Percorre as páginas (última primeiro; depois da 1ª até a última) até `fn()` achar algo.
  S.searchPages = async function searchPages(container, fn) {
    let r = fn();
    if (r) return r;
    if (await S.pageTo(container, "last")) { r = fn(); if (r) return r; }
    if (await S.pageTo(container, "first")) {
      for (let guard = 0; guard < 100; guard++) {
        r = fn();
        if (r) return r;
        if (!(await S.pageTo(container, "next"))) break;
      }
    }
    return fn();
  };

  window.passarPaginaDiagnostico = S.passarPagina = async function passarPaginaDiagnostico(index) {
    try {
      const btn = document.getElementsByClassName("ui-paginator-last ui-state-default ui-corner-all")[index]
        || document.querySelectorAll(".ui-paginator-last")[index];
      btn && btn.click();
    } catch (_) {}
  };

  // Executa fn() com N tentativas para falhas transitórias ([TIMEOUT]); repassa as demais.
  S.retry = async function retry(fn, { tries = 2, delay = 400, label = "op" } = {}) {
    let last;
    for (let i = 1; i <= tries; i++) {
      try { return await fn(i); }
      catch (e) {
        last = e;
        if (!/\[TIMEOUT\]|\[PF_ERROR\]/.test(String(e && e.message || e))) throw e;
        dbg(`retry ${label} ${i}/${tries}:`, e.message || e);
        await S.sleep(delay * i);
      }
    }
    throw last;
  };

  // Fecha diálogos abertos (recuperação após erro, para não bloquear o próximo comando).
  S.closeDialogs = function closeDialogs(widgetVars) {
    (widgetVars || []).forEach(w => { try { window.PF && PF(w) && PF(w).hide(); } catch (_) {} });
    document.querySelectorAll(".ui-dialog").forEach(d => {
      if (!S.visible(d)) return;
      const x = d.querySelector(".ui-dialog-titlebar-close");
      if (x) { try { x.click(); } catch (_) {} }
    });
  };
})();
