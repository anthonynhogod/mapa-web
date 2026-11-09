"""Tela /admin/threads: jobs de comercializacao precisam aparecer (inclusive com a lista vazia no carregamento)."""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import ExecJob, Registro, Role, Usuario


@pytest.fixture()
def admin(app):
    a = Usuario(nome="adm", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.ADMIN)
    db.session.add(a)
    db.session.commit()
    c = app.test_client()
    c.post("/auth/login", data={"nome": "adm", "senha": "pw"})
    return c, a


def _job(user, status, *, erros=None, tipo="comercializacao", backend="browser"):
    reg = Registro(data=date(2026, 3, 1), periodo_ini=date(2026, 3, 1), periodo_fim=date(2026, 3, 31),
                   tipo=tipo, lancamento="venda" if tipo == "comercializacao" else None, especie="suino",
                   user_id=user.id, status="PT")
    db.session.add(reg)
    db.session.flush()
    j = ExecJob(registro_id=reg.id, owner_user_id=user.id, gta_source="venda", commands=["a()", "b()"], status=status,
                progress=1, errors=erros or [], meta={"modulo": tipo, "lancamento": "venda", "backend": backend})
    db.session.add(j)
    db.session.commit()
    return j


def test_somente_admin(app):
    u = Usuario(nome="op", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.USER)
    db.session.add(u)
    db.session.commit()
    c = app.test_client()
    c.post("/auth/login", data={"nome": "op", "senha": "pw"})
    assert c.get("/admin/threads").status_code == 403
    assert c.get("/admin/threads/jobs").status_code == 403


def test_tabelas_existem_mesmo_vazias_para_o_auto_refresh_preencher(admin):
    c, _ = admin
    html = c.get("/admin/threads").get_data(as_text=True)
    assert 'id="jobs-tbody"' in html and 'id="workers-tbody"' in html       # antes: so com {% if rows %}
    assert "Nenhum job" in html and "Nenhum worker" in html


def test_job_de_comercializacao_aparece_com_modulo_periodo_e_motivo(admin):
    c, a = admin
    _job(a, "FALHOU", erros=["cmd[1]: Erro: [REGISTRO_COM_DADOS] ja tem dados", "Traceback (most recent call last):\n  File x"])
    html = c.get("/admin/threads").get_data(as_text=True)
    assert "Comercialização" in html and "01/03/2026 a 31/03/2026" in html and "adm" in html
    assert "[REGISTRO_COM_DADOS]" in html and "Retry (limpando portal)" in html
    # o resumo mostra a mensagem, nao o traceback
    resumo = html.split('class="erro-resumo"')[1].split("</div>")[0]
    assert "REGISTRO_COM_DADOS" in resumo and "Traceback" not in resumo


def test_filtros_e_fragmentos(admin):
    c, a = admin
    _job(a, "SUCESSO")
    _job(a, "ESPERA")
    em_aberto = c.get("/admin/threads/jobs").get_data(as_text=True)
    assert em_aberto.count("<tr>") == 1 and "ESPERA" in em_aberto           # padrao: sem os concluidos
    todos = c.get("/admin/threads/jobs?filtro=todos").get_data(as_text=True)
    assert todos.count("<tr>") == 2 and "SUCESSO" in todos
    assert "Nenhum job falho" in c.get("/admin/threads/jobs?filtro=falhos").get_data(as_text=True)
    assert "Nenhum worker" in c.get("/admin/threads/workers").get_data(as_text=True)
    assert c.get("/admin/threads/jobs?filtro=lixo").status_code == 200       # filtro invalido cai no padrao


def test_registros_sem_job_sao_explicados(admin):
    c, a = admin
    db.session.add(Registro(data=date(2026, 3, 1), periodo_ini=date(2026, 3, 1), periodo_fim=date(2026, 3, 31),
                            tipo="comercializacao", lancamento="venda", especie="suino", user_id=a.id, status="AT"))
    db.session.commit()
    html = c.get("/admin/threads").get_data(as_text=True)
    assert "sem job na fila" in html and "Comercialização" in html
