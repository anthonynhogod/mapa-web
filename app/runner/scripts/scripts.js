// ===============================
// scripts.js — Mapa de ABATE (depende do core.js)
//
// Os ids gerados pelo JSF ("j_idt673:frmDialog:...") mudam a cada deploy do portal.
// Aqui cada elemento é resolvido pela parte estável do id (sufixo / dentro do diálogo
// visível) e o id antigo fica só como fallback. Funções, argumentos e textos de retorno
// permanecem os mesmos de antes (o Python/worker não muda).
// ===============================
(function () {
  const S = window.SIGSIF;
  if (!S) { throw new Error("[JS_NOT_READY] core.js não foi injetado antes de scripts.js"); }

  // =======================
  // Parâmetros e constantes
  // =======================
  const DEFAULT_UF_INDEX = 23;
  const cfg = () => S.cfg;

  // Utilidade: define o select pelo índice, disparando change()
  function setSelectByIndex(select, idx) {
    if (!select) return -1;
    if (idx == null) return -1;
    const i = Number(idx);
    if (Number.isInteger(i) && i >= 0 && i < select.options.length) {
      select.value = select.options[i].value;
      S.fireChange(select);
      return i;
    }
    return -1;
  }

  // Campo de diálogo: resolve por sufixo estável, com o id legado como fallback.
  const campo = (tail, legacyId) => S.waitEl({ id: legacyId, tail }, { timeout: 10000 });
  // Botão de diálogo (commandDialogAdd_/Query_/Bind_...) dentro do formulário `form`.
  const botao = (form, parte, legacyId) =>
    S.srcId({ id: legacyId, css: `[id^="${S.escAttr(form)}:"][id*="${parte}"]` });
  // Botão pelo texto estável do id, onde ele estiver (preferindo o visível).
  const botaoGlobal = (parte, legacyId, extraCss) =>
    S.srcId({ id: legacyId, css: `[id*="${parte}"]${extraCss || ""}` });

  const msgErro = (mensagem) => mensagem && mensagem.toLowerCase().includes("erro");

  // =======================
  // Funções de inclusão/edição
  // =======================
  window.incluirGta = async function incluirGta(ufIndex, nGta, nSerie, machos, femeas) {
    try {
      const rawIndex = Number(ufIndex);
      const effectiveUfIndex = (Number.isInteger(rawIndex) && rawIndex > 0) ? rawIndex : DEFAULT_UF_INDEX;
      dbg("incluirGta:start", { ufIndex, effectiveUfIndex, nGta, nSerie, machos, femeas });

      await S.pf({
        s: 'formAlterarMapaAbate:dtbSaldos:btnNovoSaldo',
        u: 'formIncluirMapaAbate:dtbSaldos @(.dlgIncluirSaldoSelector) @(.panelSaldos) @(.frmDialogInsertSelector) @(.msgDialogInsertSelector) @(.dlgInserirLoteSelector) @(.dtbIncluirSaldoSelector) @(.frmDialogQuerySelector) @(.msgDialogQuerySelector) @(.dlgVincularDoencaSelector) @(.frmDialogInsertSelector) @(.msgDialogInsertSelector) @(.dlgIncluirPartesAfetadasSelector)'
      }, "abrirNovoSaldo");
      await PF('dlgIncluirSaldo').show();

      await S.pf({
        s: await botaoGlobal(':especie:commandSearch_especie', 'j_idt673:frmDialog:especie:commandSearch_especie'),
        u: 'mensagensValidacao @(.frmDialogQuerySelector) @(.msgDialogQuerySelector)'
      }, "abrirPesquisaEspecie");
      await PF('dlgPesquisarEspecie').show();

      const especie = await campo('frmDialog:codigoEspecie:codigoEspecie', 'j_idt664:frmDialog:codigoEspecie:codigoEspecie');
      especie.value = "7.1";
      const fEsp = S.formOf(especie);

      await S.pf({
        s: await botao(fEsp, ':commandDialogQuery_', 'j_idt664:frmDialog:j_idt666:j_idt667:commandDialogQuery_j_idt667'),
        p: fEsp,
        u: `${fEsp} ${fEsp}:msgDialog`
      }, "queryEspecie");

      await selectRadio('ui-radiobutton-box');

      const bindEsp = await S.waitEl({
        id: 'j_idt659:frmDialog:j_idt670:j_idt671:commandDialogBind_j_idt671',
        css: '[id*=":commandDialogBind_"]'
      }, { timeout: 8000 }).catch(() => null);
      const fBind = (bindEsp && S.formOf(bindEsp)) || 'j_idt659:frmDialog';
      await S.pf({
        s: bindEsp ? bindEsp.id : 'j_idt659:frmDialog:j_idt670:j_idt671:commandDialogBind_j_idt671',
        u: `${fBind}:msgDialog @(.dlgPesquisarEspecieSelector) @(.dlgIncluirSaldoSelector)`
      }, "bindEspecie");
      await PF('dlgPesquisarEspecie').hide();

      const select = await campo('frmDialog:cbbUfSaldo:cbbUfSaldo_input', 'j_idt678:frmDialog:cbbUfSaldo:cbbUfSaldo_input');
      const fGta = S.formOf(select);
      let chosenIndex = setSelectByIndex(select, effectiveUfIndex);
      if (!select.value) {
        const fallbackIndex = setSelectByIndex(select, DEFAULT_UF_INDEX);
        if (!select.value) {
          const snapshot = Array.from(select.options).slice(0, 5).map(o => ({ text: o.text, value: o.value }));
          const msg = `UF não selecionada. recebi ufIndex=${ufIndex}, efetivo=${effectiveUfIndex}, default=${DEFAULT_UF_INDEX}, options=${select.options.length}, primeirasOpcoes=${JSON.stringify(snapshot)}`;
          dbg("incluirGta:UF_FAIL", msg);
          return `Erro: [UF_NOT_SET] ${msg}`;
        }
        chosenIndex = fallbackIndex;
      }

      (await campo('frmDialog:numeroGta:numeroGta', `${fGta}:numeroGta:numeroGta`)).value = nGta;
      (await campo('frmDialog:numeroSerie:numeroSerie', `${fGta}:numeroSerie:numeroSerie`)).value = nSerie;
      (await campo('frmDialog:quantidadeMachos:quantidadeMachos', `${fGta}:quantidadeMachos:quantidadeMachos`)).value = machos;
      (await campo('frmDialog:quantidadeFemeas:quantidadeFemeas', `${fGta}:quantidadeFemeas:quantidadeFemeas`)).value = femeas;

      await S.pf({
        s: await botao(fGta, ':commandDialogAdd_', 'j_idt678:frmDialog:j_idt685:j_idt686:commandDialogAdd_j_idt686'),
        p: fGta,
        u: `${fGta} ${fGta}:msgDialog @(.dlgIncluirSaldoSelector)`
      }, "addSaldo");

      const mensagem = await getMensagemErro(cfg().pfFlushTimeout);
      if (msgErro(mensagem)) {
        return `Erro: [INCLUIR_GTA] ${mensagem}`;
      }
      dbg("incluirGta:OK", { chosenIndex, value: select.value });
      return "OK: Inclusão de GTA realizada.";
    } catch (err) {
      console.error("incluirGta:EXCEPTION", err);
      return `Erro: [EXC_INCLUIR_GTA] ${err && (err.message || err.stack) || 'erro desconhecido'}`;
    }
  };

  window.incluirTipoGta = async function incluirTipoGta(indexGta, tipoIndex, lote, quantMachos = 0, pesoMachos = 0, quantFemeas = 0, pesoFemeas = 0) {
    try {
      await S.pf({
        s: 'formAlterarMapaAbate:dtbSaldos:' + indexGta + ':commandAdd_inserirLote',
        u: '@(.frmDialogInsertSelector) @(.msgDialogInsertSelector) @(.dlgInserirLoteSelector) @(.dtbIncluirSaldoSelector) @(.frmDialogQuerySelector) @(.msgDialogQuerySelector) @(.dlgVincularDoencaSelector) @(.frmDialogInsertSelector) @(.msgDialogInsertSelector) @(.dlgIncluirPartesAfetadasSelector)'
      }, "abrirInserirLote");
      await PF('dlgInserirLote').show();

      const selTipo = await campo('frmDialog:tipoLote:tipoLote_input', 'j_idt688:frmDialog:tipoLote:tipoLote_input');
      const f = S.formOf(selTipo);
      if (!selTipo.options[tipoIndex]) return `Erro: [TIPO_INDEX] Índice ${tipoIndex} inválido para tipo de lote`;
      selTipo.value = selTipo.options[tipoIndex].value; S.fireChange(selTipo);

      (await campo('frmDialog:numeroLote:numeroLote', `${f}:numeroLote:numeroLote`)).value = lote;
      (await campo('frmDialog:qtdMacho:qtdMacho', `${f}:qtdMacho:qtdMacho`)).value = quantMachos;
      (await campo('frmDialog:pesoMM_hinput', `${f}:pesoMM_hinput`)).value = pesoMachos;
      (await campo('frmDialog:qtdFemea:qtdFemea', `${f}:qtdFemea:qtdFemea`)).value = quantFemeas;
      (await campo('frmDialog:pesoMF_hinput', `${f}:pesoMF_hinput`)).value = pesoFemeas;

      await S.pf({
        s: await botao(f, ':commandDialogAdd_', 'j_idt688:frmDialog:j_idt706:j_idt707:commandDialogAdd_j_idt707'),
        p: f,
        u: `${f} ${f}:msgDialog @(.dlgInserirLoteSelector)`
      }, "addTipoGta");
      await PF('dlgInserirLote').hide();

      const mensagem = await getMensagemErro(cfg().pfFlushTimeout);
      if (msgErro(mensagem)) {
        return `Erro: [INCLUIR_TIPO_GTA] ${mensagem}`;
      }
      return "OK: Tipo de GTA incluído.";
    } catch (err) {
      console.error("incluirTipoGta:EXCEPTION", err);
      return `Erro: [EXC_INCLUIR_TIPO_GTA] ${err && (err.message || err.stack) || 'erro desconhecido'}`;
    }
  };

  window.incluirDiagnostico = async function incluirDiagnostico(indexTipo, diagnostico, quantidade) {
    try {
      await S.pf({
        s: 'formAlterarMapaAbate:dtbLoteAbate:' + indexTipo + ':commandAdd_inserirDoenca',
        u: '@(.frmDialogQuerySelector) @(.msgDialogQuerySelector) @(.dlgVincularDoencaSelector) @(.frmDialogInsertSelector) @(.msgDialogInsertSelector) @(.dlgIncluirPartesAfetadasSelector)'
      }, "abrirVincularDoenca");
      await PF('dlgVincularDoenca').show();

      const inputDiagnostico = await campo('frmDialog:nomeDiagnostico:nomeDiagnostico', 'j_idt709:frmDialog:nomeDiagnostico:nomeDiagnostico');
      const f = S.formOf(inputDiagnostico);
      inputDiagnostico.value = diagnostico;

      await S.pf({
        s: await botao(f, ':commandDialogQuery_', 'j_idt709:frmDialog:j_idt712:j_idt713:commandDialogQuery_j_idt713'),
        p: f,
        u: `${f} ${f}:msgDialog`
      }, "queryDiagnostico");

      await selectRadio('ui-radiobutton-box');

      const inputQuantidade = await campo('frmDialog:quantidadeAnimaisAcometidos:quantidadeAnimaisAcometidos', `${f}:quantidadeAnimaisAcometidos:quantidadeAnimaisAcometidos`);
      inputQuantidade.value = quantidade;

      await S.pf({
        s: await botao(f, ':commandDialogAdd_', 'j_idt709:frmDialog:j_idt720:j_idt721:commandDialogAdd_j_idt721'),
        p: f,
        u: `${f} ${f}:msgDialog @(.dlgVincularDoencaSelector)`
      }, "addDiagnostico");
      await PF('dlgVincularDoenca').hide();

      const mensagem = await getMensagemErro(cfg().pfFlushTimeout);
      if (msgErro(mensagem)) {
        return `Erro: [INCLUIR_DIAG] ${mensagem}`;
      }
      return "OK: Diagnóstico incluído.";
    } catch (err) {
      console.error("incluirDiagnostico:EXCEPTION", err);
      return `Erro: [EXC_INCLUIR_DIAG] ${err && (err.message || err.stack) || 'erro desconhecido'}`;
    }
  };

  window.incluirParte = async function incluirParte(indexDiagnostico, indexParte, indexDestino, quantidade) {
    try {
      await S.pf({
        s: 'formAlterarMapaAbate:dtbDoenca:' + indexDiagnostico + ':commandAdd_inserirParteAfetada',
        u: '@(.frmDialogInsertSelector) @(.msgDialogInsertSelector) @(.dialogInsertSelector)'
      }, "abrirIncluirParte");
      await PF('dlgIncluirPartesAfetadas').show();

      const parte = await campo('frmDialog:parteAnimalAfetada:parteAnimalAfetada_input', 'j_idt723:frmDialog:parteAnimalAfetada:parteAnimalAfetada_input');
      const f = S.formOf(parte);
      if (!parte.options[indexParte]) return `Erro: [PARTE_INDEX] Índice ${indexParte} inválido`;
      parte.value = parte.options[indexParte].value; S.fireChange(parte);

      const inputQuantidade = await campo('frmDialog:numeroPartesAfetadas:numeroPartesAfetadas', `${f}:numeroPartesAfetadas:numeroPartesAfetadas`);
      inputQuantidade.value = quantidade;

      const destino = await campo('frmDialog:cbbDestino:cbbDestino_input', `${f}:cbbDestino:cbbDestino_input`);
      if (!destino.options[indexDestino]) return `Erro: [DESTINO_INDEX] Índice ${indexDestino} inválido`;
      destino.value = destino.options[indexDestino].value; S.fireChange(destino);

      await S.pf({
        s: await botao(f, ':commandDialogAdd_', 'j_idt723:frmDialog:j_idt729:j_idt730:commandDialogAdd_j_idt730'),
        p: f,
        u: `${f} ${f}:msgDialog @(.dlgIncluirPartesAfetadasSelector)`
      }, "addParte");
      await PF('dlgIncluirPartesAfetadas').hide();

      const mensagem = await getMensagemErro(cfg().pfFlushTimeout);
      if (msgErro(mensagem)) {
        return `Erro: [INCLUIR_PARTE] ${mensagem}`;
      }
      return "OK: Parte afetada incluída.";
    } catch (err) {
      console.error("incluirParte:EXCEPTION", err);
      return `Erro: [EXC_INCLUIR_PARTE] ${err && (err.message || err.stack) || 'erro desconhecido'}`;
    }
  };

  // passarPaginaDiagnostico vem do core.js

  // =======================
  // Registro: finalizar
  // =======================
  const FIN_LEGACY = 'formAlterarMapaAbate:j_idt457:j_idt458:commandUpdate_j_idt458';
  const finalizarSpec = { id: FIN_LEGACY, css: '[id^="formAlterarMapaAbate:"][id*=":commandUpdate_"]' };

  window.finalizarRegistro = async function finalizarRegistro() {
    try {
      const finId = await S.srcId(finalizarSpec);
      await S.pf({ s: finId, u: 'outContent mensagensValidacao' }, 'finalizarRegistro');

      // flush/settle com SIGSIF_CFG
      await S.ajaxIdle(cfg().pfFlushTimeout || 1500);
      await onceDelay(cfg().settleDelay || 80);

      const msg = await getMensagemErro(cfg().pfFlushTimeout);
      const erroUi = S.uiErrors();
      if (msgErro(msg) || erroUi) {
        return `Erro: [FINALIZAR] ${erroUi || msg}`;
      }

      // Se o botão continuar habilitado, um retry simples
      try {
        const btn = S.findEl(finalizarSpec);
        if (btn && !btn.disabled) {
          await S.pf({ s: btn.id, u: 'outContent mensagensValidacao' }, 'finalizarRegistro-retry');
          const msg2 = await getMensagemErro(cfg().pfFlushTimeout);
          if (msgErro(msg2)) {
            return `Erro: [FINALIZAR-RETRY] ${msg2}`;
          }
        }
      } catch (_) {}

      return 'OK: Registro finalizado.';
    } catch (err) {
      console.error('finalizarRegistro:EXCEPTION', err);
      return `Erro: [EXC_FINALIZAR] ${err && (err.message || err.stack) || 'erro desconhecido'}`;
    }
  };

  // =======================
  // Registro: localizar/criar/alterar ativo
  // =======================
  window.findRegistro = async function findRegistro(data) {
    try {
      const pfTO = cfg().pfFlushTimeout || 1500;
      const settle = cfg().settleDelay || 80;

      // 0) Tela de consulta: setar DATA
      const data_busca = await waitForElement("formConsultarMapaAbate:dataAbate:dataAbate_input");
      data_busca.value = data; S.fireChange(data_busca);

      // 1) Ajustar ABATE = "S"
      try {
        const abateCbo = document.getElementById("formConsultarMapaAbate:cbbAbate:cbbAbate_input");
        if (abateCbo) { abateCbo.value = "S"; S.fireChange(abateCbo); }
      } catch (_) {}

      // 1.1) (Opcional) Situação = Aberto (se existir)
      try {
        const sitInput = document.querySelector('[id^="formConsultarMapaAbate:"][id*="situacao"][id$="_input"]');
        if (sitInput) { sitInput.value = "Aberto"; S.fireChange(sitInput); }
      } catch (_) {}

      const queryId = await S.srcId({
        id: 'formConsultarMapaAbate:j_idt80:mapaAbate:commandQuery_mapaAbate',
        css: '[id^="formConsultarMapaAbate:"][id$=":mapaAbate:commandQuery_mapaAbate"]'
      });

      async function doQuery() {
        await S.pf({ s: queryId, p: 'formConsultarMapaAbate', u: 'formConsultarMapaAbate mensagensValidacao' }, "queryMapaAbate");
        await S.ajaxIdle(pfTO);
        await onceDelay(settle);
        try { await backFirstPage(); } catch (_) {}
      }

      let msg = null;
      for (let t = 0; t < 3; t++) {
        await doQuery();
        msg = await getMensagemErro(pfTO);
        if (msg && /erro/i.test(msg)) {
          return `Erro: [QUERY_ABATE] ${msg}`;
        }
        if (!msg || !/nenhum registro encontrado/i.test(msg)) {
          return msg || "OK";
        }
        await onceDelay(Math.max(120, settle));
      }
      return msg || "Nenhum registro encontrado.";
    } catch (err) {
      console.error("findRegistro:EXCEPTION", err);
      return `Erro: [EXC_FIND_REGISTRO] ${err && (err.message || err.stack) || 'erro desconhecido'}`;
    }
  };

  // *** CRIAÇÃO: fluxo com onco, flush/settle pós-insert ***
  window.criarNovoRegistroAbate = async function criarNovoRegistroAbate(data, nEstabelecimento = "167") {
    try {
      // 1) Entrar em modo inclusão
      const prepId = await S.srcId({
        id: 'formConsultarMapaAbate:j_idt80:j_idt83:commandPrepareInsert_j_idt83',
        css: '[id^="formConsultarMapaAbate:"][id*=":commandPrepareInsert_"]'
      });
      S.pf({ s: prepId, p: 'formConsultarMapaAbate', u: 'outContent mensagensValidacao' }, "prepareInsert").catch(() => {});

      // 2) Preencher dados na tela de inclusão (sem fireChange)
      const data_abate = await waitForElement('formIncluirMapaAbate:dataAbate:dataAbate_input');
      data_abate.value = data;
      const abate = await waitForElement('formIncluirMapaAbate:cbbAbate:cbbAbate_input');
      abate.value = "S";

      // 3) Pesquisar/selecionar estabelecimento (dialog)
      await S.pf({
        s: 'formIncluirMapaAbate:numeroRegistro:commandSearch_numeroRegistro',
        u: 'mensagensValidacao @(.frmDialogQuerySelector) @(.msgDialogQuerySelector)'
      }, "pesquisarRegistro");
      await PF('dlgPesquisarRegistro').show();

      const sif = await campo('frmDialog:numeroRegistro:numeroRegistro', 'j_idt639:frmDialog:numeroRegistro:numeroRegistro');
      sif.value = nEstabelecimento;
      const fReg = S.formOf(sif);

      await S.pf({
        s: await botao(fReg, ':commandDialogQuery_', 'j_idt639:frmDialog:j_idt646:j_idt647:commandDialogQuery_j_idt647'),
        p: fReg,
        u: `${fReg} ${fReg}:msgDialog`
      }, "queryRegistro");
      try { await backFirstPage(); } catch (_) {}
      await selectRadio('ui-radiobutton-box');

      await S.pf({
        s: await botao(fReg, ':commandDialogBind_', 'j_idt639:frmDialog:j_idt661:j_idt662:commandDialogBind_j_idt662'),
        u: `${fReg}:msgDialog @(.dlgPesquisarRegistroSelector)`
      }, "bindRegistro");
      try { await PF('dlgPesquisarRegistro').hide(); } catch (_) {}

      // 4) Insert
      await S.pf({
        s: await S.srcId({
          id: 'formIncluirMapaAbate:j_idt358:j_idt359:commandInsert_j_idt359',
          css: '[id^="formIncluirMapaAbate:"][id*=":commandInsert_"]'
        }),
        u: 'outContent mensagensValidacao'
      }, "insertRegistro");

      // 5) Flush/settle pós-insert
      await S.ajaxIdle(cfg().pfFlushTimeout || 1500);
      await onceDelay(cfg().settleDelay || 80);

      // 6) Erros de validação (texto com "erro" OU mensagem marcada como erro pelo portal)
      const mensagem = await getMensagemErro(cfg().pfFlushTimeout || 1500);
      const erroUi = S.uiErrors();
      if (msgErro(mensagem) || erroUi) {
        return `Erro: [INSERT_REGISTRO] ${erroUi || mensagem}`;
      }
      return "OK: Registro criado.";
    } catch (error) {
      return `Erro: [EXC_INSERT_REGISTRO] ${error && (error.message || error.stack) || 'erro desconhecido'}`;
    }
  };

  window.selecionarRegistroAtivo = async function selecionarRegistroAtivo() {
    const tabela = Array.from(document.querySelectorAll('tbody[id]'))
      .find(el => el.id.includes('datatable') && el.classList.contains('ui-datatable-data'));
    if (tabela) {
      const linhas = tabela.querySelectorAll('tr');
      for (const linha of linhas) {
        const dataRk = linha.getAttribute('data-rk');
        if (dataRk && !dataRk.includes('excluido=S')) {
          const radio = linha.querySelector('.ui-radiobutton-box');
          if (radio) { try { radio.click(); return; } catch (_) { /* tenta próximas */ } }
        }
      }
    }
    throw new Error(`Nenhum registro não excluído encontrado.`);
  };

  window.alterarRegistroAtivo = async function alterarRegistroAtivo() {
    try {
      await selecionarRegistroAtivo();
      await S.pf({
        s: await S.srcId({
          id: 'formConsultarMapaAbate:j_idt115:j_idt118:commandPrepareUpdate_j_idt118',
          css: '[id^="formConsultarMapaAbate:"][id*=":commandPrepareUpdate_"]'
        }),
        p: 'formConsultarMapaAbate',
        u: 'outContent mensagensValidacao'
      }, "prepareUpdate");
      return "OK: Registro ativo alterado.";
    } catch (err) {
      console.error("alterarRegistroAtivo:EXCEPTION", err);
      return `Erro: [EXC_ALTERAR_ATIVO] ${err && (err.message || err.stack) || 'erro desconhecido'}`;
    }
  };

  window.excluirTodosLotes = async function excluirTodosLotes() {
    try {
      let botoes = document.querySelectorAll('button[id^="formAlterarMapaAbate:dtbSaldos"][id$="commandEventRemove_Lote"]');
      while (botoes.length > 0) {
        const b = botoes[0];
        const id = b.id;
        dbg("excluirLote", id);
        PrimeFaces.ab({ s: id, u: "@(.dlgIncluirPartesAfetadasSelector) @(.dlgVincularDoencaSelector) @(.dlgInserirLoteSelector)" });
        await onceDelay(300);
        botoes = document.querySelectorAll('button[id^="formAlterarMapaAbate:dtbSaldos"][id$="commandEventRemove_Lote"]');
      }
      await finalizarRegistro();
      return "OK: Todos os lotes excluídos e registro finalizado.";
    } catch (e) {
      console.error("excluirTodosLotes:EXCEPTION", e);
      return `Erro: [EXC_EXCLUIR_TODOS] ${e && (e.message || e.stack) || 'erro desconhecido'}`;
    }
  };
})();
