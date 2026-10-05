# app/runner/web/navegador.py
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchWindowException,
    InvalidSessionIdException,
    UnexpectedAlertPresentException,
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
)

from time import sleep as wait
import os
import logging

try:
    # opcional: se quiser controle automático de driver
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except Exception:
    HAS_WDM = False

# Exceções internas do app
from app.runner.exceptions import FalhaNoJavascript
from app.logic.js_literal import js_str

# --- Constantes de ambiente/rota/UI ---
LOGIN_URL = r"https://sistemas.agricultura.gov.br/segaut/login.jsp?sgAplicacaoRedirecionada=%2Fpga_sigsif"
ABATE_HOME_URL = "https://sistemas.agricultura.gov.br/pga_sigsif/pages/view/sigsif/mapaabate/indexMapaAbate.xhtml"
COMERCIALIZACAO_HOME_URL = "https://sistemas.agricultura.gov.br/pga_sigsif/pages/view/sigsif/mapacomercializacao/indexMapaComercializacao.xhtml"
ABATE_URL_HINT = "/pga_sigsif/"
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
# comandos que tipicamente rendem mensagens na tela
UI_TAP_COMMANDS = ("finalizarRegistro", "incluirGta", "incluirTipoGta", "incluirDiagnostico", "incluirParte", "findRegistro")


