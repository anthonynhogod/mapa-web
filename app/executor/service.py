# app/executor/service.py
from pathlib import Path
from datetime import datetime
from flask import current_app
from app import db
from app.models import ExecJob
from app.logic.preview_utils import merge_diagnostics, build_commands, build_legacy_structure_from_new, normalize_gta_manual

def cleanup_tmp_folder(registro_id: int):
    base = Path(current_app.config.get("UPLOAD_TMP_DIR", "uploads/tmp"))
    target = base / str(registro_id)
    if not target.exists():
        return
    # remove arquivos
    for p in target.rglob("*"):
        try:
            if p.is_file():
                p.unlink()
        except Exception:
            pass
    # remove dirs (de baixo pra cima)
    for p in sorted(target.rglob("*"), reverse=True):
        try:
            if p.is_dir():
                p.rmdir()
        except Exception:
            pass
    try:
        target.rmdir()
    except Exception:
        pass

def create_api_job(registro, prep: dict, totais, gta_source: str = "api", extra_meta: dict | None = None) -> ExecJob:
    """Job de envio via webservice: o corpo JSON ja montado vai em meta.payload."""
    from app.mapa_api.montagem import descricao_requisicao
    job = ExecJob(
        registro_id=registro.id,
        owner_user_id=registro.user_id,
        gta_source=gta_source,
        commands=[descricao_requisicao(prep)],
        meta={"backend": "api", "servico": prep["servico"], "payload": prep["payload"], "totais": totais,
              **(extra_meta or {})},
        status="ESPERA", progress=0, errors=[],
    )
    db.session.add(job)
    registro.status = "PT"
    db.session.commit()
    cleanup_tmp_folder(registro.id)
    return job


def create_job_and_finalize(registro, gta_source: str, gta_records, dif_records, sif_records, totais) -> ExecJob:
    """
    - Gera comandos a partir dos datasets (sem revalidar nada — isso já foi feito no preview)
    - Cria ExecJob (ESPERA)
    - Seta registro.status = 'PT'
    - Limpa uploads/tmp/<registro_id>
    """
    # 1) Gera plano de comandos
    # Sem coletor de propósito: aqui qualquer constante sem vínculo levanta
    # ConstanteNaoMapeada e aborta a criação do job. É a última trava — o
    # preview já deveria ter barrado, mas nenhum comando pode ir ao portal
    # com diagnóstico cru ou id 0.
    from app.mapa_api.montagem import preparar_abate, usa_api
    if usa_api():
        # sem coletor: qualquer constante/credencial pendente levanta (ultima trava)
        prep = preparar_abate(registro, gta_records, dif_records, sif_records)
        return create_api_job(registro, prep, totais, gta_source)

    from app.logic.bloqueios import uf_origem_index
    from app.mapa_api.montagem import credencial_do_usuario
    merged_diag = merge_diagnostics(dif_records, sif_records)
    uf_idx, _aviso = uf_origem_index(credencial_do_usuario(registro.user_id, registro.especie))
    estrutura_lotes = build_legacy_structure_from_new(gta_records, merged_diag, uf_index_default=uf_idx)
    comandos = build_commands(estrutura_lotes)
    # 2) Cria job
    job = ExecJob(
        registro_id=registro.id,
        owner_user_id=registro.user_id,
        gta_source=gta_source,
        commands=comandos,
        meta={"totais": totais},
        status="ESPERA",
        progress=0,
        errors=[],
        started_at=None,
        finished_at=None,
    )
    db.session.add(job)

    # 3) Trava o registro (PT) e limpa tmp
    registro.status = "PT"
    db.session.commit()  # commit antes da limpeza de disco (seguro)

    cleanup_tmp_folder(registro.id)
    return job


class Bloqueado(Exception):
    """O lancamento nao pode ser (re)enviado; `motivos` explica por que (lista legivel)."""

    def __init__(self, motivos):
        self.motivos = list(motivos)
        super().__init__("; ".join(self.motivos))


