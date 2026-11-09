"""Verificação e atualização automática do chromedriver.

Problema: o driver precisa ter a mesma versão principal do Chrome instalado. O Chrome se atualiza
sozinho, então de tempos em tempos o driver (embutido, em CHROMEDRIVER_PATH ou esquecido no PATH) fica
velho e o Selenium falha com "Unable to obtain driver" / "This version of ChromeDriver only supports...".

Como funciona (baseado no Selenium Manager, que já vem no pacote `selenium`):
  1. testa os drivers conhecidos ABRINDO o Chrome de verdade (headless): é o único teste definitivo de
     compatibilidade, e funciona igual no Windows, Linux e macOS;
  2. se nenhum serve, pede ao Selenium Manager o driver certo para o Chrome instalado (ele baixa e guarda
     em cache; primeiro ignorando drivers soltos no PATH, que são a causa clássica de versão errada);
  3. testa o driver obtido do mesmo jeito e guarda o caminho verificado para o resto do processo.

Roda na inicialização (thread em segundo plano), quando um worker sobe, sob demanda no Admin e, como
último recurso, quando o Chrome falha ao abrir durante um lançamento.

Rede restrita? O Selenium Manager lê estas variáveis (ver `selenium-manager --help`):
  SE_PROXY, SE_DRIVER_MIRROR_URL, SE_CACHE_PATH, SE_OFFLINE, SE_AVOID_STATS.
"""
from __future__ import annotations

import datetime as dt
import glob
import io
import logging
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.request import getproxies

from selenium import webdriver
from selenium.common.exceptions import NoSuchDriverException, SessionNotCreatedException, WebDriverException
from selenium.webdriver.chrome.service import Service

log = logging.getLogger(__name__)

DRIVERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "drivers"))
# Drivers baixados pelo proprio sistema (fallback em Python): drivers/auto/<major>/chromedriver[.exe]
CFT_MARCOS = "https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json"

# Sem isso o Selenium Manager manda estatística de uso para plausible.io (ruído em rede corporativa)
os.environ.setdefault("SE_AVOID_STATS", "true")


class NavegadorIndisponivel(RuntimeError):
    """Chrome/chromedriver não abrem. NÃO é problema de credenciais do MAPA."""


_lock = threading.RLock()
_estado: Dict[str, Any] = {
    "ok": None,                # None = ainda não verificado
    "driver_path": None,       # caminho verificado (None com ok=True => Selenium Manager resolve sozinho)
    "chrome": None, "driver": None, "origem": None,
    "mensagem": "Ainda não verificado.", "verificado_em": None, "tentativas": [],
}


def estado() -> Dict[str, Any]:
    with _lock:
        return {**_estado, "tentativas": list(_estado["tentativas"])}


# ---------------------------------------------------------------------------------------------
# Classificação de erros (para não confundir driver quebrado com credencial inválida)
# ---------------------------------------------------------------------------------------------
_MARCAS_DRIVER = ("unable to obtain driver", "unable to obtain working selenium manager", "only supports chrome",
                  "this version of chromedriver", "cannot find chrome binary", "no chrome binary",
                  "chromedriver", "driver_location", "executable needs to be in path", "session not created")


def eh_erro_de_driver(exc: BaseException) -> bool:
    if isinstance(exc, (NoSuchDriverException, SessionNotCreatedException)):
        return True
    if isinstance(exc, WebDriverException):
        t = str(exc).lower()
        return any(m in t for m in _MARCAS_DRIVER)
    return False


def _resumir(exc: BaseException) -> str:
    """Mensagem curta e legível (sem stacktrace nem links de documentação)."""
    t = str(exc)
    m = re.search(r"only supports Chrome version (\d+)", t)
    if m:
        atual = re.search(r"Current browser version is ([\d.]+)", t)
        return (f"o chromedriver é da versão {m.group(1)} e não serve para o Chrome instalado"
                + (f" ({atual.group(1)})" if atual else ""))
    if "unable to obtain driver" in t.lower():
        return "o Selenium não conseguiu obter um chromedriver (download bloqueado ou Chrome não encontrado)"
    linha = next((l.strip() for l in t.splitlines() if l.strip()), type(exc).__name__)
    linha = re.sub(r"^Message:\s*", "", linha)
    return linha[:240]


def explicar(exc: BaseException) -> str:
    return f"Navegador/driver do Chrome indisponível (não é problema das credenciais): {_resumir(exc)}."


# ---------------------------------------------------------------------------------------------
# Teste definitivo: abrir o Chrome
# ---------------------------------------------------------------------------------------------
def _opcoes() -> "webdriver.ChromeOptions":
    o = webdriver.ChromeOptions()
    o.add_argument("--headless=new")
    o.add_argument("--log-level=3")
    if hasattr(os, "geteuid") and os.geteuid() == 0:     # Chrome como root (Linux/containers) exige
        o.add_argument("--no-sandbox")
    return o


