"""Conferencia linha a linha do registro lido do portal."""
import pytest

from app.logic import conferencia_portal as C


def it(uf, desc, q, produto=None, id_=1):
    return {"uf": uf, "descricao": desc, "quantidade": q, "produto": produto or desc.upper(), "id": id_}


def leitura(linhas, ufs=("MT", "TO", "RS")):
    """linhas: (uf, produto, qtd). Gera transacoes (id 100+i) e produtos, como o reader do JS."""
    ids = {uf: str(100 + i) for i, uf in enumerate(ufs)}
    return {"transacoes": [{"id": i, "uf": uf, "tipo_codigo": "V", "tipo": "Expedição"} for uf, i in ids.items()],
            "produtos": [{"transacao": ids[uf], "produto": p, "quantidade": q, "unidade": "Quilograma"} for uf, p, q in linhas],
            "tabela_produtos": True, "paginas": {"transacoes": 1, "produtos": 1}}


def test_tudo_certo_com_acentos_e_formato_do_portal():
    plano = [it("MT", "Linguica cozida", 100.5), it("TO", "Bacon", 88.33), it("TO", "Bacon", 1499.21, id_=2)]
    res = C.verificar_portal(plano, leitura([("MT", "LINGUIÇA COZIDA", 100.5), ("TO", "BACON", 1499.21), ("TO", "BACON", 88.330000)]))
    assert res["status"] == "OK" and res["linhas_diff"]["ok"] == 3
    assert res["totais"]["linhas_plano"] == res["totais"]["linhas_portal"] == 3
    assert "celulas" in res and res["celulas"]            # compativel com as telas de conferencia por celula


def test_caso_real_duas_linhas_faltando_sao_apontadas_exatamente():
    plano = [it("MT", "Apresuntado", 4688.7), it("MT", "Bacon", 1586.99, "BACON", 17189),
             it("TO", "Carne congelada de suino sem osso", 889.98, "CARNE CONGELADA SEM OSSO", 17347), it("TO", "Bacon", 88.33, id_=17186)]
    res = C.verificar_portal(plano, leitura([("MT", "APRESUNTADO", 4688.70), ("TO", "BACON", 88.33)]))
    d = res["linhas_diff"]
    assert res["status"] == "DIVERGENTE"
    assert [(f["uf"], f["quantidade"], f["id"]) for f in d["faltando"]] == [("MT", 1586.99, 17189), ("TO", 889.98, 17347)]
    assert d["sobrando"] == [] and d["diferentes"] == []
    assert [f["id"] for f in C.itens_a_complementar(d)] == [17189, 17347]


def test_quantidade_diferente_nao_vira_falta_mais_sobra():
    d = C.diferenca([it("RS", "Bacon", 100.0)], [{"uf": "RS", "produto": "BACON", "quantidade": 90.0, "transacao": "7"}])
    assert d["faltando"] == [] and d["sobrando"] == []
    assert d["diferentes"][0]["esperado"] == 100.0 and d["diferentes"][0]["portal"] == 90.0 and d["diferentes"][0]["diff"] == -10.0
    assert C.itens_a_complementar(d) == []                # corrigir quantidade exige excluir: nao e "complemento"


def test_linha_a_mais_no_portal_e_duplicidade():
    plano = [it("RS", "Bacon", 10.0)]
    d = C.diferenca(plano, [{"uf": "RS", "produto": "BACON", "quantidade": 10.0, "transacao": "1"},
                            {"uf": "RS", "produto": "BACON", "quantidade": 10.0, "transacao": "1"}])
    assert d["ok"] == 1 and d["sobrando"] == [{"uf": "RS", "produto": "BACON", "quantidade": 10.0, "transacao": "1"}]
    assert d["faltando"] == []


def test_multiconjunto_duas_linhas_iguais_no_plano_e_uma_so_no_portal():
    d = C.diferenca([it("RS", "Miudos", 600.0, id_=1), it("RS", "Miudos", 600.0, id_=1)],
                    [{"uf": "RS", "produto": "MIUDOS", "quantidade": 600.0, "transacao": "1"}])
    assert d["ok"] == 1 and len(d["faltando"]) == 1


def test_uf_vem_da_transacao_e_transacao_desconhecida_avisa():
    lei = leitura([("MT", "BACON", 1.0)])
    lei["produtos"].append({"transacao": "999", "produto": "SALAME", "quantidade": 2.0})
    linhas, avisos = C.linhas_do_portal(lei)
    assert linhas[0]["uf"] == "MT" and linhas[1]["uf"] == "?" and "1 linha" in avisos[0]


def test_leitura_invalida_e_erro_nao_divergencia():
    with pytest.raises(C.LeituraInvalida):
        C.linhas_do_portal({"transacoes": []})
    with pytest.raises(C.LeituraInvalida, match="tabela de produtos"):
        C.linhas_do_portal({"transacoes": [], "produtos": [], "tabela_produtos": False})
    with pytest.raises(C.LeituraInvalida, match="Quantidade ilegível"):
        C.linhas_do_portal(leitura([("MT", "BACON", None)]))


def test_ambiguidade_e_avisada():
    plano = [it("PB", "Bacon", 97.42, id_=17189), it("PB", "Bacon", 97.42, id_=17186)]
    res = C.verificar_portal(plano, leitura([("MT", "BACON", 1)], ufs=("MT",)))
    assert any("não distingue" in a for a in res["avisos"]) or C.ambiguas(plano)
