"""
Worker que busca ExecJobs diretamente no banco e executa os comandos via Selenium.
- Evita fila em memória entre processos (cada worker 'faz claim' no DB).
- Usa SELECT ... FOR UPDATE SKIP LOCKED quando suportado (PG/MySQL 8+); possuí fallback.
- Só 'clama' jobs em ESPERA (sem corrida em FALHOU).
- Etiqueta job.meta com host/pid/worker_name ao fazer claim (ambos os caminhos).
"""

import atexit
from threading import Lock
import os
import socket
import threading
import time
import datetime as dt
from app.runner.web.navegador import Navegador, NavegadorComercializacao
import traceback
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from app import db
from app.models import ExecJob, Registro, MapaCredencial, WorkerSession  # Adicione WorkerSession
from app.executor.credentials import ensure_valid_credential  # valida credenciais
from app.runner import drivers
from app.runner.drivers import NavegadorIndisponivel
from app.executor import conferencia
from app.runner.exceptions import FalhaNoJavascript
from app.mapa_api.execucao import executar_job_api

_ACTIVE_NAV = set()
_ACTIVE_LOCK = Lock()

def _register_nav(nav):
    with _ACTIVE_LOCK:
        _ACTIVE_NAV.add(nav)

def _unregister_nav(nav):
    with _ACTIVE_LOCK:
        if nav in _ACTIVE_NAV:
            _ACTIVE_NAV.remove(nav)

def shutdown_all_browsers():
    """Fecha todos os navegadores vivos deste processo."""
    with _ACTIVE_LOCK:
        navs = list(_ACTIVE_NAV)
        _ACTIVE_NAV.clear()
    for n in navs:
        try:
            n.quit()
        except Exception:
            pass

atexit.register(shutdown_all_browsers)

def _fim_registro(job, registro):
    """data_fim do Registro so muda em lancamentos; conferencia/complemento nao a alteram."""
    if not conferencia.tipo_job(job):
        registro.data_fim = job.finished_at


def _pode_repetir(nav, resultado) -> bool:
    """True so para erro que o navegador classifica como transitorio (nao ha como saber se um erro desconhecido ja gravou)."""
    classificar = getattr(nav, "_erro_transitorio", None)
    try:
        return bool(classificar and classificar(resultado))
    except Exception:
        return False