def _abrir_para_teste(driver_path: Optional[str]) -> Tuple[bool, Any]:
    d = None
    try:
        svc = Service(executable_path=driver_path) if driver_path else Service()
        d = webdriver.Chrome(service=svc, options=_opcoes())
        caps = d.capabilities or {}
        return True, {"chrome": caps.get("browserVersion"),
                      "driver": ((caps.get("chrome") or {}).get("chromedriverVersion") or "").split(" ")[0] or None}
    except Exception as e:  # noqa: BLE001 - qualquer falha de abertura vira diagnóstico
        return False, _resumir(e)
    finally:
        try:
            if d is not None:
                d.quit()
        except Exception:
            pass


def _candidatos(explicito: Optional[str]) -> List[Tuple[str, str]]:
    cands = [("caminho informado", explicito), ("CHROMEDRIVER_PATH", os.getenv("CHROMEDRIVER_PATH")),
             ("driver embutido", os.path.join(DRIVERS_DIR, "chromedriver")),
             ("driver embutido", os.path.join(DRIVERS_DIR, "chromedriver.exe"))]
    # baixados antes pelo fallback em Python (o mais novo primeiro)
    for c in sorted(glob.glob(os.path.join(DRIVERS_DIR, "auto", "*", "chromedriver*")), reverse=True):
        cands.append(("driver baixado antes", c))
    vistos, out = set(), []
    for origem, c in cands:
        if not c:
            continue
        c = os.path.abspath(c)
        if c in vistos:
            continue
        vistos.add(c)
        if os.path.isfile(c):
            out.append((origem, c))
    return out


def proxy_do_sistema() -> Optional[str]:
    """Proxy configurado na máquina (variáveis HTTPS_PROXY/HTTP_PROXY ou, no Windows, as Configurações de Proxy).
    O Selenium Manager não lê o proxy do Windows sozinho; o navegador lê, por isso "no Chrome funciona"."""
    if os.getenv("SE_PROXY"):
        return None                                    # o usuario ja definiu: o Selenium Manager le sozinho
    try:
        px = getproxies()
    except Exception:
        return None
    p = px.get("https") or px.get("http")
    if not p:
        return None
    return p if "://" in p else f"http://{p}"


def _selenium_manager(ignorar_path: bool, tentativas: int = 1, espera: float = 2.0,
                      dormir: Callable[[float], None] = time.sleep) -> Optional[str]:
    """Pede ao Selenium Manager o driver do Chrome instalado (baixa se preciso). Levanta se não conseguir.
    Repete `tentativas` vezes: com proxy que bloqueia de forma intermitente (F5 resolve), uma nova tentativa passa."""
    ultimo: Optional[Exception] = None
    for i in range(1, max(1, tentativas) + 1):
        try:
            return _selenium_manager_uma_vez(ignorar_path)
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if i < tentativas:
                log.warning("Selenium Manager falhou (tentativa %s/%s): %s", i, tentativas, e)
                dormir(espera * i)
    raise ultimo  # type: ignore[misc]


def _selenium_manager_uma_vez(ignorar_path: bool) -> Optional[str]:
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    args = ["--browser", "chrome", "--avoid-stats"]
    proxy = proxy_do_sistema()
    if proxy:
        args += ["--proxy", proxy]
    if ignorar_path:
        args.append("--skip-driver-in-path")
    try:
        res = SeleniumManager().binary_paths(args)
    except WebDriverException as e:
        # a causa real ("Unsuccessful response (404...) for URL ...", "error sending request ...") vem no corpo
        m = re.search(r"'message': '([^']+)'", str(e))
        raise RuntimeError("o Selenium Manager não conseguiu baixar/achar o driver" + (f": {m.group(1)}" if m else "")) from e
    return res.get("driver_path") or None


# ---------------------------------------------------------------------------------------------
# Fallback em Python: baixa o driver com repetição e diagnóstico de rede
# ---------------------------------------------------------------------------------------------
def versao_do_chrome() -> Optional[str]:
    """Versão do Chrome instalado (ex.: '143.0.7499.40') ou None. Windows: registro; demais: `chrome --version`."""
    if sys.platform == "win32":
        try:
            import winreg
            tentativas = [(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon", "version"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon", "version"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}", "pv"),
                          (winreg.HKEY_CURRENT_USER, r"Software\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}", "pv")]
            for raiz, chave, valor in tentativas:
                try:
                    with winreg.OpenKey(raiz, chave) as k:
                        v = str(winreg.QueryValueEx(k, valor)[0])
                        if re.match(r"\d+\.\d+", v):
                            return v
                except OSError:
                    continue
        except Exception:
            pass
        return None
    for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            continue
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    return None


