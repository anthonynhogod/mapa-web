"""Abate pelo webservice: linhas planas, pendencias de id da API, avisos e job."""
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.logic import constantes
from app.logic.constantes import ColetorPendencias, ConstanteNaoMapeada
from app.mapa_api import montagem, payloads
from app.models import (
    Destino, DifTmp, Diagnostico, EspecieApi, ExecJob, GtaTemp, MapaCredencial, ParteAfetada, Registro,
    Role, SifTmp, UploadStatus, Usuario,
)
from app.seeds.run import seed_abate


@pytest.fixture()
def cenario(app):
    seed_abate()
    u = Usuario(nome="op", senha=generate_password_hash("pw", method="pbkdf2:sha256"), role=Role.USER)
    db.session.add(u)
    db.session.flush()
    c = MapaCredencial(owner_user_id=u.id, usuario_app="usr", numero_sif="167", especie="suino",
                       cpf_cnpj="12345678000199", ambito="SIF", cod_uf="RS", cod_municipio_ibge="4314902")
    c.senha = "x"
    db.session.add(c)
    reg = Registro(data=date(2026, 3, 5), especie="suino", user_id=u.id)
    db.session.add(reg)
    db.session.flush()
    db.session.add(GtaTemp(numero=830271, serie="A", machos=65, femeas=0, lote=4, peso=111.19, tipo="A", registro_id=reg.id))
    rec = {"lote": 4, "descricao": "ABCESSO", "parte afetada": "Carcaça", "destino": "Liberado",
           "quantidade": 3, "emergencia": 0}
    meta = {"errors": [], "warnings": [], "counts": {"output_records": 1}}
    for M, nome in ((DifTmp, "dif"), (SifTmp, "sif")):
        db.session.add(M(registro_id=reg.id, filename=f"{nome}.xlsx", model="padrao", uploaded_by=u.id,
                         status=UploadStatus.VALIDATED, payload={"records": [rec], "meta": meta}))
    db.session.commit()
    return u, reg


def _ids(diag=11, parte=22, destino=33, especie=7):
    Diagnostico.query.filter_by(descricao_mapa="ABSCESSO (MAMÍFEROS)").one().id_api = diag
    ParteAfetada.query.filter_by(nome="Carcaça").one().id_api = parte
    Destino.query.filter_by(nome="Liberado").one().id_api = destino
    EspecieApi.query.filter_by(nome="suino").one().id_api = especie
    db.session.commit()
    constantes.invalidar_cache()


def test_payload_abate_linhas_planas(app, cenario):
    u, reg = cenario
    _ids()
    gta = [{"numero_gta": 830271, "serie": "A", "machos": 65, "femeas": 0, "total": 65, "lote": 4, "peso_medio": 111.19}]
    dif = [{"lote": 4, "descricao": "ABCESSO", "parte afetada": "Carcaça", "destino": "Liberado", "quantidade": 3, "emergencia": 0}]
    prep = montagem.preparar_abate(reg, gta, dif, [])
    assert prep["servico"] == "abate" and prep["metodo"] == "POST" and prep["avisos"] == []
    linhas = prep["payload"]
    assert len(linhas) == 1                     # 1 GTA x 1 lote x 1 diagnostico x 1 parte x 1 destino
    l = linhas[0]
    assert l == {
        "numero": "167", "ambito": "SIF", "cpf_cnpj": "12345678000199", "cod_uf": "RS", "cod_municipio_ibge": "4314902",
        "data_abate": "05/03/2026", "abate": "S", "nr_gta": 830271, "cdSerie": "A", "especie": 7,
        "quantidadeMachos": 65, "quantidadeFemeas": 0, "numeroLote": 4, "tipoLote": "NO",
        "pesoMortoMacho": 7227.35, "pesoMortoFemea": 0.0,
        "diagnostico": 11, "quantidadeAnimaisAcometidos": 3, "parteAfetada": 22,
        "quantidadePartesAfetadas": 3, "destinoCondenacao": 33,
    }


def test_pendencias_de_id_da_api_e_credencial(app, cenario):
    u, reg = cenario                              # nenhum id_api preenchido
    gta = [{"numero_gta": 1, "serie": "AC", "machos": 5, "femeas": 0, "total": 5, "lote": 4, "peso_medio": 100.0}]
    dif = [{"lote": 4, "descricao": "ABCESSO", "parte afetada": "Carcaça", "destino": "Liberado", "quantidade": 1, "emergencia": 0}]
    c = ColetorPendencias()
    prep = montagem.preparar_abate(reg, gta, dif, [], c)
    tipos = {p["tipo"] for p in c.listar()}
    assert {"diagnostico_api", "parte_api", "destino_api", "especie_api"} <= tipos
    assert [l for l in prep["payload"] if "diagnostico" in l] == []          # nada incompleto vai ao corpo
    with pytest.raises(ConstanteNaoMapeada):
        montagem.preparar_abate(reg, gta, dif, [])

    _ids()
    assert any("série" in a for a in montagem.preparar_abate(reg, gta, dif, [])["avisos"])   # 'AC' != 1 letra

    MapaCredencial.query.one().cpf_cnpj = None
    db.session.commit()
    c = ColetorPendencias()
    montagem.preparar_abate(reg, gta, dif, [], c)
    assert ("credencial", "CPF/CNPJ (11 ou 14 dígitos)") in {(p["tipo"], p["valor"]) for p in c.listar()}
    with pytest.raises(payloads.DadosIncompletos):
        montagem.preparar_abate(reg, gta, dif, [])


def test_preview_e_finalizar_abate_em_modo_api(app, cenario):
    u, reg = cenario
    _ids()
    cl = app.test_client()
    assert cl.post("/auth/login", data={"nome": "op", "senha": "pw"}).status_code == 302
    r = cl.get(f"/registros/{reg.id}/preview/geral?gta_source=manual&confirm_mismatch=yes")
    html = r.get_data(as_text=True)
    import html as h
    assert r.status_code == 200 and "webservice do MAPA" in html
    assert "POST /abate" in h.unescape(html) and '"diagnostico": 11' in h.unescape(html)

    r = cl.post(f"/registros/{reg.id}/finalizar", data={"gta_source": "manual"})
    assert r.status_code == 302
    job = ExecJob.query.one()
    assert job.status == "ESPERA" and job.meta["backend"] == "api" and job.meta["servico"] == "abate"
    assert job.commands == ["POST /abate"] and job.meta["payload"][-1]["destinoCondenacao"] == 33
    assert db.session.get(Registro, reg.id).status == "PT"
