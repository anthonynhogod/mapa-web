// ===============================================================
// producao.js — Mapa de PRODUÇÃO (depende do core.js)
//
// Comandos chamados pelo Python (app/logic/producao.py):
//   abrirInclusaoProducao(competencia, sif)           "MM/AAAA", nº do registro do estabelecimento
//        -> abre a tela de inclusão e preenche estabelecimento, período e "Produção = Sim"
//   incluirProdutoProducao(descricao, idProduto, qtd, ingrediente)
//        -> um produto padronizado (busca por descrição, seleção por id; `ingrediente` "MIX"/"Único" desempata linhas
//           com o mesmo id; vazio = a primeira linha com o id)
//   finalizarRegistroProducao(competencia, esperados) -> confere cabeçalho e nº de linhas e salva (Confirmar)
//   -> cada um devolve "OK: ..." | "Erro: [CODIGO] ..."
//
// O mapa de produção é mensal e NASCE no formulário de inclusão: nada fica gravado no portal até o
// finalizarRegistroProducao(). Por isso uma falha no meio não deixa registro pela metade e o job pode ser
// reexecutado do zero. A única etapa sem volta dentro do job é o "Adicionar" de cada produto: erro depois dele vira
// [ADD_INCERTO] (não-transitório, nunca repetido sozinho) para não duplicar a linha.
//
// Mesmo princípio do comercializacao.js:
//  - ids "j_idt..." resolvidos pela parte estável (id legado só como fallback);
//  - campo DENTRO do diálogo certo (os diálogos de inclusão e de busca têm ambos "...:frmDialog:produto:produto");
//  - produto casado por id EXATO (id=17178 não casa com id=1717), em todas as páginas da busca;
//  - botões acionados pelo próprio onclick do portal (click()), que já traz o `u:`/`onco:` corretos.
// ===============================================================
(function () {
  const S = window.SIGSIF;
  if (!S) { throw new Error("[JS_NOT_READY] core.js não foi injetado antes de producao.js"); }

  const FC = "formConsultarMapaProducao";
  const FI = "formIncluirMapaProducao";
  const W = {
    registro: "dialogPesquisarRegistro",             // pesquisa do estabelecimento
    novo: "dialogIncluirProdutoPadronizado",         // inclusão de produto (quantidade)
    busca: "dialogPesquisarProdutoPadronizado",      // pesquisa do produto padronizado
  };
  const DIALOGOS = [W.registro, W.novo, W.busca];
  // ids "legados" (fallback quando a resolução pela parte estável não acha nada)
  const LEG = {
    novoRegistro: `${FC}:j_idt85:j_idt87:commandPrepareInsert_j_idt87`,
    lupaRegistro: `${FI}:j_idt234`,
    periodo: `${FI}:periodoProducaoMascara`,
    producao: `${FI}:producao:producao_input`,
    novoProduto: `${FI}:producaoProdutosPoa:j_idt249`,
    salvar: `${FI}:j_idt295:j_idt296:commandInsert_j_idt296`,
    sif: "j_idt464:frmDialog:numeroRegistro:numeroRegistro",
    consultaRegistro: "j_idt464:frmDialog:j_idt471:j_idt472:commandDialogQuery_j_idt472",
    vinculaRegistro: "j_idt464:frmDialog:j_idt488:j_idt489:commandDialogBind_j_idt489",
    lupaProduto: "j_idt491:frmDialog:produto:commandSearch_produto",
    quantidade: "j_idt491:frmDialog:quantidade_hinput",
    adiciona: "j_idt491:frmDialog:j_idt501:j_idt502:commandDialogAdd_j_idt502",
    descricao: "j_idt504:frmDialog:produto:produto",
    consultaProduto: "j_idt504:frmDialog:j_idt508:j_idt509:commandDialogQuery_j_idt509",
    vinculaProduto: "j_idt504:frmDialog:j_idt531:j_idt532:commandDialogBind_j_idt532",
  };

  const esc = S.escAttr;
  const ok = (m) => `OK: ${m}`;
  const exc = (code, e) => S.err(code, e);
  const formExists = (name) => !!(S.byId(name) || document.querySelector(`[id^="${name}:"]`));
  const habilitado = (b) => !!b && !b.disabled && !b.classList.contains("ui-state-disabled");
  const digitos = (t) => String(t == null ? "" : t).replace(/\D/g, "");

  // ---------------------------------------------------------------
  // Diálogos / campos
  // ---------------------------------------------------------------
  function dialogoEl(widgetVar) {
    try { const w = PF(widgetVar); return (w && w.jq && w.jq[0]) || null; } catch (_) { return null; }
  }
  function dialogoVisivel(widgetVar) {
    try { const w = PF(widgetVar); return !!(w && w.jq && w.jq.is(":visible")); } catch (_) { return false; }
  }

  // O onco do portal abre/fecha o diálogo; se ele não o fizer a tempo, força pela API do widget.
  async function aguardarDialogo(widgetVar, visivel = true, timeout = 8000) {
    const quer = () => dialogoVisivel(widgetVar) === visivel;
    try { await S.waitFor(quer, { timeout: 2500, label: `diálogo ${widgetVar}` }); return; } catch (_) { /* força abaixo */ }
    try { if (visivel) PF(widgetVar).show(); else PF(widgetVar).hide(); } catch (_) { /* a espera abaixo reporta */ }
    await S.waitFor(quer, { timeout, label: `diálogo ${widgetVar} ${visivel ? "aberto" : "fechado"}` });
  }

  // Campo DENTRO do diálogo certo (ver comercializacao.js: ids com o mesmo sufixo existem em mais de um diálogo).
  function campoNoDialogo(widgetVar, tail, legacyId, { fora = null, timeout = 10000 } = {}) {
    const escolher = (cands) => {
      const vis = cands.filter(S.visible);               // campos "hidden" (ex.: quantidade_hinput) nunca são visíveis
      const pool = vis.length ? vis : cands;
      return pool.find(e => e.id === legacyId) || pool[0] || null;
    };
    const noForm = (e) => !fora || S.formOf(e) !== fora;
    return S.waitFor(() => {
      const dlg = dialogoEl(widgetVar);
      if (dlg) {
        const achou = escolher(S.findAll({ tail, within: dlg }));
        if (achou) return achou;
      }
      return escolher(S.findAll({ tail }).filter(noForm));
    }, { timeout, label: `campo ${tail} no diálogo ${widgetVar}` }).catch((e) => {
      const todos = S.findAll({ tail }).map(x => `${x.id}(${S.visible(x) ? "visível" : "oculto"})`);
      throw new Error(`${e.message} [diálogo ${dialogoEl(widgetVar) ? "achado" : "sem elemento"}; candidatos: ${todos.join(", ") || "nenhum"}]`);
    });
  }

  // Botão de um formulário: parte estável do id (ex.: ":commandDialogBind_"); id legado só como fallback.
  async function botaoDoForm(form, parte, legacyId, timeout = 8000) {
    return S.waitEl({ id: legacyId, css: `[id^="${esc(form)}:"][id*="${parte}"]` }, { timeout, label: `botão ${parte} em ${form}` });
  }

  // Espera o ajax do PrimeFaces terminar (teto generoso: round-trips do portal passam de 1 s).
  async function esperarAjax(max = 8000) {
    await S.sleep(30);
    await S.ajaxIdle(max);
    await S.sleep(S.cfg.settleDelay || 80);
  }

  // Aciona um botão pelo próprio onclick do portal.
  async function clicar(btn, max) {
    btn.click();
    await esperarAjax(max);
  }

  // Confirma a seleção de um diálogo de busca ("Incluir"). O botão nasce desabilitado e só habilita depois do ajax do
  // radio, que pode SUBSTITUIR o nó (@(.commandBindSelector)): por isso ele é re-resolvido a cada checagem, nunca guardado.
  // Se não habilitar a tempo, aciona o PrimeFaces.ab direto (o caminho que a comercialização sempre usou); se a seleção
  // não tiver chegado ao servidor, o passo seguinte devolve a mensagem do próprio portal.
  async function confirmarSelecao(form, parte, legacyId, pfCfg, rotulo) {
    const achar = () => S.findEl({ id: legacyId, css: `[id^="${esc(form)}:"][id*="${parte}"]` });
    let btn = null;
    try {
      btn = await S.waitFor(() => { const b = achar(); return habilitado(b) ? b : null; },
        { timeout: 6000, label: `botão ${rotulo} habilitado` });
    } catch (_) { btn = null; }
    if (btn) { await clicar(btn); return; }
    const alvo = achar();
    if (!alvo) throw new Error(`[TIMEOUT] botão ${rotulo} (${parte}) não encontrado em ${form}`);
    S.dbg(`${rotulo}: botão continuou desabilitado; acionando o ajax direto`);
    await S.pf(Object.assign({ s: alvo.id }, pfCfg), `confirmar:${rotulo}`);
    await esperarAjax();
  }

  function setValor(el, v) {
    el.value = v;
    S.fireInput(el);
    S.fireChange(el);
  }

  // ---------------------------------------------------------------
  // Cabeçalho: estabelecimento, período, "Produção = Sim"
  // ---------------------------------------------------------------
  const campoPeriodo = () => S.findEl({ id: LEG.periodo, tail: ":periodoProducaoMascara" });
  const campoRegistro = () => S.byId(`${FI}:numeroRegistro`);
  const campoProducao = () => S.findEl({ id: LEG.producao, tail: ":producao:producao_input" });

  async function escolherEstabelecimento(sif) {
    const lupa = await S.waitEl({ id: LEG.lupaRegistro, css: `[id="${esc(FI)}:panelNumeroRegistro"] button` },
      { label: "botão de pesquisa do estabelecimento" });
    lupa.click();
    await aguardarDialogo(W.registro);

    const sifEl = await campoNoDialogo(W.registro, "frmDialog:numeroRegistro:numeroRegistro", LEG.sif);
    setValor(sifEl, String(sif));
    const fEmp = S.formOf(sifEl);

    await clicar(await botaoDoForm(fEmp, ":commandDialogQuery_", LEG.consultaRegistro));
    try { await S.backFirstPage(); } catch (_) { /* cosmético */ }

    const dlg = () => dialogoEl(W.registro) || document;
    const radio = () => dlg().querySelector(".ui-radiobutton-box");
    const vazio = () => /nenhum registro encontrado/i.test((dlg().innerText || dlg().textContent || ""));
    await S.waitFor(() => radio() || vazio(), { timeout: 10000, label: "resultado da pesquisa do estabelecimento" });
    if (!radio()) {
      S.closeDialogs([W.registro]);
      return `Erro: [ESTABELECIMENTO_NAO_ENCONTRADO] a pesquisa do registro "${sif}" não devolveu estabelecimento.`;
    }
    await S.selectRadio("ui-radiobutton-box");
    await esperarAjax();

    await confirmarSelecao(fEmp, ":commandDialogBind_", LEG.vinculaRegistro,
      { u: `${fEmp}:msgDialog @(.dialogPesquisarRegistroSelector)` }, "Incluir do estabelecimento");
    await aguardarDialogo(W.registro, false);
    await S.waitFor(() => { const c = campoRegistro(); return c && c.value.trim(); },
      { timeout: 8000, label: "estabelecimento preenchido no formulário" });
    return null;
  }

  async function definirPeriodo(competencia) {
    const el = await S.waitEl({ id: LEG.periodo, tail: ":periodoProducaoMascara" }, { label: "campo do período de produção" });
    setValor(el, competencia);              // o change dispara o ajax do próprio campo (onchange do portal)
    await esperarAjax();
    // o ajax re-renderiza o campo a partir do servidor: o valor que sobra é o que o servidor aceitou
    await S.waitFor(() => { const c = campoPeriodo(); return c && c.value.trim() === competencia; },
      { timeout: 4000, label: `período ${competencia} aceito pelo portal` });
  }

  async function definirProducaoSim() {
    const sel = await S.waitEl({ id: LEG.producao, tail: ":producao:producao_input" }, { label: "select Produção" });
    if (sel.value === "S") return;
    // item do menu do PrimeFaces: atualiza o rótulo e dispara o change (que re-renderiza as tabelas de produtos)
    const item = Array.from(document.querySelectorAll(`[id="${esc(FI)}:producao:producao_items"] li`))
      .find(li => S.norm(li.getAttribute("data-label") || li.textContent) === "SIM");
    if (item) { item.click(); await esperarAjax(); }
    if ((campoProducao() || {}).value !== "S") {
      S.selectOption(sel, { value: "S" });             // dispara o change do select (onchange do portal)
      await esperarAjax();
    }
    await S.waitFor(() => (campoProducao() || {}).value === "S", { timeout: 4000, label: 'select Produção = "S"' });
  }

  // Devolve a descrição do que está errado no cabeçalho, ou null.
  function problemaNoCabecalho(competencia) {
    const p = campoPeriodo();
    if (!p || p.value.trim() !== competencia) return `período do formulário é "${p ? p.value : "(campo ausente)"}", esperado "${competencia}"`;
    const r = campoRegistro();
    if (!r || !r.value.trim()) return "estabelecimento não preenchido";
    const s = campoProducao();
    if (!s || s.value !== "S") return 'select "Produção" não está em "Sim"';
    return null;
  }

  window.abrirInclusaoProducao = async function abrirInclusaoProducao(competencia, sif) {
    try {
      if (!/^(0[1-9]|1[0-2])\/\d{4}$/.test(String(competencia))) return exc("COMPETENCIA_INVALIDA", `"${competencia}" (use MM/AAAA)`);
      S.closeDialogs(DIALOGOS);
      S.clearMessages();

      // 1) tela de inclusão (a partir da consulta); se já estiver nela (nova tentativa), segue preenchendo
      if (!formExists(FI)) {
        if (!formExists(FC)) return exc("SEM_FORMULARIO", "nem a consulta nem a inclusão do mapa de produção estão abertas");
        const novo = await S.waitEl({ id: LEG.novoRegistro, css: `[id^="${FC}:"][id*=":commandPrepareInsert_"]` },
          { label: "botão Novo da consulta" });
        novo.click();
        await S.waitFor(() => formExists(FI), { timeout: 12000, label: "formulário de inclusão do mapa de produção" });
        await esperarAjax();
      }

      // 2) estabelecimento primeiro: a pesquisa re-renderiza o formulário inteiro a partir do servidor
      const falhou = await escolherEstabelecimento(sif);
      if (falhou) return falhou;
      // 3) período e 4) Produção = Sim
      await definirPeriodo(String(competencia));
      await definirProducaoSim();

      const prob = problemaNoCabecalho(String(competencia));
      if (prob) return exc("CABECALHO_INCOMPLETO", prob);
      const err = await S.readError();
      if (err) return `Erro: [ABRIR_INCLUSAO] ${err}`;
      const nome = (S.byId(`${FI}:nomeEstabelecimento:nomeEstabelecimento`) || {}).value || "";
      return ok(`Inclusão aberta: ${competencia}, registro ${campoRegistro().value.trim()}${nome ? " — " + nome.trim() : ""}.`);
    } catch (e) {
      console.error("abrirInclusaoProducao:EXCEPTION", e);
      S.closeDialogs(DIALOGOS);
      return exc("EXC_ABRIR_INCLUSAO", e);
    }
  };

  // ---------------------------------------------------------------
  // Produtos
  // ---------------------------------------------------------------
  const botaoNovoProduto = () => {
    const cands = Array.from(document.querySelectorAll(`button[id^="${esc(FI)}:producaoProdutosPoa:"]`))
      .filter(b => !b.closest("tbody"));
    return cands.find(b => b.id === LEG.novoProduto) || cands.find(b => S.norm(b.textContent) === "NOVO") || cands[0] || null;
  };

  // Linhas do RESULTADO DA BUSCA (nunca as do mapa), dentro do formulário do diálogo de busca.
  const corpoBusca = (form) => {
    const f = form && S.byId(form);
    return f ? f.querySelector("tbody.ui-datatable-data") : null;
  };

  // "nomeIngrediente" do data-rk da linha (normalizado), ou "".
  const ingredienteDe = (rk) => S.norm(((/nomeIngrediente=([^,\]]*)/.exec(rk || "")) || [])[1] || "");

  async function selecionarProduto(form, idProduto, ingrediente) {
    const re = new RegExp(`(^|[^A-Za-z0-9_])id=${String(idProduto)}(?![0-9])`);
    const ing = S.norm(ingrediente || "");
    const tabela = () => { const tb = corpoBusca(form); return tb ? (S.tableOf(tb) || tb.parentElement) : null; };
    const acha = () => {
      const tb = corpoBusca(form);
      for (const linha of (tb ? tb.querySelectorAll("tr[data-rk]") : [])) {
        const rk = linha.getAttribute("data-rk");
        if (rk && re.test(rk) && (!ing || ingredienteDe(rk) === ing)) {
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

  window.incluirProdutoProducao = async function incluirProdutoProducao(descricao, idProduto, quantidade, ingrediente) {
    let adicionando = false;     // a partir do clique em "Adicionar" um erro não pode mais ser repetido
    try {
      if (!formExists(FI)) return exc("SEM_FORMULARIO", "formulário de inclusão do mapa de produção não está aberto");
      S.closeDialogs(DIALOGOS);  // resto de tentativa anterior (nada foi adicionado antes do clique em "Adicionar")
      S.clearMessages();

      // 1) "Novo" -> diálogo de inclusão do produto
      const novo = await S.waitFor(botaoNovoProduto, { timeout: 10000, label: 'botão "Novo" da tabela de produtos' });
      novo.click();
      await aguardarDialogo(W.novo);
      await esperarAjax();

      const q0 = await campoNoDialogo(W.novo, "frmDialog:quantidade_hinput", LEG.quantidade);
      const fIns = S.formOf(q0);

      // 2) lupa -> diálogo de busca do produto padronizado
      await clicar(await botaoDoForm(fIns, ":commandSearch_produto", LEG.lupaProduto));
      await aguardarDialogo(W.busca);

      // 3) descrição + Consultar
      const campoBusca = await campoNoDialogo(W.busca, "frmDialog:produto:produto", LEG.descricao, { fora: fIns });
      campoBusca.focus();
      setValor(campoBusca, descricao);
      if (campoBusca.value !== descricao) {
        S.closeDialogs(DIALOGOS);
        return `Erro: [CAMPO_BUSCA_INVALIDO] não consegui preencher a busca do produto com "${descricao}" (campo ${campoBusca.id}; valor lido: "${campoBusca.value}").`;
      }
      const fQry = S.formOf(campoBusca);
      if (fQry === fIns) {
        S.closeDialogs(DIALOGOS);
        return `Erro: [CAMPO_BUSCA_INVALIDO] o campo de busca (${campoBusca.id}) é do formulário de inclusão, não do de busca.`;
      }
      await clicar(await botaoDoForm(fQry, ":commandDialogQuery_", LEG.consultaProduto));
      await S.waitFor(() => { const tb = corpoBusca(fQry); return tb && tb.querySelector("tr"); },
        { timeout: 10000, label: "resultado da busca do produto" });

      // 4) seleciona o produto pelo id EXATO e confirma (o botão só habilita depois da seleção)
      if (!(await selecionarProduto(fQry, idProduto, ingrediente))) {
        const tb = corpoBusca(fQry);
        const vistos = Array.from(tb ? tb.querySelectorAll("tr[data-rk]") : [])
          .map(l => {
            const rk = l.getAttribute("data-rk") || "";
            const id = (rk.match(/(?:^|[^A-Za-z0-9_])id=(\d+)/) || [])[1];
            const ing = (/nomeIngrediente=([^,\]]*)/.exec(rk) || [])[1];
            return id ? `${id}${ing ? " [" + ing + "]" : ""}` : null;
          }).filter(Boolean);
        S.closeDialogs(DIALOGOS);
        const comIng = ingrediente ? ` com ingrediente "${ingrediente}"` : "";
        return `Erro: [PRODUTO_NAO_ENCONTRADO] id=${idProduto}${comIng} ("${descricao}") não apareceu na busca do portal ` +
          `(termo digitado: "${descricao}"; ${vistos.length} resultado(s) na página atual${vistos.length ? ": " + vistos.slice(0, 8).join(", ") : ""}).`;
      }
      await esperarAjax();
      await confirmarSelecao(fQry, ":commandDialogBind_", LEG.vinculaProduto,
        { u: `${fQry}:msgDialog @(.dialogPesquisarProdutoPadronizadoSelector)` }, "Incluir do produto");
      await aguardarDialogo(W.busca, false);

      // 5) quantidade (campo numérico: o valor enviado é o do _hinput, em decimal com ponto) e Adicionar
      const q = await campoNoDialogo(W.novo, "frmDialog:quantidade_hinput", LEG.quantidade);
      q.value = String(quantidade);
      const fAdd = S.formOf(q);
      const add = await botaoDoForm(fAdd, ":commandDialogAdd_", LEG.adiciona);

      adicionando = true;
      await S.pf({
        s: add.id,
        p: fAdd,
        u: `${fAdd} ${fAdd}:msgDialog @(.dialogIncluirProdutoPadronizadoSelector)`,
      }, "addProduto");
      await esperarAjax();

      const err = await S.readError();
      if (err) {
        S.closeDialogs(DIALOGOS);
        return `Erro: [INCLUIR_PRODUTO] ${descricao}: ${err}`;
      }
      if (dialogoVisivel(W.novo)) { try { PF(W.novo).hide(); } catch (_) { /* já fechado */ } }
      return ok(`Produto ${idProduto}${ingrediente ? " [" + ingrediente + "]" : ""} (${descricao}) incluído: ${quantidade}.`);
    } catch (e) {
      console.error("incluirProdutoProducao:EXCEPTION", e);
      const msg = String((e && e.message) || e);
      S.closeDialogs(DIALOGOS);
      if (adicionando) {
        // o texto não pode conter marcas de erro transitório ([TIMEOUT] etc.), que o Python repete sozinho
        const limpa = msg.replace(/\[TIMEOUT\]/g, "tempo esgotado").replace(/\[PF_ERROR\]/g, "falha do portal");
        return `Erro: [ADD_INCERTO] não consegui confirmar a inclusão de "${descricao}" (${limpa}). O produto pode ter sido incluído; ` +
          "nada foi salvo no registro — reexecute o job para recomeçar do zero.";
      }
      return exc("EXC_INCLUIR_PRODUTO", e);
    }
  };

  // ---------------------------------------------------------------
  // Conferência da tabela de produtos e salvar
  // ---------------------------------------------------------------
  const corpoProdutos = () => document.querySelector(`tbody[id="${esc(FI)}:producaoProdutosPoa_data"]`)
    || document.querySelector(`tbody[id$="producaoProdutosPoa_data"]`);

  // Conta as linhas de produto do mapa em TODAS as páginas (a linha "Nenhum registro encontrado" não tem data-ri).
  async function contarProdutos() {
    const tabela = () => { const tb = corpoProdutos(); return tb ? S.tableOf(tb) : null; };
    if (!tabela()) return null;
    await S.pageTo(tabela, "first");
    let total = 0;
    for (let guard = 0; guard < 400; guard++) {
      const tb = corpoProdutos();
      total += tb ? tb.querySelectorAll("tr[data-ri]").length : 0;
      if (!(await S.pageTo(tabela, "next"))) break;
    }
    await S.pageTo(tabela, "first");
    return total;
  }

  window.finalizarRegistroProducao = async function finalizarRegistroProducao(competencia, esperados) {
    try {
      if (!formExists(FI)) return exc("SEM_FORMULARIO", "formulário de inclusão do mapa de produção não está aberto");
      S.closeDialogs(DIALOGOS);
      S.clearMessages();

      // o cabeçalho pode ter sido re-renderizado desde o início: reconfere (e repõe o período, se sumiu)
      const p = campoPeriodo();
      if (p && !p.value.trim()) { await definirPeriodo(String(competencia)); }
      const prob = problemaNoCabecalho(String(competencia));
      if (prob) return exc("CABECALHO_INCOMPLETO", `${prob}. Nada foi salvo.`);

      const n = await contarProdutos();
      if (n === null) return exc("SEM_TABELA_PRODUTOS", "tabela de produtos do mapa não encontrada. Nada foi salvo.");
      if (Number(esperados) !== n) {
        return exc("LINHAS_DIVERGENTES", `a tabela de produtos tem ${n} linha(s), esperado ${esperados}. Nada foi salvo; confira o portal e reexecute o job.`);
      }

      const salvar = await S.srcId({ id: LEG.salvar, css: `[id^="${esc(FI)}:"][id*=":commandInsert_"]` });
      S.clearMessages();
      await S.pf({ s: salvar, u: "outContent mensagensValidacao" }, "salvarProducao");
      await esperarAjax(12000);

      const err = await S.readError(400);
      if (err) return `Erro: [FINALIZAR] ${err}`;
      // avisos de validação que o portal mostra sem severidade "erro" (obrigatório, duplicado...) também impedem o "salvo"
      const msg = (window.collectUiMessages && window.collectUiMessages()) || "";
      if (/j[aá] existe|duplicad|obrigat[oó]ri|inv[aá]lid|n[aã]o foi poss[ií]vel/i.test(msg)) return `Erro: [FINALIZAR] ${msg}`;
      return ok(`Mapa de produção ${competencia} salvo com ${n} produto(s).${msg ? " Portal: " + msg.replace(/\s+/g, " ").slice(0, 200) : ""}`);
    } catch (e) {
      console.error("finalizarRegistroProducao:EXCEPTION", e);
      const msg = String((e && e.message) || e).replace(/\[TIMEOUT\]/g, "tempo esgotado");
      // falha depois de enviar o Confirmar não pode ser repetida: o mapa pode ter sido salvo
      return `Erro: [EXC_FINALIZAR] ${msg}. O mapa pode ou não ter sido salvo: confira no portal antes de reexecutar.`;
    }
  };
})();
