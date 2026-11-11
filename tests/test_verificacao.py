"""Conferencia relatorio do MAPA x enviado: leitura, comparacao por celula, base do registro e telas."""
import io
from datetime import date

import pytest
from openpyxl import load_workbook
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.logic import verificacao as V
from app.models import ExecJob, MapaCredencial, ProdutoVenda, Registro, Role, Usuario, Verificacao, VendasTmp
from helpers import LINHAS_OK, xlsx_relatorio_mapa, xlsx_vendas


def it(uf, desc, q, produto=None, id_=1):
    return {"uf": uf, "descricao": desc, "quantidade": q, "produto": produto or desc.upper(), "id": id_}


def ln(prod, uf, total):
    return {"produto": prod, "destino": uf, "total": total, "linha": 0}


# ---------------------------------------------------------------- leitura do relatorio
def test_le_o_layout_do_mapa_e_ignora_o_que_nao_e_venda_por_uf_em_kg():
    buf = xlsx_relatorio_mapa(
        [("APRESUNTADO", "RS", 100.5), ("APRESUNTADO", "SP", "1.234,56"),
         ("BACON", "RS", 9, {"Operação": "PAÍS"}),
         ("BACON", "SP", 8, {"Controle Origem (Receb)": "55"}),
         ("OVOS", "SP", 7, {"Unidade": "Unidade"})],
        antes=3, extra=[["Total", "", "", "", "", "", "", "", "", "", "", "", "", "", 999]])
    r = V.ler_relatorio_mapa(buf)
    assert r["meta"]["errors"] == []
    assert [(l["produto"], l["destino"], l["total"]) for l in r["linhas"]] == [("APRESUNTADO", "RS", 100.5), ("APRESUNTADO", "SP", 1234.56)]
    motivos = " | ".join(r["meta"]["ignoradas"])
    assert "PAÍS" in motivos and "recebimentos" in motivos and "Unidade" in motivos
    assert r["meta"]["controles"] == ["167"]


def test_relatorio_sem_colunas_obrigatorias_ou_invalido_nao_estoura():
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["a", "b"])
    b = io.BytesIO()
    wb.save(b)
    b.seek(0)
    assert "Cabeçalho não encontrado" in V.ler_relatorio_mapa(b)["meta"]["errors"][0]["message"]
    assert V.ler_relatorio_mapa(io.BytesIO(b"nao e xlsx"))["meta"]["errors"]


# ---------------------------------------------------------------- comparacao
def test_totais_por_estado_e_por_produto_iguais_nao_bastam_so_a_celula_acusa():
    """Erros que se compensam: o total de cada UF e de cada produto bate, mas as 4 celulas estao erradas."""
    enviados = [it("MT", "Bacon", 100), it("TO", "Bacon", 100), it("MT", "Salame", 100), it("TO", "Salame", 100)]
    mapa = [ln("BACON", "MT", 150), ln("BACON", "TO", 50), ln("SALAME", "MT", 50), ln("SALAME", "TO", 150)]
    res = V.verificar(enviados, mapa)
    assert all(g["diff"] == 0 for g in res["por_uf"]) and all(g["diff"] == 0 for g in res["por_produto"])
    assert res["totais"]["diff"] == 0
    assert res["status"] == "DIVERGENTE" and res["totais"]["divergentes"] == 4


def test_produtos_internos_com_a_mesma_descricao_sao_somados_como_faz_o_mapa():
    enviados = [it("RS", "Apresuntado", 10, "APRESUNTADO RESFRIADO", 17178), it("RS", "Apresuntado", 5.5, "APRESUNTADO FATIADO", 17176)]
    res = V.verificar(enviados, [ln("APRESUNTADO", "RS", 15.5)])
    assert res["status"] == "OK" and res["celulas"][0]["nosso"] == 15.5 and len(res["celulas"][0]["itens"]) == 2


def test_acento_caixa_e_pontuacao_nao_criam_divergencia():
    res = V.verificar([it("RS", "Linguica cozida", 1)], [ln("LINGUIÇA COZIDA", "rs", 1)])
    assert res["status"] == "OK"


def test_arredondamento_tem_faixa_propria_e_acima_dela_e_divergencia():
    enviados = [it("RS", "Bacon", 1.0, "A"), it("RS", "Bacon", 1.0, "B")]
    assert V.verificar(enviados, [ln("BACON", "RS", 2.02)])["celulas"][0]["status"] == "arredondamento"
    assert V.verificar(enviados, [ln("BACON", "RS", 2.02)])["status"] == "OK"
    assert V.verificar(enviados, [ln("BACON", "RS", 2.05)])["celulas"][0]["status"] == "divergente"


