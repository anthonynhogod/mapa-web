"""Veto de lancamentos: sem constante vinculada / credencial valida nada vai ao MAPA, e o motivo aparece."""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.executor.service import Bloqueado, reconstruir_job
from app.extensions import db
from app.logic.bloqueios import UF_ORIGEM_PADRAO, avaliar_credencial, uf_origem_index
from app.logic.constantes import invalidar_cache
from app.models import ExecJob, MapaCredencial, ProdutoVenda, Registro, Role, Usuario, VendasTmp


@pytest.fixture(autouse=True)
def _navegador(app):
    app.config["EXEC_BACKEND"] = "browser"


@pytest.fixture()
def ctx(app):
    u = Usuario(nome="op", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.ADMIN)
    db.session.add(u)
    db.session.commit()
    c = app.test_client()
    c.post("/auth/login", data={"nome": "op", "senha": "pw"})
    return c, u


def _cred(u, **kw):
    c = MapaCredencial(owner_user_id=u.id, usuario_app="usr", numero_sif="167", especie="suino", **kw)
    c.senha = "segredo"
    db.session.add(c)
    db.session.commit()
    return c


def _registro(u, records, status="AT"):
    reg = Registro(data=date(2026, 3, 1), periodo_ini=date(2026, 3, 1), periodo_fim=date(2026, 3, 31),
                   tipo="comercializacao", lancamento="venda", especie="suino", user_id=u.id, status=status)
    db.session.add(reg)
    db.session.flush()
    db.session.add(VendasTmp(registro_id=reg.id, uploaded_by=u.id, filename="v.xlsx", model="venda",
                             payload={"records": records, "meta": {"warnings": [], "errors": [], "counts": {}}}))
    db.session.commit()
    return reg


BOM = [{"produto": "BACON", "uf": "RS", "quantidade": 3}]


def test_preview_sem_credencial_mostra_motivo_e_desabilita_o_botao(ctx):
    c, u = ctx
    reg = _registro(u, BOM)
    html = c.get(f"/comercializacao/{reg.id}/preview").get_data(as_text=True)
    assert "Credenciais do MAPA não cadastradas" in html
    assert "disabled" in html.split("Confirmar")[0].rsplit("<button", 1)[1]


def test_finalizar_veta_com_todos_os_motivos_e_nao_cria_job(ctx):
    c, u = ctx
    _cred(u)
    reg = _registro(u, [{"produto": "PRODUTO FANTASMA", "uf": "RS", "quantidade": 1},
                        {"produto": "BACON", "uf": "ZZ", "quantidade": 2}])
    r = c.post(f"/comercializacao/{reg.id}/finalizar", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert ExecJob.query.count() == 0 and db.session.get(Registro, reg.id).status == "AT"
    assert "Lançamento vetado" in html and "PRODUTO FANTASMA" in html and "ZZ" in html   # os dois, nao so o primeiro
    assert "Como resolver" in html                                                         # tela de pendencias explica


def test_credencial_com_senha_ilegivel_veta(ctx, app):
    c, u = ctx
    cred = _cred(u)
    cred._senha_enc = b"lixo"            # ex.: FERNET_KEY trocada depois de salvar
    db.session.commit()
    _, motivos = avaliar_credencial(u.id)
    assert any("FERNET_KEY" in m for m in motivos)
    reg = _registro(u, BOM)
    c.post(f"/comercializacao/{reg.id}/finalizar")
    assert ExecJob.query.count() == 0


def test_retry_reconstroi_com_o_de_para_atual_e_veta_se_perdeu_o_vinculo(ctx, app):
    c, u = ctx
    _cred(u)
    reg = _registro(u, BOM, status="ER")
    job = ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="venda", status="FALHOU", errors=["x"],
                  commands=["verificarRegistroVazio(\"Venda\", [\"Venda\"])", "incluirProdutoVenda(\"RS\", 0, 3, \"Bacon\", 1, \"Venda\")"],
                  meta={"modulo": "comercializacao", "backend": "browser"})
    db.session.add(job)
    db.session.commit()

    # id antigo (1) no job; o De->Para atual tem outro -> o retry usa o ATUAL
    c.post(f"/admin/retry-job/{job.id}")
    db.session.expire_all()
    job = db.session.get(ExecJob, job.id)
    atual = ProdutoVenda.query.filter_by(nome="BACON").one().id_mapa
    assert job.status == "ESPERA"
    assert f", {atual}, " in job.commands[2] and ", 1, " not in job.commands[2]

    # produto desativado depois: o retry NAO reenfileira e explica
    job.status = "FALHOU"
    ProdutoVenda.query.filter_by(nome="BACON").one().ativo = False
    db.session.commit()
    invalidar_cache()
    antes = list(job.commands)
    r = c.post(f"/admin/retry-job/{job.id}", follow_redirects=True)
    db.session.expire_all()
    job = db.session.get(ExecJob, job.id)
    assert job.status == "FALHOU" and job.commands == antes
    assert "NÃO foi reenfileirado" in r.get_data(as_text=True) and "BACON" in r.get_data(as_text=True)


