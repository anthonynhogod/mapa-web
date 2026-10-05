"""Worker: despacha jobs de comercializacao para o navegador certo (aqui, um falso)."""
import threading
import time
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.executor import worker as worker_mod
from app.extensions import db
from app.logic.comercializacao import build_commands, build_plano
from app.models import ExecJob, MapaCredencial, Registro, Role, Usuario


class NavFalso:
    instancias = []
    WORKER_ATTEMPTS = 1
    falhar_em = None

    def __init__(self, mode="HIDE", speed_profile=None, tap_ui=None, module_script=None):
        self.cmds, self.login_args = [], None
        NavFalso.instancias.append(self)

    def login(self, *args):
        self.login_args = args

    def injetar_scripts(self, *a):
        pass

    def executar_comando(self, cmd, ctx=""):
        self.cmds.append(cmd)
        if NavFalso.falhar_em == cmd:
            return "Erro: [REGISTRO_COM_DADOS] ja tem dados"
        return "OK: feito"

    def _collect_console_errors(self):
        return []

    def quit(self):
        pass


@pytest.fixture()
def cenario(app, monkeypatch):
    NavFalso.instancias, NavFalso.falhar_em = [], None
    monkeypatch.setattr(worker_mod, "NavegadorComercializacao", NavFalso)
    monkeypatch.setattr(worker_mod, "Navegador", NavFalso)
    monkeypatch.setattr(worker_mod, "ensure_valid_credential", lambda cred, dia: (True, None))

    u = Usuario(nome="op", senha=generate_password_hash("pw"), role=Role.USER)
    db.session.add(u)
    db.session.commit()
    c = MapaCredencial(owner_user_id=u.id, usuario_app="usr", numero_sif="167", especie="suino")
    c.senha = "s"
    db.session.add(c)
    reg = Registro(data=date(2026, 3, 1), periodo_ini=date(2026, 3, 1), periodo_fim=date(2026, 3, 31),
                   tipo="comercializacao", especie="suino", user_id=u.id, status="PT")
    db.session.add(reg)
    db.session.flush()
    venda = {"rotulo": "Venda", "tipo_transacao_idx": 1, "ambito_idx": 1, "operador_idx": 2}
    cmds = build_commands(build_plano([{"produto": "BACON", "uf": "RS", "quantidade": 3}]), tipo=venda)
    job = ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="vendas", commands=cmds,
                  meta={"modulo": "comercializacao", "numero_sif": "999"}, status="ESPERA", errors=[])
    db.session.add(job)
    db.session.commit()
    return reg.id, job.id


def _rodar_ate_terminar(app, job_id, timeout=15):
    stop = threading.Event()
    w = worker_mod.Worker(app, name="w-test", profile="prod", stop_event=stop)
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    fim = time.time() + timeout
    status = None
    while time.time() < fim:
        db.session.expire_all()
        status = db.session.get(ExecJob, job_id).status
        if status in ("SUCESSO", "FALHOU"):
            break
        time.sleep(0.1)
    stop.set()
    t.join(5)
    db.session.expire_all()
    return status


def test_job_comercializacao_sucesso(app, cenario):
    reg_id, job_id = cenario
    assert _rodar_ate_terminar(app, job_id) == "SUCESSO"
    nav = NavFalso.instancias[0]
    assert nav.login_args == ("01/03/2026", "31/03/2026", "usr", "s", "999")     # sif do job.meta
    job = db.session.get(ExecJob, job_id)
    assert nav.cmds == job.commands and job.progress == len(job.commands)
    assert db.session.get(Registro, reg_id).status == "FZ"


def test_job_comercializacao_erro_para_cedo_e_marca_registro(app, cenario):
    reg_id, job_id = cenario
    NavFalso.falhar_em = db.session.get(ExecJob, job_id).commands[0]
    assert _rodar_ate_terminar(app, job_id) == "FALHOU"
    job = db.session.get(ExecJob, job_id)
    assert len(NavFalso.instancias[0].cmds) == 1          # sem repetir (WORKER_ATTEMPTS=1) e sem seguir
    assert "[REGISTRO_COM_DADOS]" in job.errors[0]
    assert db.session.get(Registro, reg_id).status == "ER"


def test_retry_limpando_portal_troca_primeiro_comando(app, cenario):
    reg_id, job_id = cenario
    adm = Usuario(nome="adm", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.ADMIN)
    db.session.add(adm)
    job = db.session.get(ExecJob, job_id)
    job.status = "FALHOU"
    db.session.commit()
    c = app.test_client()
    c.post("/auth/login", data={"nome": "adm", "senha": "pw"})

    r = c.post(f"/admin/retry-job/{job_id}", data={"limpar": "1"})
    assert r.status_code == 302
    db.session.expire_all()
    job = db.session.get(ExecJob, job_id)
    assert job.status == "ESPERA" and job.commands[0] == 'limparTransacoes("Venda", ["Venda"])'
    assert db.session.get(Registro, reg_id).status == "PT"

    # retry normal nao mexe nos comandos
    job.status = "FALHOU"
    db.session.commit()
    c.post(f"/admin/retry-job/{job_id}")
    db.session.expire_all()
    assert db.session.get(ExecJob, job_id).commands[0].startswith("limparTransacoes(")