def test_categorias_de_celula_e_nome_sem_correspondencia():
    enviados = [it("MT", "Bacon", 10), it("MT", "Salame", 5), it("TO", "Pate raro", 3)]
    mapa = [ln("BACON", "TO", 10), ln("SALAME", "MT", 5), ln("MORTADELA", "RS", 7)]
    st = {(c["descricao"], c["uf"]): c["status"] for c in V.verificar(enviados, mapa)["celulas"]}
    assert st[("Bacon", "MT")] == "faltando_no_mapa"            # Bacon existe no relatorio, mas nao em MT
    assert st[("BACON", "TO")] == "a_mais_no_mapa"
    assert st[("Salame", "MT")] == "ok"
    assert st[("Pate raro", "TO")] == "nome_nosso_sem_par"      # nome nao aparece em lugar nenhum do relatorio
    assert st[("MORTADELA", "RS")] == "nome_mapa_sem_par"


def test_dicas_duplicidade_e_item_omitido():
    dobro = V.verificar([it("RS", "Bacon", 100, "A")], [ln("BACON", "RS", 200)])["celulas"][0]
    assert "dobro" in dobro["dica"]
    omitido = V.verificar([it("RS", "Bacon", 100, "BACON A"), it("RS", "Bacon", 40, "BACON B")], [ln("BACON", "RS", 100)])["celulas"][0]
    assert omitido["status"] == "divergente" and "BACON B" in omitido["dica"] and "não ter sido gravado" in omitido["dica"]


def test_relatorio_de_outro_periodo_gera_aviso():
    enviados = [it("RS", f"P{i}", 1) for i in range(8)]
    res = V.verificar(enviados, [ln(f"X{i}", "RS", 1) for i in range(8)])
    assert any("OUTRO período" in a for a in res["avisos"])


def test_mesma_celula_repetida_no_relatorio_e_somada():
    res = V.verificar([it("RS", "Bacon", 3)], [ln("BACON", "RS", 1), ln("BACON", "RS", 2)])
    assert res["status"] == "OK" and res["celulas"][0]["linhas_mapa"] == 2


# ---------------------------------------------------------------- base de comparacao (o que foi ENVIADO)
@pytest.fixture()
def ctx(app):
    u = Usuario(nome="op", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.USER)
    db.session.add(u)
    db.session.commit()
    c = app.test_client()
    c.post("/auth/login", data={"nome": "op", "senha": "pw"})
    return c, u


def _registro(u, records=None, status="FZ"):
    reg = Registro(data=date(2026, 3, 1), periodo_ini=date(2026, 3, 1), periodo_fim=date(2026, 3, 31),
                   tipo="comercializacao", lancamento="venda", especie="suino", user_id=u.id, status=status)
    db.session.add(reg)
    db.session.flush()
    if records is not None:
        db.session.add(VendasTmp(registro_id=reg.id, uploaded_by=u.id, filename="v.xlsx", model="venda",
                                 payload={"records": records, "meta": {}}))
    db.session.commit()
    return reg


def _job(reg, u, *, plano=None, commands=("x()",), status="SUCESSO"):
    j = ExecJob(registro_id=reg.id, owner_user_id=u.id, gta_source="venda", commands=list(commands), status=status,
                errors=[], meta={"modulo": "comercializacao", **({"plano": plano} if plano else {})})
    db.session.add(j)
    db.session.commit()
    return j


PLANO = [{"uf": "RS", "index": 23, "itens": [{"produto": "BACON", "descricao": "Bacon", "id": 17189, "quantidade": 10.0}]}]


def test_base_usa_o_plano_gravado_no_job_mesmo_que_o_de_para_tenha_mudado(app, ctx):
    c, u = ctx
    reg = _registro(u, [{"produto": "BACON", "uf": "RS", "quantidade": 99}])
    _job(reg, u, plano=PLANO)
    ProdutoVenda.query.filter_by(nome="BACON").one().descricao_busca = "Descricao Nova"      # De->Para mudou depois
    db.session.commit()
    itens, origem, avisos, job_id = V.base_do_registro(reg)
    assert origem == "job_plano" and itens[0]["descricao"] == "Bacon" and itens[0]["quantidade"] == 10.0 and avisos == []


