import pytest

from app.extensions import db
from app.logic import constantes
from app.logic.comercializacao import (
    FINALIZAR_CMD, LIMPAR_CMD, VERIFICAR_CMD, build_commands, build_plano, totais,
)
from app.logic.constantes import ColetorPendencias, ConstanteNaoMapeada
from app.logic.js_literal import js_num, js_str
from app.models import ProdutoVenda, ProdutoVendaAlias


def rec(produto, uf, q):
    return {"produto": produto, "uf": uf, "quantidade": q}


def test_plano_agrupa_por_uf_e_aplica_de_para(app):
    plano = build_plano([
        rec("BACON", "RS", 10), rec("BACON", "AL", 5), rec("APRESUNTADO RESFRIADO", "RS", 2.5),
    ])
    assert [b["uf"] for b in plano] == ["AL", "RS"]
    assert plano[0]["index"] == 2 and plano[1]["index"] == 23          # AL=2, RS=23
    rs = plano[1]["itens"]
    assert {(i["descricao"], i["id"]) for i in rs} == {("Bacon", 17189), ("Apresuntado", 17178)}
    assert totais(plano) == {"ufs": 2, "itens": 3, "quantidade": 17.5}


def test_produtos_diferentes_com_mesmo_id_sao_lancados_separados(app):
    plano = build_plano([rec("LINGUICA TOSCANA CONGELADA", "AL", 10.5), rec("LINGUICA FRESCAL CONGELADA", "AL", 4.25)])
    itens = plano[0]["itens"]
    assert len(itens) == 2 and {i["id"] for i in itens} == {18152}
    assert {i["quantidade"] for i in itens} == {10.5, 4.25}


def test_sem_vinculo_vira_pendencia_e_nada_e_emitido(app):
    c = ColetorPendencias()
    plano = build_plano([rec("PRODUTO INEXISTENTE", "RS", 1), rec("BACON", "XX", 1), rec("BACON", "SC", 3)], c)
    pend = {(p["tipo"], p["valor"]) for p in c.listar()}
    assert pend == {("produto_venda", "PRODUTO INEXISTENTE"), ("estado_venda", "XX")}
    assert [b["uf"] for b in plano] == ["SC"]
    with pytest.raises(ConstanteNaoMapeada):
        build_plano([rec("PRODUTO INEXISTENTE", "RS", 1)])


def test_produto_inativo_nao_resolve_e_cache_invalida(app):
    p = ProdutoVenda.query.filter_by(nome="BACON").one()
    p.ativo = False
    db.session.commit()
    constantes.invalidar_cache()
    c = ColetorPendencias()
    build_plano([rec("BACON", "RS", 1)], c)
    assert not c.vazio


def test_alias_resolve_grafia_alternativa(app):
    p = ProdutoVenda.query.filter_by(nome="BACON").one()
    db.session.add(ProdutoVendaAlias(produto_id=p.id, alias="Bacon Defumado (kg)", alias_norm="bacon defumado kg"))
    db.session.commit()
    constantes.invalidar_cache()
    assert build_plano([rec("BACON DEFUMADO (KG)", "RS", 1)])[0]["itens"][0]["id"] == 17189


def test_comandos_ordem_hint_e_literais_seguros(app):
    p = ProdutoVenda.query.filter_by(nome="BACON").one()
    p.descricao_busca = "Bacon d'água \"especial\" </script>"
    db.session.commit()
    constantes.invalidar_cache()
    plano = build_plano([rec("BACON", "AL", 5), rec("BACON", "RS", 7)])
    cmds = build_commands(plano)
    assert cmds[0] == VERIFICAR_CMD and cmds[-1] == FINALIZAR_CMD
    assert cmds[1] == 'incluirEstadoVenda("AL", 2)'
    assert cmds[3] == 'incluirEstadoVenda("RS", 23)'
    assert cmds[4].startswith('incluirProdutoVenda("RS", 1, 7, ')            # dica = posicao do estado no plano
    assert "</script>" not in cmds[4] and "\\u003c/script\\u003e" in cmds[4]
    assert "d'água" in cmds[4] and '\\"especial\\"' in cmds[4]
    assert build_commands(plano, limpar=True)[0] == LIMPAR_CMD


def test_js_literal():
    assert js_str("a'b\"c\n") == '"a\'b\\"c\\n"'
    assert js_str(None) == '""'
    assert js_num(10.0) == "10" and js_num(10.25) == "10.25"
    with pytest.raises(ValueError):
        js_num(float("nan"))