def _classificar_rede(exc: BaseException) -> str:
    import requests
    if isinstance(exc, requests.exceptions.SSLError):
        return ("o certificado HTTPS não é confiável para o Python (proxy da empresa que inspeciona HTTPS?). "
                "Defina REQUESTS_CA_BUNDLE com o certificado da empresa")
    if isinstance(exc, requests.exceptions.ProxyError):
        return "o proxy recusou a conexão (ou exige autenticação)"
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout)):
        return "tempo esgotado (a rede/proxy não respondeu)"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "não foi possível conectar (DNS, firewall ou proxy)"
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def _http_get(url: str, *, json_: bool, tentativas: int = 5, dormir: Callable[[float], None] = time.sleep,
              timeout: float = 20.0):
    """GET com repetição: o proxy da rede bloqueia de forma intermitente (no navegador, o F5 resolve).
    Repete em erro de rede, status != 200 (ex.: 403 do proxy) e corpo que não é o esperado (página de bloqueio)."""
    import requests
    ultimo: Optional[str] = None
    for i in range(1, tentativas + 1):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code != 200:
                ultimo = f"HTTP {r.status_code}"
            else:
                return r.json() if json_ else r.content
        except ValueError:
            ultimo = "resposta inesperada (página de bloqueio do proxy?)"
        except requests.RequestException as e:
            ultimo = _classificar_rede(e)
        if i < tentativas:
            log.warning("GET %s falhou (%s); tentativa %s/%s", url, ultimo, i, tentativas)
            dormir(min(2.0 * i, 8.0))
    raise RuntimeError(f"{ultimo} (após {tentativas} tentativas)")


def _plataforma_cft() -> str:
    if sys.platform == "win32":
        return "win64" if "64" in (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "64") else "win32"
    if sys.platform == "darwin":
        import platform
        return "mac-arm64" if platform.machine() == "arm64" else "mac-x64"
    return "linux64"


