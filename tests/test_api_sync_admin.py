import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.logic import constantes
from app.mapa_api import sync
from app.models import (
    Destino, Diagnostico, EspecieApi, MapaCredencial, ParteAfetada, ProdutoVenda, Role, Usuario,
)
from app.seeds.run import seed_abate


class CliFalso:
    ambiente = "homologacao"

    def __init__(self, cats):
        self.cats = cats

    def catalogo(self, nome, usar_cache=True):
        return self.cats[nome]


CATS = {
    "especies": [{"Id": 3, "nomeEspecie": "Suínos"}, {"Id": 4, "nomeEspecie": "Bovinos"}],
    "diagnosticos": [{"id": 11, "nome": "ABSCESSO (MAMÍFEROS)"}, {"id": 12, "nome": "CAQUEXIA"}, {"id": 13, "nome": "Inexistente aqui"}],
    "partes": [{"id": 21, "nome": "Carcaça"}, {"id": 22, "nome": "carcaca"}],           # ambiguo: 2 ids p/ mesma chave
    "destinos": [{"id": 31, "nome": "Liberado"}],
    "produtos": [{"cod_produto": 501, "produto": "BACON"}, {"cod_produto": 502, "produto": "Outro produto"}],
}


def test_sincronizar_preenche_vazios_sem_sobrescrever(app):
    seed_abate()
    Diagnostico.query.filter_by(descricao_mapa="CAQUEXIA").one().id_api = 999        # informado a mao
    db.session.commit()
    constantes.invalidar_cache()

    rel = sync.sincronizar(CliFalso(CATS))
    db.session.expire_all()

    assert EspecieApi.query.filter_by(nome="suino").one().id_api == 3
    assert Diagnostico.query.filter_by(descricao_mapa="ABSCESSO (MAMÍFEROS)").one().id_api == 11
    assert Diagnostico.query.filter_by(descricao_mapa="CAQUEXIA").one().id_api == 999         # preservado
    assert ParteAfetada.query.filter_by(nome="Carcaça").one().id_api is None                  # ambiguo -> nao chuta
    assert any("Carcaça" in x and "ambíguo" in x for x in rel["partes"]["sem_correspondencia"])
    assert Destino.query.filter_by(nome="Liberado").one().id_api == 31
    assert ProdutoVenda.query.filter_by(nome="BACON").one().cod_api == 501
    assert rel["diagnosticos"]["ja_preenchidos"] == 1 and rel["produtos"]["total"] == 2


def test_catalogo_sem_campo_de_id_e_reportado(app):
    seed_abate()
    cats = dict(CATS, diagnosticos=[{"nome": "ABSCESSO (MAMÍFEROS)", "situacao": "A"}])
    rel = sync.sincronizar(CliFalso(cats))
    assert rel["diagnosticos"]["sem_id"] is True and rel["diagnosticos"]["amostra"]
    assert Diagnostico.query.filter(Diagnostico.id_api.isnot(None)).count() == 0


def test_extrair_formatos():
    assert sync.extrair({"cod_produto": 5, "produto": "X"}) == (5, "X")
    assert sync.extrair({"Id": "7", "nomeEspecie": "Suino"}) == (7, "Suino")
    assert sync.extrair({"nome": "A"}) == (None, "A")
    assert sync.extrair("x") == (None, None)


@pytest.fixture()
def admin(app):
    u = Usuario(nome="adm", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.ADMIN)
    db.session.add(u)
    db.session.flush()
    c = MapaCredencial(owner_user_id=u.id, usuario_app="u", numero_sif="1", especie="suino")
    c.senha = "x"
    db.session.add(c)
    db.session.commit()
    cl = app.test_client()
    cl.post("/auth/login", data={"nome": "adm", "senha": "pw"})
    return cl


def test_admin_api_mapa_paginas(app, admin, monkeypatch):
    seed_abate()
    html = admin.get("/admin/api-mapa").get_data(as_text=True)
    assert "homologacao" in html and "Sincronizar catálogos" in html and "suino" in html

    from app.blueprints.admin import api_mapa
    monkeypatch.setattr(api_mapa, "cliente", lambda cred: CliFalso(CATS))
    html = admin.post("/admin/api-mapa/sincronizar").get_data(as_text=True)
    assert "Resultado da sincronização" in html and "ABSCESSO (MAMÍFEROS) -&gt; 11" in html
    html = admin.get("/admin/api-mapa/catalogo/produtos").get_data(as_text=True)
    assert "501" in html and "BACON" in html
    assert admin.get("/admin/api-mapa/catalogo/xyz").status_code == 302

    esp = EspecieApi.query.one()
    admin.post(f"/admin/api-mapa/especie/{esp.id}", data={"id_api": "9"})
    db.session.expire_all()
    assert db.session.get(EspecieApi, esp.id).id_api == 9
    admin.post(f"/admin/api-mapa/especie/{esp.id}", data={"id_api": "abc"})
    db.session.expire_all()
    assert db.session.get(EspecieApi, esp.id).id_api == 9


def test_admin_constantes_com_ids_da_api_opcionais(app, admin):
    html = admin.get("/admin/constantes/produtos-venda").get_data(as_text=True)
    assert "Código API" in html and "cst-tabela" in html
    assert "cod_produto na API" in admin.get("/admin/constantes/produtos-venda/novo").get_data(as_text=True)
    r = admin.post("/admin/constantes/produtos-venda/novo", data={
        "nome": "PRODUTO NOVO", "descricao_busca": "x", "cod_api": "321", "id_mapa": "", "ativo": "1"})
    assert r.status_code == 302
    p = ProdutoVenda.query.filter_by(nome="PRODUTO NOVO").one()
    assert (p.cod_api, p.id_mapa) == (321, None)
    r = admin.post("/admin/constantes/partes/novo", data={"nome": "Parte X", "id_api": "55", "id_mapa": "", "ativo": "1"})
    assert ParteAfetada.query.filter_by(nome="Parte X").one().id_api == 55
    admin.post("/admin/constantes/partes/novo", data={"nome": "Parte Y", "id_api": "-3", "ativo": "1"})
    assert ParteAfetada.query.filter_by(nome="Parte Y").count() == 0
