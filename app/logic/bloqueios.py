"""Veto de lancamentos: junta, em uma lista unica e legivel, TUDO o que impede o envio ao MAPA.

Regra do sistema: nenhum lancamento sai com constante sem vinculo, credencial ausente/ilegivel/incompleta
ou tipo de lancamento inativo. O preview mostra os motivos (e desabilita o botao); `finalizar` e o
retry de job refazem a checagem no servidor, porque a tela pode estar velha.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from markupsafe import Markup, escape

from app.logic.constantes import ColetorPendencias


def avaliar_credencial(user_id: int, especie: Optional[str] = None, *, api: bool = False) -> Tuple[object, List[str]]:
    """(credencial|None, motivos). Em modo API tambem exige os dados do estabelecimento."""
    from app.mapa_api.montagem import credencial_do_usuario
    from app.mapa_api import payloads

    cred = credencial_do_usuario(user_id, especie)
    if cred is None:
        return None, ["Credenciais do MAPA não cadastradas (Configurações › Credenciais MAPA)."]
    motivos: List[str] = []
    try:
        if not cred.senha:
            motivos.append("A senha do MAPA salva está vazia: salve a credencial de novo.")
    except Exception:
        motivos.append("Não foi possível ler a senha do MAPA salva (a FERNET_KEY do servidor mudou ou falta). "
                       "Salve a credencial de novo em Configurações › Credenciais MAPA.")
    if not (cred.usuario_app or "").strip() or not (cred.numero_sif or "").strip():
        motivos.append("Credencial sem usuário ou número SIF.")
    if api:
        try:
            payloads.estabelecimento(cred)
        except payloads.DadosIncompletos as e:
            motivos.append("Dados do estabelecimento incompletos para o webservice: " + ", ".join(e.faltando) + ".")
    return cred, motivos


def motivos_pendencias(coletor: Optional[ColetorPendencias]) -> List[str]:
    """Pendencias de constante -> frases curtas (a tabela detalhada fica na tela de pendencias)."""
    if coletor is None:
        return []
    out = []
    for p in coletor.listar():
        out.append(f"{p['rotulo']}: {p['valor']!r} ({p['ocorrencias']}×)")
    return out


def resumo_flash(titulo: str, motivos: List[str]) -> Markup:
    """HTML seguro (flash e renderizado com |safe): titulo + lista de motivos, tudo escapado."""
    itens = "".join(f"<li>{escape(m)}</li>" for m in motivos[:12])
    mais = f"<li>… e mais {len(motivos) - 12}</li>" if len(motivos) > 12 else ""
    return Markup(f"{escape(titulo)}<ul class=\"lista-motivos\">{itens}{mais}</ul>")


UF_ORIGEM_PADRAO = 23   # indice de RS no select de UF do portal (comportamento legado)


def uf_origem_index(cred) -> Tuple[int, Optional[str]]:
    """
    Indice (select do portal) da UF de ORIGEM das GTAs. A planilha de GTA nao traz UF, entao usa a UF do
    estabelecimento (credencial) vinculada em Admin > Constantes > Estados. Sem isso cai no padrao legado
    (RS) mas AVISA, para que a suposicao nunca seja silenciosa.
    """
    from app.logic.constantes import ConstanteNaoMapeada, resolve_estado_venda
    uf = (getattr(cred, "cod_uf", "") or "").strip()
    if uf:
        try:
            return resolve_estado_venda(uf)["index"], None
        except ConstanteNaoMapeada:
            motivo = f"a UF {uf} da credencial não está cadastrada em Constantes › Estados"
    else:
        motivo = "a credencial não informa a UF do estabelecimento"
    return UF_ORIGEM_PADRAO, (f"UF de origem das GTAs assumida como RS (índice {UF_ORIGEM_PADRAO}) porque {motivo}. "
                              "Confira antes de confirmar: GTAs de outra UF seriam lançadas como RS.")
