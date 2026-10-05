import pytest

from app.extensions import db
from app.logic import constantes
from app.logic.comercializacao import (
    FINALIZAR_CMD, build_commands, build_plano, para_limpar, totais,
)
from app.logic.constantes import ColetorPendencias, ConstanteNaoMapeada
from app.logic.js_literal import js_num, js_str
from app.models import ProdutoVenda, ProdutoVendaAlias


VENDA = {"rotulo": "Venda", "tipo_transacao_idx": 1, "ambito_idx": 1, "operador_idx": 2}


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
    cmds = build_commands(plano, tipo=VENDA, rotulos=["Recebimento"])
    assert cmds[0] == 'verificarRegistroVazio("Venda", ["Recebimento", "Venda"])' and cmds[-1] == FINALIZAR_CMD
    cfg = '{"tipo": 1, "ambito": 1, "operador": 2, "rotulo": "Venda"}'
    assert cmds[1] == f'incluirEstadoVenda("AL", 2, {cfg})'
    assert cmds[3] == f'incluirEstadoVenda("RS", 23, {cfg})'
    assert cmds[4].startswith('incluirProdutoVenda("RS", 1, 7, ')            # dica = posicao do estado no plano
    assert cmds[4].endswith(', 17189, "Venda")')
    assert "</script>" not in cmds[4] and "\\u003c/script\\u003e" in cmds[4]
    assert "d'água" in cmds[4] and '\\"especial\\"' in cmds[4]
    limpar = build_commands(plano, tipo=VENDA, limpar=True)[0]
    assert limpar == 'limparTransacoes("Venda", ["Venda"])' == para_limpar(cmds[0].replace('"Recebimento", ', ''))


def test_tipo_recebimento_usa_suas_opcoes(app):
    rec_t = {"rotulo": "Recebimento", "tipo_transacao_idx": 2, "ambito_idx": 1, "operador_idx": 3}
    cmds = build_commands(build_plano([rec("BACON", "RS", 7)]), tipo=rec_t, rotulos=["Venda", "Recebimento"])
    assert '"tipo": 2' in cmds[1] and '"operador": 3' in cmds[1] and '"rotulo": "Recebimento"' in cmds[1]
    assert cmds[2].endswith('"Recebimento")')


def test_js_literal():
    assert js_str("a'b\"c\n") == '"a\'b\\"c\\n"'
    assert js_str(None) == '""'
    assert js_num(10.0) == "10" and js_num(10.25) == "10.25"
    with pytest.raises(ValueError):
        js_num(float("nan"))