def test_base_cai_para_os_comandos_do_job_e_depois_para_a_planilha_com_aviso(app, ctx):
    c, u = ctx
    reg = _registro(u, [{"produto": "BACON", "uf": "RS", "quantidade": 3}])
    cmd = 'incluirProdutoVenda("RS", 0, 24197.34, "Apresuntado \\"x\\"", 17178, "Venda")'
    j = _job(reg, u, commands=["verificarRegistroVazio()", cmd])
    itens, origem, avisos, _ = V.base_do_registro(reg)
    assert origem == "job_comandos" and itens == [{"uf": "RS", "produto": None, "descricao": 'Apresuntado "x"', "id": 17178, "quantidade": 24197.34}]
    assert "lidos dos comandos" in avisos[0]
    db.session.delete(j)
    db.session.commit()
    itens, origem, avisos, _ = V.base_do_registro(reg)
    assert origem == "planilha_reprocessada" and "RECALCULADA" in avisos[0]


def test_base_sem_nenhum_dado_levanta(app, ctx):
    c, u = ctx
    with pytest.raises(ValueError, match="não tem job"):
        V.base_do_registro(_registro(u))


def test_finalizar_e_retry_gravam_o_plano_no_job(app, ctx):
    from app.executor.service import reconstruir_job
    c, u = ctx
    app.config["EXEC_BACKEND"] = "browser"
    cred = MapaCredencial(owner_user_id=u.id, usuario_app="u", numero_sif="167", especie="suino")
    cred.senha = "s"
    db.session.add(cred)
    reg = _registro(u, [{"produto": "BACON", "uf": "RS", "quantidade": 3}], status="AT")
    c.post(f"/comercializacao/{reg.id}/finalizar")
    job = ExecJob.query.one()
    assert job.meta["plano"][0]["uf"] == "RS" and job.meta["plano"][0]["itens"][0]["descricao"] == "Bacon"
    job.meta = {k: v for k, v in job.meta.items() if k != "plano"}
    reconstruir_job(job, reg)
    assert job.meta["plano"][0]["itens"][0]["quantidade"] == 3.0


# ---------------------------------------------------------------- telas
def _envia(c, mapa, **dados):
    return c.post("/verificacao/nova", data={"mapa": (mapa, "relatorio.xlsx"), **dados}, content_type="multipart/form-data")


def test_fluxo_completo_confere_divergencia_exporta_e_isola_usuarios(app, ctx):
    c, u = ctx
    reg = _registro(u, [])
    plano = [{"uf": "MT", "index": 9, "itens": [{"produto": "BACON", "descricao": "Bacon", "id": 1, "quantidade": 1586.99}]},
             {"uf": "RS", "index": 23, "itens": [{"produto": "SALAME", "descricao": "Salame", "id": 2, "quantidade": 50.0}]}]
    _job(reg, u, plano=plano)
    assert c.get("/verificacao/").status_code == 200

    r = _envia(c, xlsx_relatorio_mapa([("BACON", "MT", 1586.99), ("SALAME", "RS", 50)]), registro=str(reg.id))
    assert r.status_code == 302
    v = Verificacao.query.one()
    assert v.status == "OK" and v.origem_base == "job_plano" and v.registro_id == reg.id
    assert "Tudo confere" in c.get(r.headers["Location"]).get_data(as_text=True)

    # Bacon existe no relatorio so em TO: em MT "falta no MAPA", e o de TO e "a mais"
    r = _envia(c, xlsx_relatorio_mapa([("SALAME", "RS", 50), ("BACON", "TO", 5)]), registro=str(reg.id))
    html = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "Falta no MAPA" in html and "A mais no MAPA" in html and "1.586,99" in html and "Divergente" in html
    v2 = Verificacao.query.order_by(Verificacao.id.desc()).first()
    assert v2.status == "DIVERGENTE" and v2.resumo["totais"]["diff"] == round(55 - 1636.99, 2)

    # lista de registros mostra o badge da ultima conferencia
    assert "Divergente" in c.get("/comercializacao/").get_data(as_text=True)

    exp = c.get(f"/verificacao/{v2.id}/exportar")
    assert exp.status_code == 200 and exp.mimetype.endswith("spreadsheetml.sheet")
    wb = load_workbook(io.BytesIO(exp.data))
    assert wb.sheetnames == ["Divergências", "Todas as células", "Por UF", "Por produto"]
    assert wb["Divergências"].max_row == 3 and wb["Divergências"]["F2"].value == "Falta no MAPA"

    # o dono exclui a primeira conferencia
    assert c.post(f"/verificacao/{v.id}/excluir").status_code == 302 and Verificacao.query.count() == 1

    # outro usuario nao enxerga (por ultimo: o flask.g do contexto de teste guarda o usuario logado entre clientes)
    outro = Usuario(nome="x", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.USER)
    db.session.add(outro)
    db.session.commit()
    c2 = app.test_client()
    c2.post("/auth/login", data={"nome": "x", "senha": "pw"})
    assert c2.get(f"/verificacao/{v2.id}").status_code == 404
    assert c2.get(f"/verificacao/{v2.id}/exportar").status_code == 404
    assert c2.post(f"/verificacao/{v2.id}/excluir").status_code == 404
    assert Verificacao.query.count() == 1