def baixar_driver_em_python(versao_chrome: str, *, dormir: Callable[[float], None] = time.sleep) -> str:
    """Baixa o chromedriver da MESMA versão principal do Chrome (Chrome for Testing) para drivers/auto/<major>/."""
    major = versao_chrome.split(".")[0]
    dados = _http_get(CFT_MARCOS, json_=True, dormir=dormir)
    marco = (dados.get("milestones") or {}).get(major)
    if not marco:
        raise RuntimeError(f"a versão {major} do Chrome não consta na lista oficial de drivers")
    plat = _plataforma_cft()
    url = next((d["url"] for d in (marco.get("downloads") or {}).get("chromedriver", []) if d.get("platform") == plat), None)
    if not url:
        raise RuntimeError(f"não há chromedriver {plat} para o Chrome {major}")
    conteudo = _http_get(url, json_=False, dormir=dormir, timeout=120.0)
    nome = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"
    destino_dir = os.path.join(DRIVERS_DIR, "auto", major)
    os.makedirs(destino_dir, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        membro = next((n for n in z.namelist() if os.path.basename(n) == nome), None)
        if not membro:
            raise RuntimeError("o arquivo baixado não contém o chromedriver")
        destino = os.path.join(destino_dir, nome)
        with z.open(membro) as src, open(destino + ".tmp", "wb") as dst:
            dst.write(src.read())
    os.replace(destino + ".tmp", destino)
    if os.name != "nt":
        os.chmod(destino, 0o755)
    return destino


# ---------------------------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------------------------
def _dica(tentativas: List[Dict[str, Any]], chrome: Optional[str] = None) -> str:
    txt = " ".join(t["detalhe"] for t in tentativas).lower()
    manual = (f" Alternativa manual: abra https://googlechromelabs.github.io/chrome-for-testing/ no navegador (F5 se precisar), "
              f"baixe o chromedriver {_plataforma_cft()} do Chrome {chrome.split('.')[0] if chrome else '(versão do seu Chrome)'} "
              f"e coloque o arquivo em {DRIVERS_DIR}; o sistema passa a usá-lo.")
    if any(k in txt for k in ("error sending request", "unable to obtain", "baixar", "404", "timed out", "dns")):
        return ("Não foi possível baixar o driver: libere o acesso a googlechromelabs.github.io e "
                "storage.googleapis.com ou configure SE_PROXY / SE_DRIVER_MIRROR_URL." + manual)
    if "binary" in txt or "not found" in txt or "não encontrado" in txt:
        return "Confirme que o Google Chrome está instalado neste computador."
    return "Veja o detalhe de cada tentativa."


def garantir(*, forcar: bool = False, explicito: Optional[str] = None) -> Dict[str, Any]:
    """
    Garante um chromedriver compatível com o Chrome instalado e devolve o estado.
    `forcar=True` ignora o resultado em memória e refaz a verificação/atualização.
    """
    with _lock:
        if _estado["ok"] and not forcar:
            return estado()
        tentativas: List[Dict[str, Any]] = []

        def registrar(origem: str, ok: bool, detalhe: str):
            tentativas.append({"origem": origem, "ok": ok, "detalhe": detalhe})
            log.info("driver: %s -> %s (%s)", origem, "ok" if ok else "falhou", detalhe)

        def concluir(path: Optional[str], origem: str, info: Dict[str, Any]) -> Dict[str, Any]:
            _estado.update(ok=True, driver_path=path, chrome=info.get("chrome"), driver=info.get("driver"),
                           origem=origem, tentativas=tentativas, verificado_em=dt.datetime.now(),
                           mensagem=f"Chrome {info.get('chrome') or '?'} com chromedriver {info.get('driver') or '?'} ({origem}).")
            return estado()

        # 1) drivers já conhecidos
        for origem, caminho in _candidatos(explicito):
            ok, info = _abrir_para_teste(caminho)
            registrar(f"{origem}: {caminho}", ok, "compatível" if ok else info)
            if ok:
                return concluir(caminho, origem, info)

        # 2) Selenium Manager: primeiro ignorando drivers soltos no PATH (a causa clássica), depois aceitando-os
        for ignorar in (True, False):
            rotulo = "Selenium Manager" + (" (ignorando PATH)" if ignorar else "")
            try:
                caminho = _selenium_manager(ignorar, tentativas=3 if ignorar else 1)
            except Exception as e:  # noqa: BLE001
                registrar(rotulo, False, _resumir(e))
                continue
            ok, info = _abrir_para_teste(caminho)
            registrar(f"{rotulo}: {caminho or 'automático'}", ok, "compatível" if ok else info)
            if ok:
                return concluir(caminho, "Selenium Manager (baixado/atualizado)", info)

        # 3) Fallback em Python (repete, usa o proxy do sistema e diz a causa real da falha de rede)
        chrome = versao_do_chrome()
        if not chrome:     # a mensagem de "driver incompatível" traz a versão do Chrome
            achado = re.search(r"\((\d+\.\d+[\d.]*)\)", " ".join(t["detalhe"] for t in tentativas))
            chrome = achado.group(1) if achado else None
        if chrome:
            try:
                caminho = baixar_driver_em_python(chrome)
                ok, info = _abrir_para_teste(caminho)
                registrar(f"download em Python (Chrome {chrome}): {caminho}", ok, "compatível" if ok else info)
                if ok:
                    return concluir(caminho, "baixado pelo sistema", info)
            except Exception as e:  # noqa: BLE001
                registrar(f"download em Python (Chrome {chrome})", False, f"falhou: {e}")
        else:
            registrar("download em Python", False, "não consegui identificar a versão do Chrome instalado (ele está instalado?)")

        _estado.update(ok=False, driver_path=None, chrome=chrome, driver=None, origem=None, tentativas=tentativas,
                       verificado_em=dt.datetime.now(), mensagem="Nenhum chromedriver compatível. " + _dica(tentativas, chrome))
        return estado()


def atualizar() -> Dict[str, Any]:
    """Refaz a verificação ignorando o que está em memória (botão do Admin / `flask driver-check`)."""
    return garantir(forcar=True)


def driver_verificado() -> Optional[str]:
    """Caminho do driver já verificado neste processo, se houver (usado ao abrir o navegador)."""
    with _lock:
        return _estado["driver_path"] if _estado["ok"] else None


# ---------------------------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------------------------
_boot_iniciado = False


def _comando_flask() -> str:
    return next((a for a in sys.argv[1:] if not a.startswith("-")), "")


def verificar_na_inicializacao(app) -> bool:
    """Dispara a verificação em segundo plano (não atrasa o boot). Só quando o modo é navegador e o
    processo vai servir/rodar worker (não em `flask db upgrade`, `init-db`, testes etc.)."""
    global _boot_iniciado
    cfg = app.config
    if _boot_iniciado or cfg.get("TESTING") or not cfg.get("DRIVER_AUTOCHECK", True):
        return False
    if (cfg.get("EXEC_BACKEND") or "browser").lower() != "browser":
        return False
    exe = os.path.basename(sys.argv[0]).lower()
    if "flask" in exe and _comando_flask() not in ("run", "worker"):
        return False
    _boot_iniciado = True

    def _tarefa():
        try:
            st = garantir(explicito=cfg.get("CHROMEDRIVER_PATH"))
            (log.info if st["ok"] else log.error)("Driver do Chrome: %s", st["mensagem"])
        except Exception:
            log.exception("Falha inesperada ao verificar o driver do Chrome")

    threading.Thread(target=_tarefa, name="driver-check", daemon=True).start()
    return True