def reconstruir_job(job: ExecJob, registro, *, limpar: bool = False) -> None:
    """
    Refaz os comandos/payload do job a partir dos dados guardados e do De->Para ATUAL, ou levanta
    `Bloqueado` com todos os motivos. E o que o retry usa: os ids/descricoes gravados no job podem
    estar velhos (constante alterada/desativada/removida desde a criacao).
    """
    from app.logic.bloqueios import avaliar_credencial, motivos_pendencias, uf_origem_index
    from app.logic.constantes import ColetorPendencias

    meta = dict(job.meta or {})
    api = meta.get("backend") == "api"
    coletor = ColetorPendencias()
    cred, motivos = avaliar_credencial(job.owner_user_id, registro.especie)

    if registro.tipo == "producao":
        # sempre pelo navegador (o webservice nao cobre a producao); o De->Para e reavaliado agora
        from app.logic.producao import build_commands as prod_cmds, build_plano as prod_plano, totais as prod_totais
        from app.models import VendasTmp
        tmp = VendasTmp.query.filter_by(registro_id=registro.id).one_or_none()
        records = ((tmp.payload or {}).get("records") if tmp else None) or []
        if not records:
            motivos.append("Os dados da planilha deste registro não existem mais: envie a planilha de novo.")
            raise Bloqueado(motivos + motivos_pendencias(coletor))
        plano = prod_plano(records, coletor)
        if not plano and coletor.vazio:
            motivos.append("Nenhum produto com quantidade maior que zero para lançar.")
        motivos += motivos_pendencias(coletor)
        if motivos:
            raise Bloqueado(motivos)
        sif = meta.get("numero_sif") or (cred.numero_sif if cred else "")
        competencia = registro.periodo_ini.strftime("%m/%Y")
        job.commands = prod_cmds(plano, competencia=competencia, numero_sif=sif)
        meta["totais"] = prod_totais(plano)
        meta["plano"] = plano
        job.meta = meta
        return
    if registro.tipo == "comercializacao":
        from app.logic.comercializacao import build_commands as com_cmds, build_plano, rotulos_ativos, tipo_config, totais
        from app.mapa_api.montagem import preparar_comercializacao
        from app.models import TipoLancamento, VendasTmp
        tmp = VendasTmp.query.filter_by(registro_id=registro.id).one_or_none()
        records = ((tmp.payload or {}).get("records") if tmp else None) or []
        tipo = TipoLancamento.query.filter_by(codigo=registro.lancamento or "venda", ativo=True).first()
        if not records:
            motivos.append("Os dados da planilha deste registro não existem mais: envie a planilha de novo.")
        if tipo is None:
            motivos.append(f"O tipo de lançamento '{registro.lancamento or 'venda'}' não está ativo (Admin › Constantes).")
        if not records or tipo is None:
            raise Bloqueado(motivos + motivos_pendencias(coletor))
        if api:
            prep = preparar_comercializacao(registro, records, tipo, coletor)
            plano = prep["plano"]
        else:
            plano = build_plano(records, coletor, backend="browser")
        motivos += motivos_pendencias(coletor)
        if motivos:
            raise Bloqueado(motivos)
        if api:
            from app.mapa_api.montagem import descricao_requisicao
            job.commands = [descricao_requisicao(prep)]
            meta.update({"servico": prep["servico"], "payload": prep["payload"]})
        else:
            job.commands = com_cmds(plano, tipo=tipo_config(tipo), rotulos=rotulos_ativos(), limpar=limpar)
        meta["totais"] = totais(plano)
        meta["plano"] = plano            # o que o retry reenvia (De->Para atual): base da conferencia
    else:
        from app.models import DifTmp, GtaTemp, GtaUploadTmp, SifTmp

        def recs(m):
            row = m.query.filter_by(registro_id=registro.id).one_or_none()
            return ((row.payload or {}).get("records") if row else None) or []

        if job.gta_source == "manual":
            gta = normalize_gta_manual(GtaTemp.query.filter_by(registro_id=registro.id).all())
        else:
            gta = recs(GtaUploadTmp)
        dif, sif = recs(DifTmp), recs(SifTmp)
        if not (gta and dif and sif):
            motivos.append(f"Dados do registro ausentes (GTA={len(gta)}, DIF={len(dif)}, SIF={len(sif)}): refaça o lançamento.")
            raise Bloqueado(motivos)
        if api:
            from app.mapa_api.montagem import descricao_requisicao, preparar_abate
            prep = preparar_abate(registro, gta, dif, sif, coletor)
        else:
            uf_idx, _ = uf_origem_index(cred)
            novos = build_commands(
                build_legacy_structure_from_new(gta, merge_diagnostics(dif, sif), uf_index_default=uf_idx),
                coletor=coletor)
        motivos += motivos_pendencias(coletor)
        if motivos:
            raise Bloqueado(motivos)
        if api:
            job.commands = [descricao_requisicao(prep)]
            meta.update({"servico": prep["servico"], "payload": prep["payload"]})
        else:
            job.commands = novos
    job.meta = meta