class Navegador(webdriver.Chrome):
    """
    Navegador do portal PGA-SIGSIF. Esta classe e o perfil ABATE; outros modulos
    (ex.: NavegadorComercializacao) herdam e trocam apenas os atributos abaixo.
    """

    HOME_URL = ABATE_HOME_URL
    URL_HINT = ABATE_URL_HINT
    # trecho do id do formulario de consulta, usado p/ detectar a tela/iframe do modulo
    FORM_MARKER = "formConsultarMapaAbate"
    # script do modulo, injetado depois do core.js
    MODULE_SCRIPT = "scripts.js"
    # funcoes que precisam existir no frame para considerar os helpers carregados
    HELPER_FUNCS = ("collectUiMessages", "getMensagemErro", "getMensagemErroEstatica")
    UI_TAP_COMMANDS = UI_TAP_COMMANDS
    # True: so repete automaticamente erros transitorios (timeout/helper ausente). Evita
    # relancar comandos nao idempotentes (ex.: incluir produto) depois de um erro de negocio.
    RETRY_ONLY_TRANSIENT = False
    # True: ao receber "Erro..." do JS, le as mensagens de UI e as usa no lugar do erro.
    # False: preserva o erro codificado do JS (ex.: [REGISTRO_COM_DADOS]).
    TAP_ON_ERROR = True
    # tentativas do worker por comando (por cima das tentativas internas do executar_comando)
    WORKER_ATTEMPTS = 2

    def __init__(self, mode="HIDE", chromedriver_path: str | None = None,
                 *, speed_profile: str = "balanced", tap_ui: str = "auto",
                 module_script: str | None = None):
        self.mode = mode
        # permite sobrescrever o script do modulo (SCRIPTS_JS_PATH); o core.js sempre vai antes
        self._module_script = module_script or os.path.join(SCRIPTS_DIR, self.MODULE_SCRIPT)
        self.speed_profile = (speed_profile or "balanced").lower()
        self.tap_ui = (tap_ui or "auto").lower()  # "auto" | "always" | "never"

        self.options = webdriver.ChromeOptions()
        self.options.add_argument("--log-level=3")
        self.options.add_experimental_option("excludeSwitches", ["enable-logging"])
        if self.mode == "HIDE":
            self.options.add_argument("--headless=new")

        # Habilita o pipe de log do console do navegador via CDP (independente
        # do --log-level/excludeSwitches acima, que só afetam o log nativo do
        # Chrome escrito em disco). Sem isso, self.get_log("browser") sempre
        # retorna vazio e qualquer exceção JS não tratada no site/scripts
        # injetados nunca chega até nós.
        self.options.set_capability("goog:loggingPrefs", {"browser": "SEVERE"})

        # Resolve o driver, em ordem: caminho informado -> CHROMEDRIVER_PATH -> webdriver_manager
        # -> driver embutido em runner/drivers -> Selenium Manager (baixa o driver certo sozinho).
        # Caminho que nao existe NAO derruba mais a execucao: cai para o proximo.
        service = self._resolver_service(chromedriver_path)
        super().__init__(service=service, options=self.options)
        self.set_script_timeout(120)

        # Perfis de velocidade
        sp = self.speed_profile
        if sp == "ultra":
            self.pf_flush_timeout = 500
            self.retry_delay      = 0.10
            self.dom_settle_delay = 0.02
        elif sp == "fast":
            self.pf_flush_timeout = 800
            self.retry_delay      = 0.25
            self.dom_settle_delay = 0.05
        elif sp == "safe":
            self.pf_flush_timeout = 2500
            self.retry_delay      = 0.80
            self.dom_settle_delay = 0.20
        else:  # balanced
            self.pf_flush_timeout = 1500
            self.retry_delay      = 0.60
            self.dom_settle_delay = 0.12

        # Silenciar DEBUG do stack Selenium/urllib3 (sem afetar seus logs)
        for name in (
            "selenium.webdriver.remote.remote_connection",
            "urllib3.connectionpool",
            "urllib3",
            "selenium",
        ):
            logging.getLogger(name).setLevel(logging.WARNING)

        # Guarda o handle principal da janela
        try:
            self.main_handle = self.current_window_handle
        except Exception:
            self.main_handle = None

    @staticmethod
    def _resolver_service(chromedriver_path: str | None) -> Service:
        log = logging.getLogger(__name__)
        base_dir = os.path.join(os.path.dirname(__file__), "..", "drivers")
        candidatos = [
            chromedriver_path,
            os.getenv("CHROMEDRIVER_PATH"),
            os.path.join(base_dir, "chromedriver"),
            os.path.join(base_dir, "chromedriver.exe"),
        ]
        for c in filter(None, candidatos):
            c = os.path.abspath(c)
            if os.path.isfile(c) and (os.access(c, os.X_OK) or os.name == "nt"):
                return Service(executable_path=c)
            log.warning("Chromedriver nao utilizavel em %s; tentando a proxima opcao.", c)

        if HAS_WDM:
            try:
                return Service(executable_path=ChromeDriverManager().install())
            except Exception as e:
                log.warning("webdriver_manager falhou (%s); usando Selenium Manager.", e)
        # Service() sem caminho => Selenium Manager resolve/baixa o driver compativel
        return Service()

    def _xpath_marker(self) -> str:
        return f'//*[contains(@id,"{self.FORM_MARKER}")]'

    def _scripts_padrao(self) -> tuple:
        """core.js (helpers/resolvedores comuns) + script do modulo."""
        return (os.path.join(SCRIPTS_DIR, "core.js"), self._module_script)

    # --- Logging enxuto ---
    def _log(self, level: str, msg: str):
        print(f"[{level}] {msg}")

    def injetar_scripts(self, *caminhos_js):
        """
        Injeta um ou mais arquivos JS. Se nenhum caminho for informado,
        injeta core.js + o script do modulo (scripts.js / comercializacao.js).
        """
        if not caminhos_js:
            caminhos_js = self._scripts_padrao()

        for caminho in caminhos_js:
            caminho = os.path.abspath(caminho)
            with open(caminho, "r", encoding="utf-8") as f:
                script = f.read()
            try:
                self.execute_script(script)
            except Exception as e:
                print(f"Erro ao injetar script {caminho}: {e}")

    # --- Guard de rota/URL/iframe ---
    def _ensure_on_abate_home(self, *, timeout: int = 15) -> tuple[bool, str | None]:
        """
        Garante que estamos na home do Mapa de Abate por:
         - URL (hint) e marcador de UI;
         - troca de janela/aba;
         - entrada em iframe quando necessário.
        """
        waitr = WebDriverWait(self, timeout, poll_frequency=0.25)
        try:
            # 0) troca para última janela se abriu nova
            try:
                handles = self.window_handles
                if len(handles) > 1 and self.current_window_handle != handles[-1]:
                    self.switch_to.window(handles[-1])
                    self.main_handle = handles[-1]
            except Exception:
                pass

            # 1) se não está na URL de abate, navega
            url = (self.current_url or "").lower()
            if self.URL_HINT not in url:
                self.get(self.HOME_URL)

            # 2) espera URL conter hint
            try:
                waitr.until(lambda d: self.URL_HINT in (d.current_url or "").lower())
            except TimeoutException:
                self.refresh()
                waitr.until(lambda d: self.URL_HINT in (d.current_url or "").lower())

            # 3) entra no iframe, se existir
            self.switch_to.default_content()
            frames = self.find_elements(By.TAG_NAME, "iframe")
            for fr in frames:
                try:
                    self.switch_to.frame(fr)
                    if self.find_elements(By.XPATH, self._xpath_marker()) or \
                       self.find_elements(By.CSS_SELECTOR, "#outContent"):
                        break
                    self.switch_to.default_content()
                except Exception:
                    self.switch_to.default_content()

            # 4) presença de marcadores
            waitr.until(EC.any_of(
                EC.presence_of_element_located((By.XPATH, self._xpath_marker())),
                EC.presence_of_element_located((By.CSS_SELECTOR, '#outContent')),
            ))
            return True, None
        except Exception as e:
            try:
                self.switch_to.default_content()
            except Exception:
                pass
            return False, f"Não consegui posicionar na Home do Abate: {e}"

    def _enter_abate_frame(self, timeout=5) -> bool:
        """
        Tenta encontrar o iframe onde está o conteúdo (ex.: outContent/formConsultarMapaAbate)
        e alterna para ele. Retorna True se alternou, False se já estava ou não há iframe.
        """
        try:
            if self.find_elements(By.ID, "outContent") or self.find_elements(
                By.XPATH, self._xpath_marker()
            ):
                return False
            self.switch_to.default_content()
            frames = self.find_elements(By.TAG_NAME, "iframe")
            for fr in frames:
                self.switch_to.frame(fr)
                if self.find_elements(By.ID, "outContent") or self.find_elements(
                    By.XPATH, self._xpath_marker()
                ):
                    return True
                self.switch_to.default_content()
            return False
        except Exception:
            try:
                self.switch_to.default_content()
            except Exception:
                pass
            return False

    def _pf_flush(self, timeout_ms=None):
        """
        Aguarda a fila Ajax do PrimeFaces esvaziar. Evita ler mensagens cedo demais.
        """
        try:
            to = (timeout_ms if timeout_ms is not None else self.pf_flush_timeout) / 1000.0
            WebDriverWait(self, to, poll_frequency=0.1).until(
                lambda d: d.execute_script(
                    """
                    try {
                      return !!(window.PrimeFaces && PrimeFaces.ajax && PrimeFaces.ajax.Queue && PrimeFaces.ajax.Queue.isEmpty());
                    } catch(e) { return true; }
                """
                )
                is True
            )
        except Exception:
            pass


    def _ensure_helpers(self, scripts_js_path: str | None = None):
        """
        Se os helpers não existem no frame atual, reinjeta scripts.js.
        """
        try:
            self._enter_abate_frame()
            checks = " && ".join(f"window.{fn}" for fn in ("SIGSIF",) + tuple(self.HELPER_FUNCS))
            has_helper = self.execute_script(f"return !!({checks});")
            if not has_helper:
                self.injetar_scripts(*( [scripts_js_path] if scripts_js_path else () ))
        except InvalidSessionIdException as e:
            # Sessão já encerrada: não loga WARN; propaga para quem chamou decidir
            raise
        except Exception as e:
            # Converte mensagens típicas de sessão inválida em exceção explícita
            if "invalid session id" in str(e).lower():
                raise InvalidSessionIdException(str(e))
            print(f"[WARN] Falha ao garantir helpers: {e}")

    def injetar_scritps_seguro(self, *caminhos_js):
        try:
            self.injetar_scripts(*caminhos_js)
        except Exception as e:
            print(f"[Navegador] Aviso: falha ao injetar scripts (seguindo mesmo assim): {e}")

    def _check_logged_markers(self, timeout=15) -> tuple[bool, str | None]:
        """Retorna (ok, erro) checando múltiplos sinais de login."""
        try:
            WebDriverWait(self, timeout, poll_frequency=0.5).until(
                EC.any_of(
                    EC.presence_of_element_located(
                        (By.XPATH, self._xpath_marker())
                    ),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "#outContent")),
                )
            )
            return True, None
        except Exception:
            try:
                has_login_btn = len(self.find_elements(By.ID, "loginButton")) > 0
            except Exception:
                has_login_btn = False
            url = (self.current_url or "").lower()
            if "/pga_sigsif/" in url and not has_login_btn:
                return True, None
            return False, f"Marcadores de login não encontrados (url={self.current_url})"

    def _exec_js_async(self, comando: str) -> str:
        """
        Executa o comando JS (Promise) e retorna a string de resultado.
        NÃO faz fallback/retentativa. Uso interno.
        """
        res = self.execute_async_script(
            f"""
            const callback = arguments[arguments.length - 1];
            try {{
              const p = window.{comando};
              (p && typeof p.then === 'function' ? p : Promise.resolve(p))
                .then(v => callback(v))
                .catch(err => callback("Erro: " + (err && (err.message || err) || "desconhecido")));
            }} catch (e) {{
              callback("Erro: " + (e && (e.message || e) || "desconhecido"));
            }}
        """
        )
        if comando.strip().startswith("finalizarRegistro("):
            wait(max(0.4, self.dom_settle_delay))
        return res

    # Garantia de janela/iframe/helpers ativos
    def _ensure_window(self):
        """
        Se a janela atual foi fechada ou perdeu foco, reanexa à última válida.
        Reentra em iframe e garante helpers.
        """
        try:
            _ = self.current_url
            return
        except NoSuchWindowException:
            pass
        except Exception:
            pass
        handles = []
        try:
            handles = self.window_handles
        except Exception:
            handles = []
        if not handles:
            try:
                self.get(self.HOME_URL)
            except Exception:
                pass
        else:
            try:
                target = self.main_handle if self.main_handle in handles else handles[-1]
                self.switch_to.window(target)
                self.main_handle = target
            except Exception:
                try:
                    self.switch_to.window(handles[-1])
                    self.main_handle = handles[-1]
                except Exception:
                    pass
        try:
            self._ensure_on_abate_home(timeout=10)
            self._ensure_helpers()
        except Exception:
            pass
    # === Camada ALTO NÍVEL (robusta) ===
    
    def executar_comando(self, comando: str, ctx: str = "", tentativas: int = 2) -> str:
        """
        Executa o comando JS com:
        - Frame/Helpers garantidos;
        - Flush do PrimeFaces apenas quando necessário;
        - Coleta de mensagens de UI condicionada (auto/always/never);
        - Retry só para erros não determinísticos;
        - Screenshot quando houver erro.
        """
        def _deu_erro(msg: str) -> bool:
            return isinstance(msg, str) and msg.strip().lower().startswith("erro")

        def _tem_erro_ui(ui: str | None) -> bool:
            if not ui:
                return False
            t = ui.strip().lower()
            if t == "nenhum registro encontrado.":
                # não é erro; também não vamos logar
                return False
            # qualquer outra msg de UI tratamos como erro terminal
            return True

        def _aviso_negocio(ui: str | None) -> bool:
            return bool(ui and ui.strip().lower() == "nenhum registro encontrado.")

        def _should_tap_ui(cmd_name: str, last_msg: str | None) -> bool:
            if self.tap_ui == "always":
                return True
            if self.tap_ui == "never":
                return False
            if _deu_erro(last_msg or "") and self.TAP_ON_ERROR:
                return True
            return cmd_name in self.UI_TAP_COMMANDS

        ctx = ctx or (comando.split("(", 1)[0] if "(" in comando else comando) or "cmd"
        cmd_name = (comando.split("(", 1)[0] if "(" in comando else comando).strip()
        last = None

        for attempt in range(1, max(1, int(tentativas)) + 1):
            try:
                try:
                    self._ensure_window()
                    self._enter_abate_frame()
                    self._ensure_helpers()
                except InvalidSessionIdException:
                    # Browser fechado manualmente: devolve código especial
                    return "Erro: [SESSION_CLOSED]"
                except Exception:
                    pass

                self._log("CMD", f"{ctx} → {comando}")

                last = self._exec_js_async(comando)

                msg_ui = None
                if _should_tap_ui(cmd_name, last):
                    try:
                        self._pf_flush()
                        wait(self.dom_settle_delay)
                        self._enter_abate_frame()
                        self._ensure_helpers()
                        msg_ui = self.execute_script(
                            "return (window.collectUiMessages && window.collectUiMessages()) || null;"
                        )
                    except InvalidSessionIdException:
                        return "Erro: [SESSION_CLOSED]"
                    except Exception:
                        msg_ui = None

                # Se houver UI ≠ “Nenhum registro encontrado.”, trate como erro
                if msg_ui:
                    if msg_ui.strip().lower() == "nenhum registro encontrado.":
                        # não loga nem erra; devolve aviso para quem quiser usar
                        return msg_ui
                    # Qualquer outra mensagem de UI é erro terminal
                    try:
                        self.capturar_tela(f"uierr_{ctx}")
                    except Exception:
                        pass
                    return self._com_console(f"Erro: [UI] {msg_ui}")

                # Erro por retorno textual
                if _deu_erro(last):
                    if self.RETRY_ONLY_TRANSIENT and not self._erro_transitorio(last):
                        self._log("ERR", f"{ctx}: erro nao transitorio, sem nova tentativa: {last}")
                        return self._com_console(last)
                    self._log("WARN", f"{ctx} tent.{attempt}/{tentativas} falhou: {last}")
                    # Tenta novamente apenas se não for determinístico
                    wait(self.retry_delay)
                    continue

                # Sucesso
                self._log("OK", f"{ctx}: {last}")
                return last

            except InvalidSessionIdException:
                return "Erro: [SESSION_CLOSED]"
            except NoSuchWindowException:
                self._log("WARN", f"{ctx}: janela/aba perdida. Tentando recuperar…")
                try:
                    self._ensure_window()
                    continue
                except Exception:
                    return "Erro: [SESSION_CLOSED]"
            except Exception as e:
                self._log("ERR", f"{ctx}: exceção {e}")
                wait(self.retry_delay)

        try:
            #self.capturar_tela(ctx)
            pass
        except Exception:
            pass
        return self._com_console(last or f"Erro: Falha ao executar {ctx}")

    def clickar(self, xpath):
        try:
            WebDriverWait(self, 10, poll_frequency=0.5).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            ).click()
        except (TimeoutException, StaleElementReferenceException, ElementClickInterceptedException):
            WebDriverWait(self, 10, poll_frequency=0.5).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            ).click()

    def escrever(self, xpath, texto):
        try:
            WebDriverWait(self, 10, poll_frequency=0.5).until(
                EC.visibility_of_element_located((By.XPATH, xpath))
            ).send_keys(str(texto))
        except (TimeoutException, StaleElementReferenceException):
            self.clickar(xpath)
            WebDriverWait(self, 10, poll_frequency=0.5).until(
                EC.visibility_of_element_located((By.XPATH, xpath))
            ).send_keys(str(texto))

    # --- Captura de erros JS reais do console do navegador (CDP) ---
    def _collect_console_errors(self, level: str = "SEVERE", limit: int = 10) -> list[str]:
        """
        Lê o log 'browser' via CDP (habilitado em __init__ com goog:loggingPrefs).
        Retorna as últimas `limit` entradas do nível pedido, já formatadas.
        Não levanta exceção: se a sessão já caiu ou o driver não suportar,
        retorna lista vazia silenciosamente.
        """
        try:
            entries = self.get_log("browser")
        except Exception:
            return []
        out = []
        for e in entries:
            lvl = e.get("level")
            if level and lvl != level:
                continue
            msg = (e.get("message") or "").strip()
            if not msg:
                continue
            out.append(f"{lvl}: {msg}")
        return out[-limit:]

    _MARCAS_TRANSITORIAS = (
        "[timeout]", "[js_not_ready]", "is not defined", "is not a function",
        "stale element", "elemento nao encontrado", "elemento não encontrado",
    )

    @classmethod
    def _erro_transitorio(cls, msg: str | None) -> bool:
        t = (msg or "").lower()
        return any(m in t for m in cls._MARCAS_TRANSITORIAS)

    def _com_console(self, msg: str) -> str:
        """Anexa erros de console coletados (se houver) a uma mensagem de erro já formada."""
        try:
            errs = self._collect_console_errors()
        except Exception:
            errs = []
        if errs:
            return msg + "\n[JS-CONSOLE] " + " | ".join(errs)
        return msg

    def capturar_tela(self, nome_erro="erro"):
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_arquivo = f"screenshot_{nome_erro}_{timestamp}.png"
        caminho = os.path.join(os.getcwd(), nome_arquivo)
        self.save_screenshot(caminho)
        print(f"[!] Screenshot salva em: {caminho}")

    # === Fluxos utilitários do seu sistema ===
    def _check_url(self, expected_url: str, *, timeout: int = 20) -> bool:
        """URL igual ao esperado ou, na falta, mesma pagina (ignora query/;jsessionid)."""
        def base(u: str) -> str:
            return (u or "").split("?", 1)[0].split(";", 1)[0].rstrip("/").lower()

        try:
            WebDriverWait(self, timeout).until(
                lambda d: base(d.current_url) == base(expected_url)
            )
            return True
        except Exception:
            return False

    def authenticate(self, usuario: str, senha: str, *, timeout: int = 20) -> tuple[bool, str | None]:
        """Somente autentica e valida presença da área logada. Não injeta JS e não mexe em registro."""
        try:
            # 1) tela de login
            self.get(LOGIN_URL)
            self.escrever('//*[@id="username"]', usuario)
            self.escrever('//*[@id="password"]', senha)
            self.clickar('//*[@id="loginButton"]')
            wait(1)

            # 2) vá para a home do Mapa de Abate
            self.get(self.HOME_URL)
            if not self._check_url(self.HOME_URL):
                return False, f"URL final não é {self.HOME_URL}, atual: {self.current_url}"

            # 3) checagem robusta
            ok, err = self._check_logged_markers(timeout=timeout)
            if ok:
                # posiciona em rota/iframe corretos
                self._ensure_on_abate_home(timeout=timeout)
                return True, None

            # 4) último fallback: aguarda e checa de novo
            try:
                WebDriverWait(self, 5).until(lambda d: "/pga_sigsif/" in (d.current_url or "").lower())
            except TimeoutException:
                pass

            ok2, err2 = self._check_logged_markers(timeout=5)
            return (True, None) if ok2 else (False, err2)

        except Exception as e:
            return False, str(e)

    def _autenticar_com_retry(self, usuario, senha, tentativas: int = 3):
        """Login instavel (SSO lento/redirect) nao derruba o job na primeira falha."""
        err = None
        for i in range(1, tentativas + 1):
            ok, err = self.authenticate(usuario, senha)
            if ok:
                return
            self._log("WARN", f"login tentativa {i}/{tentativas} falhou: {err}")
            wait(1.5 * i)
        raise RuntimeError(f"Falha ao autenticar: {err or 'desconhecido'}")

    def login(self, data, usuario, senha):
        """
        Fluxo pesado (para EXECUÇÃO): autentica, injeta JS e cria/seleciona o registro da data.
        """
        self.data = data
        self._autenticar_com_retry(usuario, senha)

        # Já logado: injeta JS e segue com seu fluxo
        try:
            self.injetar_scripts()
        except Exception as e:
            print(f"[Navegador] Aviso: falha ao injetar scripts: {e}")
        self.verificar_criacao_registro()

    def _ensure_ok(self, msg: str, ctx: str):
        """Se a função JS retornar 'Erro: ...', faz screenshot e levanta exceção."""
        if isinstance(msg, str) and msg.strip().lower().startswith("erro"):
            raise FalhaNoJavascript(dia=getattr(self, "data", None), erro=msg)

    # Shim: garante frame + helpers e executa
    def _js(self, comando: str) -> str:
        try:
            self._ensure_window()
            self._enter_abate_frame()
            self._ensure_helpers()
        except Exception:
            pass
        return self._exec_js_async(comando)

    def _create_and_select(self):
        """Cria registro para self.data e seleciona como ativo (com navegação + reconsulta e filtros)."""
        # 1) Garante rota correta ANTES de criar
        self._ensure_on_abate_home(timeout=15)

        # 2) Cria (tela de inclusão)
        msg1 = self._js(f"criarNovoRegistroAbate('{self.data}')")
        self._ensure_ok(msg1, "criarNovoRegistroAbate")

        # 3) Volta explicitamente para a Home do Abate (tela de consulta)
        try:
            self.get(self.HOME_URL)
        except Exception:
            pass
        self._ensure_on_abate_home(timeout=15)
        self._ensure_helpers()
        self._pf_flush(timeout_ms=2500)  # flush um pouco maior na primeira reconsulta pós-insert

        # 4) Reconsulta a data com até 5 tentativas (findRegistro ajusta Abate=Sim)
        last_find = None
        for attempt in range(5):
            last_find = self._js(f"findRegistro('{self.data}')")
            if isinstance(last_find, str) and last_find.strip().lower().startswith("erro"):
                self._ensure_ok(last_find, "findRegistro")  # levanta FalhaNoJavascript
            if last_find != "Nenhum registro encontrado.":
                break
            self._ensure_helpers()
            self._pf_flush()
            wait(max(0.25, self.dom_settle_delay))

        if last_find == "Nenhum registro encontrado.":
            raise FalhaNoJavascript(
                dia=getattr(self, "data", None),
                erro=f"Erro: [EXC_FIND_AFTER_INSERT] {last_find or 'consulta não retornou linhas'}"
            )

        # 5) Seleciona/Prepara alteração no registro recém listado
        msg2 = self._js("alterarRegistroAtivo()")
        self._ensure_ok(msg2, "alterarRegistroAtivo")

    def verificar_criacao_registro(self):
        """
        Garante que exista registro utilizável para self.data:
        - Se não houver registro -> cria + seleciona
        - Se houver, tenta selecionar; se não houver linha não-excluída -> cria + seleciona
        """
        try:
            # 0) GARANTE que estamos na rota do Abate antes de buscar
            ok_r, err_r = self._ensure_on_abate_home(timeout=15)
            if not ok_r:
                self._log("WARN", f"Fora da rota do Abate ao buscar data: {err_r}. Forçando navegação.")
                self.get(self.HOME_URL)
                self._ensure_on_abate_home(timeout=15)

            msg = self._js(f"findRegistro('{self.data}')")
            self._ensure_ok(msg, "findRegistro")
            if msg == "Nenhum registro encontrado.":
                # Caminho normal quando nada existe para a data
                self._create_and_select()
                return

            # Já há algo listado -> tenta alterar registro ativo
            try:
                msg_alt = self._js("alterarRegistroAtivo()")
                self._ensure_ok(msg_alt, "alterarRegistroAtivo")
            except FalhaNoJavascript as e:
                text = str(e)
                if "Nenhum registro não excluído encontrado" in text:
                    self._create_and_select()
                else:
                    raise
        except JavascriptException as e:
            raise FalhaNoJavascript(dia=self.data, erro=e)

    def zerarRegistro(self):
        try:
            self._exec_js_async("excluirTodosLotes()")
        except JavascriptException as e:
            raise FalhaNoJavascript(dia=self.data, erro=e)


