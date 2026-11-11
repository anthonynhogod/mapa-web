"""O gerador de comandos do ABATE nao pode quebrar (nem injetar JS) com aspas nos textos."""
from app.extensions import db
from app.logic import constantes
from app.logic.preview_utils import build_commands, build_legacy_structure_from_new, merge_diagnostics
from app.models import Diagnostico, DiagnosticoAlias, Destino, DestinoAlias, ParteAfetada, ParteAfetadaAlias


def test_comandos_abate_com_aspas_viram_literal_valido(app):
    d = Diagnostico(descricao_mapa="LESÃO D'ÁGUA \"X\"", ativo=True)
    p = ParteAfetada(nome="Carcaça", id_mapa=1, ativo=True)
    de = Destino(nome="Liberado", id_mapa=1, ativo=True)
    db.session.add_all([d, p, de])
    db.session.flush()
    db.session.add_all([
        DiagnosticoAlias(diagnostico_id=d.id, alias="lesao dagua x", alias_norm="lesao dagua x"),
        ParteAfetadaAlias(parte_id=p.id, alias="Carcaça", alias_norm="carcaca"),
        DestinoAlias(destino_id=de.id, alias="Liberado", alias_norm="liberado"),
    ])
    db.session.commit()
    constantes.invalidar_cache()

    gtas = [{"numero_gta": 1, "serie": "A'B", "machos": 5, "femeas": 0, "lote": 1, "peso_medio": 100.0}]
    dif = [{"lote": 1, "descricao": "lesao dagua x", "parte afetada": "carcaca", "destino": "liberado",
            "quantidade": 2, "emergencia": 0}]
    estrutura = build_legacy_structure_from_new(gtas, merge_diagnostics(dif, []))
    cmds = build_commands(estrutura)
    assert cmds[0] == 'incluirGta(23, 1, "A\'B", 5, 0)'
    diag = next(c for c in cmds if c.startswith("incluirDiagnostico"))
    assert diag == 'incluirDiagnostico(0, "LESÃO D\'ÁGUA \\"X\\"", 2)'
    assert cmds[-1] == "finalizarRegistro()"
