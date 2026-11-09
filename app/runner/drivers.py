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
import logging
import os
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import NoSuchDriverException, SessionNotCreatedException, WebDriverException
from selenium.webdriver.chrome.service import Service

log = logging.getLogger(__name__)

DRIVERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "drivers"))

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


def _selenium_manager(ignorar_path: bool) -> Optional[str]:
    """Pede ao Selenium Manager o driver do Chrome instalado (baixa se preciso). Levanta se não conseguir."""
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    args = ["--browser", "chrome", "--avoid-stats"]
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
# API principal
# ---------------------------------------------------------------------------------------------
def _dica(tentativas: List[Dict[str, Any]]) -> str:
    txt = " ".join(t["detalhe"] for t in tentativas).lower()
    if any(k in txt for k in ("error sending request", "unable to obtain", "baixar", "404", "timed out", "dns")):
        return ("Não foi possível baixar o driver: libere o acesso a googlechromelabs.github.io e "
                "storage.googleapis.com (ou configure SE_PROXY / SE_DRIVER_MIRROR_URL), ou copie um chromedriver "
                f"compatível para {DRIVERS_DIR}.")
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
                caminho = _selenium_manager(ignorar)
            except Exception as e:  # noqa: BLE001
                registrar(rotulo, False, _resumir(e))
                continue
            ok, info = _abrir_para_teste(caminho)
            registrar(f"{rotulo}: {caminho or 'automático'}", ok, "compatível" if ok else info)
            if ok:
                return concluir(caminho, "Selenium Manager (baixado/atualizado)", info)

        _estado.update(ok=False, driver_path=None, chrome=None, driver=None, origem=None, tentativas=tentativas,
                       verificado_em=dt.datetime.now(), mensagem="Nenhum chromedriver compatível. " + _dica(tentativas))
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