class Worker:
    def __init__(self, app, name: str = "worker", profile: str = "prod", stop_event: threading.Event = None):
        try:
            app = app._get_current_object()
        except Exception:
            pass
        self.app = app
        self.name = name
        self.profile = profile
        self.stop_event = stop_event or threading.Event()


    def _tag_job_meta(self, job: ExecJob):
        """Adiciona host/pid/worker_name ao meta do job e persiste."""
        meta = dict(job.meta or {})
        meta["host"] = socket.gethostname()
        meta["pid"] = os.getpid()
        meta["worker_name"] = self.name
        job.meta = meta

    def _claim_next_job(self):
        """
        Captura (claim) um job ESPERA de forma atômica e já marca EXECUTANDO.
        1) Tenta SELECT ... FOR UPDATE SKIP LOCKED (PG/MySQL 8+) com begin_nested().
        2) Fallback: UPDATE condicional (compatível com SQLite e outros).
        Retorna o ExecJob em EXECUTANDO ou None se não houver candidato.
        """
        # Garante que não há restos de transação anterior nesta thread
        try:
            db.session.rollback()
        except Exception:
            pass

        now = dt.datetime.utcnow()

        # Caminho 1: SKIP LOCKED (quando suportado)
        try:
            with db.session.begin_nested():
                job = (
                    db.session.query(ExecJob)
                    .filter(ExecJob.status == "ESPERA")
                    .order_by(ExecJob.id.asc())
                    .with_for_update(skip_locked=True)  # pode levantar OperationalError se não suportado
                    .first()
                )
                if not job:
                    return None

                job.status = "EXECUTANDO"
                job.started_at = now
                job.errors = []
                # Etiqueta de ownership do processo/host atual
                self._tag_job_meta(job)

            db.session.commit()
            return job

        except OperationalError:
            # Dialeto não suporta SKIP LOCKED -> fallback genérico
            db.session.rollback()

            with db.session.begin_nested():
                candidate_id = db.session.execute(
                    select(ExecJob.id)
                    .where(ExecJob.status == "ESPERA")
                    .order_by(ExecJob.id.asc())
                    .limit(1)
                ).scalar_one_or_none()

                if candidate_id is None:
                    return None

                # Atualiza condicionalmente: só quem vê ESPERA transforma em EXECUTANDO
                res = db.session.execute(
                    update(ExecJob)
                    .where(ExecJob.id == candidate_id, ExecJob.status == "ESPERA")
                    .values(status="EXECUTANDO", started_at=now, errors=[])
                )
                if res.rowcount != 1:
                    # outro worker ganhou a corrida
                    db.session.rollback()
                    return None

            # Recarrega o objeto, etiqueta meta e persiste
            job = db.session.get(ExecJob, candidate_id)
            if not job:
                db.session.rollback()
                return None

            self._tag_job_meta(job)
            db.session.commit()
            return job

    # -------------
    # Loop principal
    # -------------
    def run(self):
        with self.app.app_context():
            # Cria ou busca sessão no DB
            session = WorkerSession.query.filter_by(name=self.name, status="EXECUTANDO").first()
            if not session:
                session = WorkerSession(name=self.name, profile=self.profile, threads=1)
                db.session.add(session)
                db.session.commit()
            
            # Armazena o ID para evitar DetachedInstanceError
            session_id = session.id
            
            # Confere (uma vez por processo) se o chromedriver combina com o Chrome; atualiza se nao combinar.
            # Falha aqui nao derruba o worker: cada job que precisar do navegador falha com a causa certa.
            cfg = self.app.config
            if not cfg.get("TESTING") and cfg.get("DRIVER_AUTOCHECK", True) and (cfg.get("EXEC_BACKEND") or "browser").lower() != "api":
                try:
                    st = drivers.garantir(explicito=cfg.get("CHROMEDRIVER_PATH"))
                    print(f"[{self.name}] Driver do Chrome: {st['mensagem']}")
                except Exception as e_drv:
                    print(f"[{self.name}] Verificacao do driver falhou: {e_drv!r}")

            scripts_path = self.app.config.get("SCRIPTS_JS_PATH")
            last_check = time.time()
            while not self.stop_event.is_set(): 
                # Recarrega sessão a cada 5 segundos
                if time.time() - last_check > 5:
                    session = WorkerSession.query.filter_by(id=session_id).first()
                    if not session or session.status == "STOPPING":
                        print(f"[{self.name}] Recebido sinal de parada ou sessão expirada. Finalizando...")
                        if session:
                            session.status = "PARADO"
                            db.session.commit()
                        break
                    last_check = time.time()
                
                job = None
                nav = None  # visível no except externo abaixo, mesmo se a falha ocorrer antes de abrir o navegador
                try:
                    job = self._claim_next_job()
                    if not job:
                        time.sleep(2)
                        continue

                    total_cmds = len(job.commands or [])
                    print(f"[{self.name}] EXECUTANDO job={job.id} cmds={total_cmds}")

                    # 2) Carrega registro
                    registro: Registro = db.session.get(Registro, job.registro_id)
                    if not registro:
                        job.status = "FALHOU"
                        job.finished_at = dt.datetime.utcnow()
                        job.errors = (job.errors or []) + ["Registro não encontrado."]
                        db.session.commit()
                        print(f"[{self.name}] job={job.id} FALHOU: registro não encontrado")
                        continue

                    # === Atualiza status do Registro: processamento ===
                    now = dt.datetime.utcnow()
                    if not conferencia.tipo_job(job):      # conferencia/complemento nao mexem no status do registro
                        registro.status = "PT"   # processamento
                        registro.data_inicio = now
                    db.session.commit()

                    especie = getattr(registro, "especie", None)
                    comercializacao = registro.tipo == "comercializacao"
                    if comercializacao:
                        ini_str = registro.periodo_ini.strftime("%d/%m/%Y")
                        fim_str = registro.periodo_fim.strftime("%d/%m/%Y")
                        dia_str = f"{ini_str} a {fim_str}"
                    else:
                        dia_str = registro.data.strftime("%d/%m/%Y")

                    # 3) Credenciais
                    cred = (
                        MapaCredencial.query
                        .filter_by(owner_user_id=job.owner_user_id, especie=especie)
                        .one_or_none()
                        or MapaCredencial.query.filter_by(owner_user_id=job.owner_user_id).first()
                    )
                    if not cred:
                        job.status = "FALHOU"
                        job.finished_at = dt.datetime.utcnow()
                        job.errors = (job.errors or []) + ["Credenciais MAPA ausentes."]
                        # Registro -> ER
                        registro.status = conferencia.status_registro(job, "ER")
                        _fim_registro(job, registro)
                        db.session.commit()
                        print(f"[{self.name}] job={job.id} FALHOU: credenciais ausentes")
                        continue

                    # --- Webservice: um envio JSON (sem navegador) ---
                    if (job.meta or {}).get("backend") == "api":
                        ok_api, msg_api = executar_job_api(job, registro, cred)
                        job.finished_at = dt.datetime.utcnow()
                        job.progress = len(job.commands or []) if ok_api else job.progress
                        job.status = "SUCESSO" if ok_api else "FALHOU"
                        if not ok_api:
                            job.errors = (job.errors or []) + [msg_api]
                        registro.status = conferencia.status_registro(job, "FZ" if ok_api else "ER")
                        _fim_registro(job, registro)
                        db.session.commit()
                        print(f"[{self.name}] job={job.id} {job.status} (api): {msg_api[:200]}")
                        continue

                    try:
                        ok, err = ensure_valid_credential(cred, dia_str)
                    except NavegadorIndisponivel as e_nav:
                        # Chrome/driver nao abre: falha de INFRA, a credencial nao foi testada e nao e marcada invalida
                        job.status = "FALHOU"
                        job.finished_at = dt.datetime.utcnow()
                        job.errors = (job.errors or []) + [str(e_nav)]
                        registro.status = conferencia.status_registro(job, "ER")
                        _fim_registro(job, registro)
                        db.session.commit()
                        print(f"[{self.name}] job={job.id} FALHOU: {e_nav}")
                        continue
                    if not ok:
                        job.status = "FALHOU"
                        job.finished_at = dt.datetime.utcnow()
                        job.errors = (job.errors or []) + [f"Credenciais inválidas: {err}"]
                        registro.status = conferencia.status_registro(job, "ER")
                        _fim_registro(job, registro)
                        db.session.commit()
                        print(f"[{self.name}] job={job.id} FALHOU: credenciais inválidas ({err})")
                        continue

                    # 5) Executa comandos
                    print(f"[{self.name}] Abrindo navegador para executar comandos do job {job.id}")
                    nav = None
                    try:
                        prof = (self.profile or " ").lower()
                        if prof == "prod":
                            nav_mode, nav_speed, nav_tap = "HIDE", "ultra", "never"
                        elif prof == "test":
                            nav_mode, nav_speed, nav_tap = "SHOW", "fast", "auto"
                        elif prof == "dev":
                            nav_mode, nav_speed, nav_tap = "SHOW", "balanced", "auto"
                        else:
                            nav_mode  = self.app.config.get("NAV_MODE", "HIDE")
                            nav_speed = self.app.config.get("NAV_SPEED_PROFILE", "balanced")
                            nav_tap   = self.app.config.get("NAV_TAP_UI", "auto")

                        if comercializacao:
                            nav = NavegadorComercializacao(
                                mode=nav_mode, speed_profile=nav_speed, tap_ui=nav_tap,
                            )
                            _register_nav(nav)
                            sif = (job.meta or {}).get("numero_sif") or cred.numero_sif
                            kw_login = {"criar_se_faltar": False} if conferencia.tipo_job(job) else {}
                            nav.login(ini_str, fim_str, cred.usuario_app, cred.senha, sif, **kw_login)
                        else:
                            # SCRIPTS_JS_PATH sobrescreve so o script do modulo (o core.js sempre vai antes)
                            nav = Navegador(
                                mode=nav_mode, speed_profile=nav_speed, tap_ui=nav_tap,
                                module_script=scripts_path or None,
                            )
                            _register_nav(nav)
                            nav.login(dia_str, cred.usuario_app, cred.senha)

                        preparo_erro = None
                        if conferencia.tipo_job(job) == "complemento":
                            preparo_erro = conferencia.preparar_complemento(job, nav)
                            total_cmds = len(job.commands or [])
                            if preparo_erro:
                                job.errors = (job.errors or []) + [preparo_erro]
                            db.session.commit()

                        for i, cmd in enumerate([] if preparo_erro else (job.commands or [])):
                            # Verificação adicional para parada rápida
                            if self.stop_event.is_set():
                                print(f"[{self.name}] Parada forçada detectada durante execução. Abortando job {job.id}.")
                                job.status = "FALHOU"
                                job.finished_at = dt.datetime.utcnow()
                                job.errors = (job.errors or []) + ["Execução interrompida por parada forçada."]
                                registro.status = conferencia.status_registro(job, "ER")
                                _fim_registro(job, registro)
                                db.session.commit()
                                break  # Sai do loop de comandos
                            
                            print(f"[{self.name}] job={job.id} cmd[{i+1}/{total_cmds}]: {cmd[:160]}")
                            last_result = None

                            for attempt in range(max(1, int(getattr(nav, 'WORKER_ATTEMPTS', 2)))):
                                # Verificação dentro do attempt também, se necessário
                                if self.stop_event.is_set():
                                    break
                                last_result = nav.executar_comando(cmd, ctx=f"cmd_{i+1}")
                                if isinstance(last_result, str) and "[SESSION_CLOSED]" in last_result:
                                    raise RuntimeError("SESSION_CLOSED")
                                if isinstance(last_result, str) and last_result.strip().lower().startswith("erro"):
                                    # So repete erro TRANSITORIO (timeout, helper ausente...). Erro de negocio
                                    # ou desconhecido nao e relancado: comandos como incluirGta/incluirProduto
                                    # nao sao idempotentes e duplicariam o lancamento no MAPA.
                                    if _pode_repetir(nav, last_result):
                                        time.sleep(0.3)
                                        continue
                                break
                            
                            job.progress = i + 1
                            erro_leitura = conferencia.captura_leitura(job, cmd, last_result)
                            if erro_leitura:
                                job.errors = (job.errors or []) + [f"cmd[{i+1}]: {erro_leitura}"]
                            db.session.commit()

                            if erro_leitura:
                                break                     # a mensagem ja foi registrada acima
                            if isinstance(last_result, str) and (
                                last_result.strip().lower().startswith("erro")
                            ):
                                # UI message já vem como Erro: [UI] ...
                                job.errors = (job.errors or []) + [f"cmd[{i+1}]: {last_result}"]
                                # encerra cedo: trata como erro terminal
                                break
                            # Se a UI retornou "Nenhum registro encontrado.", nav já não loga;
                            # e não consideramos erro — segue o fluxo normalmente.

                        # Conferencia/complemento: a ultima leitura vira uma Verificacao
                        if conferencia.tipo_job(job) and not job.errors:
                            erro_conclusao = conferencia.concluir(job, registro)
                            if erro_conclusao:
                                job.errors = (job.errors or []) + [erro_conclusao]

                        # Finalização do job
                        job.status = "FALHOU" if job.errors else "SUCESSO"
                        job.finished_at = dt.datetime.utcnow()
                        # Registro -> FZ se sucesso; ER se erro
                        registro.status = conferencia.status_registro(job, "FZ" if not job.errors else "ER")
                        _fim_registro(job, registro)
                        db.session.commit()
                        print(f"[{self.name}] job={job.id} {job.status} - errors={len(job.errors or [])}")

                        # Conferencia constante: depois de um lancamento concluido, le o portal e compara linha a linha
                        if comercializacao and job.status == "SUCESSO" and not conferencia.tipo_job(job):
                            try:
                                novo_id = conferencia.encadear_conferencia(job, registro, self.app.config)
                                db.session.commit()
                                if novo_id:
                                    print(f"[{self.name}] conferencia automatica enfileirada: job={novo_id}")
                            except Exception as e_conf:
                                db.session.rollback()
                                print(f"[{self.name}] nao foi possivel enfileirar a conferencia automatica: {e_conf!r}")

                    except RuntimeError as e:
                        # Sessão encerrada manualmente ou erro terminal controlado
                        if "SESSION_CLOSED" in str(e):
                            job.status = "FALHOU"
                            job.finished_at = dt.datetime.utcnow()
                            job.errors = (job.errors or []) + ["Navegador encerrado manualmente (SESSION_CLOSED)."]
                            registro.status = conferencia.status_registro(job, "ER")
                            _fim_registro(job, registro)
                            db.session.commit()
                            print(f"[{self.name}] job={job.id} FALHOU: navegador encerrado (SESSION_CLOSED)")
                        else:
                            raise
                    finally:
                        try:
                            if nav:
                                _unregister_nav(nav)
                                nav.quit()
                        except Exception:
                            pass

                except Exception as e:
                    # Falha geral do ciclo
                    try:
                        tb = traceback.format_exc()
                        console_errs = []
                        if nav is not None:
                            try:
                                console_errs = nav._collect_console_errors()
                            except Exception:
                                console_errs = []
                        if job:
                            job.status = "FALHOU"
                            job.finished_at = dt.datetime.utcnow()
                            if isinstance(e, NavegadorIndisponivel):
                                job.errors = (job.errors or []) + [str(e)]
                            elif isinstance(e, FalhaNoJavascript) and "[REGISTRO_INEXISTENTE]" in str(e):
                                job.errors = (job.errors or []) + [str(e)]
                            elif drivers.eh_erro_de_driver(e):
                                job.errors = (job.errors or []) + [drivers.explicar(e)]
                            else:
                                job.errors = (job.errors or []) + [repr(e), tb]
                            if console_errs:
                                job.errors = job.errors + ["[JS-CONSOLE] " + " | ".join(console_errs)]
                            # Registro -> ER
                            registro = db.session.get(Registro, job.registro_id)
                            if registro:
                                registro.status = conferencia.status_registro(job, "ER")
                                _fim_registro(job, registro)
                            db.session.commit()
                            print(f"[{self.name}] job={job.id} FALHOU (exception): {e!r}")
                            print(tb)
                        else:
                            print(f"[{self.name}] Falha antes do claim: {e!r}")
                            print(tb)
                        db.session.rollback()
                    except Exception:
                        try:
                            db.session.rollback()
                        except Exception:
                            pass
                finally:
                    try:
                        db.session.expunge_all()
                        db.session.close()
                    except Exception:
                        pass
            
            # INSIRA O TRECHO AQUI: Após o loop, para executar ao sair
            session = WorkerSession.query.filter_by(id=session_id).first()
            if session:
                session.status = "PARADO"
                db.session.commit()
            from app.executor.queue import _active_threads
            _active_threads.pop(self.name, None)  # Limpa referência
            print(f"[{self.name}] Finalizado.")