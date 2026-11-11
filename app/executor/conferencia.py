"""Jobs que LEEM o registro no portal (conferência) e que COMPLEMENTAM o que falta (só incluem, nunca excluem).

- tipo_job "conferencia": só lê (`lerRegistroComercializacao`). Não cria o registro, não altera nada no portal e não mexe no
  status do Registro; no fim vira uma Verificacao (fonte "portal").
- tipo_job "complemento": lê, calcula o que FALTA (linhas do plano ausentes no portal), inclui só essas linhas, salva e lê
  de novo; a leitura final vira uma Verificacao. Os comandos são montados DEPOIS de uma leitura fresca (idempotente: se
  alguém já incluiu a linha, ela não é incluída de novo).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.logic import conferencia_portal as C
from app.logic.js_literal import js_json, js_num, js_str

TIPOS_JOB = ("conferencia", "complemento")
LER = "lerRegistroComercializacao()"
FINALIZAR = "finalizarRegistroComercializacao()"


def tipo_job(job) -> Optional[str]:
    t = (job.meta or {}).get("tipo_job")
    return t if t in TIPOS_JOB else None


def status_registro(job, novo: str) -> str:
    """Jobs de conferência/complemento NÃO mudam o status do Registro (ele continua o do lançamento original)."""
    if tipo_job(job):
        return (job.meta or {}).get("status_original") or novo
    return novo


def captura_leitura(job, cmd: str, resultado: Any) -> Optional[str]:
    """Guarda em job.meta o JSON devolvido por lerRegistroComercializacao(). Devolve mensagem de erro ou None."""
    if not str(cmd).startswith("lerRegistroComercializacao"):
        return None
    if not isinstance(resultado, str) or not resultado.startswith("OK: "):
        return None                                           # o erro ja entra em job.errors pelo fluxo normal
    try:
        dados = json.loads(resultado[4:])
    except ValueError:
        return "A leitura do portal veio ilegível (JSON inválido)."
    meta = dict(job.meta or {})
    meta.setdefault("leitura_antes", dados)
    meta["leitura"] = dados
    job.meta = meta
    return None


def _cmd_estado(uf: str, indice: int, cfg: Dict[str, Any]) -> str:
    return f"incluirEstadoVenda({js_str(uf)}, {js_num(indice)}, {js_json(cfg)})"


def comandos_complemento(faltando: List[Dict[str, Any]], ufs_existentes, tipo: Dict[str, Any], rotulo: str,
                         indices_uf: Dict[str, int]) -> List[str]:
    """Só INCLUI: estado (se a UF ainda não tem transação) e as linhas de produto que faltam."""
    cfg = {"tipo": int(tipo["tipo_transacao_idx"]), "ambito": int(tipo["ambito_idx"]),
           "operador": int(tipo["operador_idx"]), "rotulo": tipo["rotulo"]}
    cmds: List[str] = []
    for uf in sorted({f["uf"] for f in faltando}):
        if uf not in ufs_existentes:
            cmds.append(_cmd_estado(uf, indices_uf[uf], cfg))
        for f in [x for x in faltando if x["uf"] == uf]:
            # hint -1: a linha da UF e achada pelo TEXTO (o indice pode ter mudado)
            cmds.append(f"incluirProdutoVenda({js_str(uf)}, -1, {js_num(f['quantidade'])}, "
                        f"{js_str(f['descricao'])}, {js_num(f['id'])}, {js_str(rotulo)})")
    return cmds


def preparar_complemento(job, nav) -> Optional[str]:
    """Lê o portal AGORA, calcula o que falta e monta os comandos do job. Devolve mensagem de erro ou None."""
    from app.logic.comercializacao import tipo_config
    from app.logic.constantes import ConstanteNaoMapeada, resolve_estado_venda
    from app.models import TipoLancamento

    meta = dict(job.meta or {})
    itens = meta.get("plano_base") or []
    if not itens:
        return "O job não tem o plano base para comparar."

    resultado = nav.executar_comando(LER, ctx="leitura_previa")
    if not (isinstance(resultado, str) and resultado.startswith("OK: ")):
        return f"Não foi possível ler o registro no portal antes de complementar: {resultado}"
    erro = captura_leitura(job, LER, resultado)
    if erro:
        return erro
    dados = job.meta["leitura"]
    try:
        linhas, _ = C.linhas_do_portal(dados)
    except C.LeituraInvalida as e:
        return str(e)
    dif = C.diferenca(itens, linhas)
    faltando = C.itens_a_complementar(dif)

    tipo = TipoLancamento.query.filter_by(codigo=meta.get("lancamento") or "venda", ativo=True).first()
    if tipo is None:
        return f"O tipo de lançamento {meta.get('lancamento')!r} não está ativo."
    cfg = tipo_config(tipo)
    existentes = {t["uf"] for t in dados["transacoes"]}
    indices: Dict[str, int] = {}
    for uf in {f["uf"] for f in faltando if f["uf"] not in existentes}:
        try:
            indices[uf] = resolve_estado_venda(uf)["index"]
        except ConstanteNaoMapeada:
            return f"A UF {uf} não tem vínculo em Admin › Constantes › Estados; não dá para incluir a transação."

    cmds = comandos_complemento(faltando, existentes, cfg, cfg["rotulo"], indices)
    if cmds:
        cmds += [FINALIZAR]
    cmds += [LER]                                             # leitura final = conferência do resultado
    meta = dict(job.meta or {})
    meta["complemento"] = {"faltando": len(faltando), "diferentes": len(dif["diferentes"]),
                           "sobrando": len(dif["sobrando"]), "ja_ok": dif["ok"]}
    job.meta = meta
    job.commands = cmds
    job.progress = 0
    return None


def concluir(job, registro) -> Optional[str]:
    """Transforma a última leitura em Verificacao (fonte portal). Devolve mensagem de erro ou None."""
    from app.extensions import db
    from app.models import Verificacao

    meta = dict(job.meta or {})
    leitura, itens = meta.get("leitura"), meta.get("plano_base") or []
    if not leitura:
        return "O job terminou sem uma leitura do portal."
    try:
        res = C.verificar_portal(itens, leitura)
    except C.LeituraInvalida as e:
        return str(e)
    avisos = list(res.pop("avisos", [])) + list(meta.get("avisos_base") or [])
    v = Verificacao(
        user_id=job.owner_user_id, registro_id=registro.id, job_id=meta.get("job_base"),
        origem_base=meta.get("origem_base") or "job_plano", arquivo_mapa="(lido direto do portal)", arquivo_base=None,
        status=res["status"],
        resumo={"fonte": "portal", "tipo_job": tipo_job(job), "job_conferencia": job.id, "totais": res["totais"],
                "contagem": res["contagem"], "avisos": avisos, "ignoradas": {}, "controles": [],
                "linhas_relatorio": res["totais"]["linhas_portal"], "itens_enviados": res["totais"]["linhas_plano"],
                "complemento": meta.get("complemento"), "lido_em": leitura.get("lido_em"),
                "paginas": leitura.get("paginas")},
        resultado={"celulas": res["celulas"], "por_uf": res["por_uf"], "por_produto": res["por_produto"],
                   "linhas_diff": res["linhas_diff"]},
    )
    db.session.add(v)
    db.session.flush()
    meta["verificacao_id"] = v.id
    job.meta = meta
    faltam = len(res["linhas_diff"]["faltando"])
    if tipo_job(job) == "complemento" and faltam:
        # o complemento disse OK em cada comando, mas a leitura final mostra que ainda faltam linhas: NAO e sucesso
        return (f"Depois de complementar ainda faltam {faltam} linha(s) no portal (conferência #{v.id}). "
                "Os comandos retornaram OK, mas as linhas não aparecem na tabela do portal.")
    return None


def encadear_conferencia(job, registro, config) -> Optional[int]:
    """
    Depois de um LANCAMENTO de comercializacao concluido, enfileira uma leitura do portal (somente leitura) para conferir
    linha a linha o que foi realmente gravado, sem o operador precisar pedir. Devolve o id do novo job (ou None).
    """
    meta = job.meta or {}
    if tipo_job(job) or meta.get("backend") == "api" or registro.tipo != "comercializacao":
        return None
    if not config.get("AUTO_CONFERIR", True):
        return None
    itens = C_itens(meta.get("plano"))
    if not itens:
        return None                                            # job antigo, sem o plano gravado
    from app.extensions import db
    from app.models import ExecJob
    novo = ExecJob(
        registro_id=registro.id, owner_user_id=job.owner_user_id, gta_source="conferencia", commands=[LER],
        status="ESPERA", progress=0, errors=[],
        meta={"modulo": "comercializacao", "tipo_job": "conferencia", "automatica": True, "lancamento": meta.get("lancamento"),
              "numero_sif": meta.get("numero_sif"), "status_original": registro.status, "plano_base": itens,
              "origem_base": "job_plano", "job_base": job.id, "avisos_base": [], "periodo": meta.get("periodo")},
    )
    db.session.add(novo)
    db.session.flush()
    return novo.id


def C_itens(plano):
    from app.logic.verificacao import itens_do_plano
    return itens_do_plano(plano)