class NavegadorComercializacao(Navegador):
    """Perfil do Mapa de Comercializacao (mesmo portal/login, outra tela e outros scripts)."""

    HOME_URL = COMERCIALIZACAO_HOME_URL
    FORM_MARKER = "formConsultarMapaComercializacao"
    MODULE_SCRIPT = "comercializacao.js"
    HELPER_FUNCS = (
        "collectUiMessages", "getMensagemErro",
        "findRegistroComercializacao", "incluirProdutoVenda",
    )
    # O JS da comercializacao ja detecta erro de UI por severidade e devolve "Erro: [CODIGO] ...";
    # coletar mensagens de UI por fora so geraria falso positivo com avisos informativos.
    UI_TAP_COMMANDS = ()
    TAP_ON_ERROR = False
    RETRY_ONLY_TRANSIENT = True
    WORKER_ATTEMPTS = 1  # o executar_comando ja trata as repeticoes seguras

    def login(self, periodo_ini: str, periodo_fim: str, usuario: str, senha: str,
              numero_sif: str = "167"):
        """
        Autentica, injeta os scripts e deixa o registro do periodo ABERTO PARA ALTERACAO
        (cria se nao existir). Periodo no formato dd/mm/aaaa.
        """
        self.periodo_ini, self.periodo_fim, self.numero_sif = periodo_ini, periodo_fim, str(numero_sif)
        self.data = f"{periodo_ini} a {periodo_fim}"
        self._autenticar_com_retry(usuario, senha)
        try:
            self.injetar_scripts()
        except Exception as e:
            print(f"[NavegadorComercializacao] Aviso: falha ao injetar scripts: {e}")
        self.verificar_criacao_registro()

    def _find(self) -> str:
        return self._js(f"findRegistroComercializacao({js_str(self.periodo_ini)}, {js_str(self.periodo_fim)})")

    def _create_and_select(self):
        self._ensure_on_abate_home(timeout=15)
        msg = self._js(
            f"criarRegistroComercializacao({js_str(self.periodo_ini)}, {js_str(self.periodo_fim)}, "
            f"{js_str(self.numero_sif)})"
        )
        self._ensure_ok(msg, "criarRegistroComercializacao")
        if isinstance(msg, str) and "[MODO_INCLUIR]" in msg:
            # O portal exige as transacoes antes do insert: seguimos no formulario de inclusao
            # e o finalizarRegistroComercializacao() faz o insert no fim.
            return

        try:
            self.get(self.HOME_URL)
        except Exception:
            pass
        self._ensure_on_abate_home(timeout=15)
        self._ensure_helpers()
        self._pf_flush(timeout_ms=2500)

        last = None
        for _ in range(5):
            last = self._find()
            if isinstance(last, str) and last.strip().lower().startswith("erro"):
                self._ensure_ok(last, "findRegistroComercializacao")
            if last != "Nenhum registro encontrado.":
                break
            self._ensure_helpers()
            self._pf_flush()
            wait(max(0.25, self.dom_settle_delay))
        if last == "Nenhum registro encontrado.":
            raise FalhaNoJavascript(
                dia=self.data, erro="Erro: [EXC_FIND_AFTER_INSERT] consulta nao retornou o registro criado"
            )
        self._ensure_ok(self._js("alterarRegistroAtivoComercializacao()"), "alterarRegistroAtivoComercializacao")

    def verificar_criacao_registro(self):
        try:
            ok_r, err_r = self._ensure_on_abate_home(timeout=15)
            if not ok_r:
                self._log("WARN", f"Fora da rota da Comercializacao: {err_r}. Forcando navegacao.")
                self.get(self.HOME_URL)
                self._ensure_on_abate_home(timeout=15)

            msg = self._find()
            self._ensure_ok(msg, "findRegistroComercializacao")
            if msg == "Nenhum registro encontrado.":
                self._create_and_select()
                return
            try:
                self._ensure_ok(self._js("alterarRegistroAtivoComercializacao()"),
                                "alterarRegistroAtivoComercializacao")
            except FalhaNoJavascript as e:
                if "Nenhum registro não excluído encontrado" in str(e):
                    self._create_and_select()
                else:
                    raise
        except JavascriptException as e:
            raise FalhaNoJavascript(dia=getattr(self, "data", None), erro=e)