def test_planilha_avulsa_usa_a_planilha_de_vendas_e_veta_termo_sem_vinculo(app, ctx):
    c, u = ctx
    ok = _envia(c, xlsx_relatorio_mapa([("BACON", "RS", 100.0), ("APRESUNTADO", "RS", 25739.29), ("APRESUNTADO", "AL", 0)]),
                registro="", vendas=(xlsx_vendas(LINHAS_OK[:2]), "vendas.xlsx"))
    assert ok.status_code == 302
    v = Verificacao.query.one()
    assert v.origem_base == "planilha_enviada" and v.arquivo_base == "vendas.xlsx"
    assert any("De→Para de hoje" in a for a in v.resumo["avisos"])

    ruim = _envia(c, xlsx_relatorio_mapa([("BACON", "RS", 1)]), registro="",
                  vendas=(xlsx_vendas([("PRODUTO FANTASMA", "999", "RS", 5)]), "v.xlsx"))
    assert ruim.status_code == 400 and "PRODUTO FANTASMA" in ruim.get_data(as_text=True)
    assert Verificacao.query.count() == 1


def test_erros_de_entrada_sao_explicados(app, ctx):
    c, u = ctx
    assert _envia(c, io.BytesIO(b"lixo"), registro="").status_code in (400,)
    sem_base = _envia(c, xlsx_relatorio_mapa([("BACON", "RS", 1)]), registro="")
    assert sem_base.status_code == 400 and "planilha de vendas" in sem_base.get_data(as_text=True)
    reg_sem_dados = _registro(u)
    r = _envia(c, xlsx_relatorio_mapa([("BACON", "RS", 1)]), registro=str(reg_sem_dados.id))
    assert r.status_code == 400 and "base de comparação" in r.get_data(as_text=True)
    assert _envia(c, xlsx_relatorio_mapa([("BACON", "RS", 1)]), registro="9999").status_code == 404


def test_relatorio_de_outro_sif_gera_aviso(app, ctx):
    c, u = ctx
    cred = MapaCredencial(owner_user_id=u.id, usuario_app="u", numero_sif="0167", especie="suino")
    cred.senha = "s"
    db.session.add(cred)
    reg = _registro(u, [])
    _job(reg, u, plano=PLANO)
    _envia(c, xlsx_relatorio_mapa([("BACON", "RS", 10)], controle="999"), registro=str(reg.id))
    assert any("999" in a and "SIF" in a for a in Verificacao.query.one().resumo["avisos"])
    _envia(c, xlsx_relatorio_mapa([("BACON", "RS", 10)], controle="167"), registro=str(reg.id))
    assert not any("SIF" in a for a in Verificacao.query.order_by(Verificacao.id.desc()).first().resumo["avisos"])   # 167 == 0167


def test_rotas_exigem_login(app):
    c = app.test_client()
    assert c.get("/verificacao/").status_code == 302 and c.post("/verificacao/nova").status_code == 302


def test_migration_cria_e_remove_a_tabela_a_partir_do_head_anterior(app):
    """Cobre o caminho `flask db upgrade` (o resto da suite usa create_all)."""
    from flask_migrate import downgrade, stamp, upgrade
    from sqlalchemy import inspect
    db.metadata.drop_all(bind=db.engine, tables=[db.metadata.tables["verificacao"]])
    assert "verificacao" not in inspect(db.engine).get_table_names()
    stamp(revision="e3c5a9b7d2f4")
    upgrade(revision="f7a1c9d3b5e2")
    cols = {c["name"] for c in inspect(db.engine).get_columns("verificacao")}
    assert {"user_id", "registro_id", "job_id", "origem_base", "status", "resumo", "resultado", "created_at"} <= cols
    downgrade(revision="e3c5a9b7d2f4")
    assert "verificacao" not in inspect(db.engine).get_table_names()
    upgrade(revision="f7a1c9d3b5e2")          # deixa o schema como o teardown espera