def test_reconstruir_job_sem_dados_da_planilha_bloqueia(ctx):
    c, u = ctx
    _cred(u)
    reg = _registro(u, BOM)
    VendasTmp.query.filter_by(registro_id=reg.id).delete()
    job = ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="venda", status="FALHOU", commands=["x()"],
                  meta={"modulo": "comercializacao", "backend": "browser"}, errors=[])
    db.session.add(job)
    db.session.commit()
    with pytest.raises(Bloqueado) as e:
        reconstruir_job(job, reg)
    assert "planilha" in " ".join(e.value.motivos)


def test_uf_de_origem_vem_da_credencial_e_o_padrao_e_avisado(ctx):
    c, u = ctx
    cred = _cred(u)
    idx, aviso = uf_origem_index(cred)
    assert idx == UF_ORIGEM_PADRAO and "assumida como RS" in aviso       # sem UF na credencial: avisa
    cred.cod_uf = "SC"
    db.session.commit()
    idx, aviso = uf_origem_index(cred)
    assert aviso is None and idx == 24                                    # SC no select do portal (seed)
    cred.cod_uf = "ZZ"
    assert uf_origem_index(cred)[1] and "não está cadastrada" in uf_origem_index(cred)[1]


def test_perfil_de_especie_nao_tem_fallback_para_suino(app):
    from app.blueprints.processor.especie import profiles
    import app.blueprints.processor.especie.suino.validate  # noqa: F401  (registra o perfil suino)
    assert profiles.get_profile("suino").name == "suino"
    with pytest.raises(KeyError):
        profiles.get_profile("bovino")


def test_salvar_credencial_no_modo_navegador_nao_apaga_dados_do_estabelecimento(ctx, app):
    c, u = ctx
    cred = _cred(u, cpf_cnpj="12345678000199", ambito="SIF", cod_uf="SC", cod_municipio_ibge="4205407")
    html = c.get("/credenciais-mapa").get_data(as_text=True)
    assert 'id="cod_uf"' in html and 'value="SC" selected' in html          # UF opcional visivel tambem no modo navegador
    r = c.post("/credenciais-mapa", data={"usuario_app": "u2", "numero_sif": "0167", "especie": "suino", "cod_uf": "RS"})
    assert r.status_code == 302
    db.session.expire_all()
    cred = MapaCredencial.query.one()
    assert (cred.usuario_app, cred.cod_uf) == ("u2", "RS")
    assert (cred.cpf_cnpj, cred.ambito, cred.cod_municipio_ibge) == ("12345678000199", "SIF", "4205407")   # preservados


def test_preview_do_abate_mostra_de_onde_vem_a_uf(ctx):
    from app.logic.bloqueios import uf_origem_texto
    c, u = ctx
    cred = _cred(u)
    assert "RS (padrão" in uf_origem_texto(cred) and "não informa a UF" in uf_origem_texto(cred)
    cred.cod_uf = "SC"
    assert uf_origem_texto(cred) == "SC (da credencial)"
