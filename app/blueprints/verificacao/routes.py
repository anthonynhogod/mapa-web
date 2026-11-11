"""Conferência de lançamentos de comercialização: relatório do MAPA × o que foi enviado."""
import io

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from markupsafe import escape
from werkzeug.utils import secure_filename

from app.blueprints.comercializacao.parser import validar_vendas
from app.extensions import db
from app.forms import VerificacaoForm
from app.logic import verificacao as V
from app.executor.queue import get_queue
from app.logic import conferencia_portal as CP
from app.logic.bloqueios import avaliar_credencial, resumo_flash
from app.mapa_api.montagem import credencial_do_usuario, usa_api
from app.models import ExecJob, Registro, Verificacao
from app.utils.audit import log_action

bp = Blueprint("verificacao", __name__)

ORIGEM_ROTULO = {
    "job_plano": "plano enviado (gravado no job)",
    "job_comandos": "comandos executados pelo job",
    "planilha_reprocessada": "planilha de vendas reprocessada (De→Para atual)",
    "planilha_enviada": "planilha de vendas enviada agora (De→Para atual)",
}
STATUS_LABELS = {"AT": "Em andamento", "PT": "Pendente", "FZ": "Finalizado", "ER": "Erro"}


def _registros_do_usuario():
    return (Registro.query.filter_by(user_id=current_user.id, tipo="comercializacao")
            .order_by(Registro.periodo_ini.desc(), Registro.id.desc()).all())


def _preencher(form, preselecionado=None):
    opcoes = [("", "— usar uma planilha de vendas (enviar abaixo) —")]
    for r in _registros_do_usuario():
        opcoes.append((str(r.id), f"{r.periodo_label} · {r.lancamento or 'venda'} · {STATUS_LABELS.get(r.status, r.status)}"))
    form.registro.choices = opcoes
    if preselecionado and request.method == "GET":
        form.registro.data = str(preselecionado)
    elif request.method == "GET" and len(opcoes) > 1:
        fz = next((r for r in _registros_do_usuario() if r.status == "FZ"), None)
        form.registro.data = str(fz.id) if fz else opcoes[1][0]


def _historico():
    return (Verificacao.query.filter_by(user_id=current_user.id)
            .order_by(Verificacao.created_at.desc(), Verificacao.id.desc()).limit(30).all())


def _jobs_portal():
    """Ultimos jobs de conferencia/complemento no portal do usuario (em andamento ou concluidos)."""
    jobs = ExecJob.query.filter_by(owner_user_id=current_user.id).order_by(ExecJob.id.desc()).limit(60).all()
    return [j for j in jobs if (j.meta or {}).get("tipo_job") in ("conferencia", "complemento")][:10]


def _pagina(form, status=200):
    regs = {r.id: r for r in _registros_do_usuario()}
    return render_template("verificacao/lista.html", form=form, historico=_historico(), registros=regs,
                           ORIGEM_ROTULO=ORIGEM_ROTULO, jobs_portal=_jobs_portal()), status


def _verificacao_do_usuario(vid):
    v = db.session.get(Verificacao, vid)
    if v is None or v.user_id != current_user.id:
        abort(404)
    return v


@bp.route("/", methods=["GET"])
@login_required
def lista():
    form = VerificacaoForm()
    _preencher(form, request.args.get("registro", type=int))
    return _pagina(form)


@bp.route("/nova", methods=["POST"])
@login_required
def nova():
    form = VerificacaoForm()
    _preencher(form)
    if not form.validate_on_submit():
        flash("Envie o relatório do MAPA em .xlsx.", "warning")
        return _pagina(form, 400)

    arq_mapa = form.mapa.data
    nome_mapa = secure_filename(arq_mapa.filename or "") or "relatorio_mapa.xlsx"
    rel = V.ler_relatorio_mapa(arq_mapa.stream)
    meta = rel["meta"]
    if meta["errors"]:
        resumo = "; ".join(f"{e['where']}: {e['message']}" for e in meta["errors"][:5])
        flash(f"Relatório do MAPA com problemas: {escape(resumo)}", "danger")
        return _pagina(form, 400)

    avisos = [w["message"] for w in meta["warnings"]]
    registro, job_id, arquivo_base = None, None, None
    if form.registro.data:
        try:
            registro = Registro.query.filter_by(id=int(form.registro.data), user_id=current_user.id,
                                                tipo="comercializacao").first()
        except ValueError:
            registro = None
        if registro is None:
            flash("Registro não encontrado.", "danger")
            return _pagina(form, 404)
        try:
            itens, origem, avisos_base, job_id = V.base_do_registro(registro)
        except ValueError as e:
            flash(resumo_flash("Não foi possível montar a base de comparação:", [str(e)]), "danger")
            return _pagina(form, 400)
        avisos += avisos_base
    else:
        arq_vendas = form.vendas.data
        if arq_vendas is None or not getattr(arq_vendas, "filename", ""):
            flash("Escolha um registro já lançado ou envie a planilha de vendas.", "warning")
            return _pagina(form, 400)
        arquivo_base = secure_filename(arq_vendas.filename) or "vendas.xlsx"
        res_vendas = validar_vendas(arq_vendas.stream)
        if res_vendas["meta"]["errors"]:
            msg = [f"{e['where']}: {e['message']}" for e in res_vendas["meta"]["errors"][:6]]
            flash(resumo_flash("A planilha de vendas tem erros:", msg), "danger")
            return _pagina(form, 400)
        itens, pend = V.itens_da_planilha(res_vendas["records"])
        if pend:    # mesma regra do lançamento: sem vínculo no De→Para não há como comparar
            flash(resumo_flash("Há termos sem vínculo no De→Para; cadastre-os em Admin › Constantes e tente de novo:", pend),
                  "danger")
            return _pagina(form, 400)
        origem = "planilha_enviada"
        avisos.append("A base foi calculada com o De→Para de hoje; se uma constante mudou depois do lançamento, "
                      "pode diferir do que foi enviado.")

    # relatório de outro estabelecimento?
    cred = credencial_do_usuario(current_user.id)
    if cred is not None and meta["controles"]:
        meu = V._sem_zeros(cred.numero_sif)
        if meu and all(V._sem_zeros(c) != meu for c in meta["controles"]):
            avisos.append(f"O relatório é do controle {', '.join(meta['controles'])}, mas o seu SIF cadastrado é {cred.numero_sif}.")

    res = V.verificar(itens, rel["linhas"])
    avisos += res.pop("avisos")
    if meta["ignoradas"]:
        avisos.append("Linhas do relatório NÃO conferidas: " + "; ".join(f"{n}× {m}" for m, n in meta["ignoradas"].items()) + ".")

    v = Verificacao(
        user_id=current_user.id, registro_id=registro.id if registro else None, job_id=job_id, origem_base=origem,
        arquivo_mapa=nome_mapa, arquivo_base=arquivo_base, status=res["status"],
        resumo={"totais": res["totais"], "contagem": res["contagem"], "avisos": avisos, "ignoradas": meta["ignoradas"],
                "controles": meta["controles"], "linhas_relatorio": meta["counts"]["linhas"], "itens_enviados": len(itens)},
        resultado={"celulas": res["celulas"], "por_uf": res["por_uf"], "por_produto": res["por_produto"]},
    )
    db.session.add(v)
    db.session.commit()
    log_action("verificacao:create", entity="Verificacao", entity_id=v.id,
               meta={"registro": v.registro_id, "status": v.status, "divergentes": res["totais"]["divergentes"]})
    flash("Conferência concluída: " + ("tudo confere." if v.status == "OK" else
          f"{res['totais']['divergentes']} divergência(s) encontrada(s)."), "success" if v.status == "OK" else "warning")
    return redirect(url_for("verificacao.ver", verificacao_id=v.id))


def _criar_job_portal(registro: Registro, tipo_job: str):
    """Cria o job (conferencia ou complemento) com TODOS os vetos. Devolve (job, None) ou (None, motivos)."""
    from app.executor.conferencia import LER
    if usa_api():
        return None, ["Ler/complementar direto no portal só funciona no modo navegador (EXEC_BACKEND=browser)."]
    if ExecJob.query.filter(ExecJob.registro_id == registro.id, ExecJob.status.in_(["ESPERA", "EXECUTANDO"])).first():
        return None, ["Já existe um job ativo para este registro: espere ele terminar."]
    cred, motivos = avaliar_credencial(current_user.id, registro.especie)
    if motivos:
        return None, motivos
    try:
        itens, origem, avisos, job_base = V.base_do_registro(registro)
    except ValueError as e:
        return None, [f"Não foi possível montar a base de comparação: {e}"]
    job = ExecJob(
        registro_id=registro.id, owner_user_id=current_user.id, gta_source=tipo_job, commands=[LER], status="ESPERA",
        progress=0, errors=[],
        meta={"modulo": "comercializacao", "tipo_job": tipo_job, "lancamento": registro.lancamento or "venda",
              "numero_sif": cred.numero_sif, "status_original": registro.status, "plano_base": itens,
              "origem_base": origem, "job_base": job_base, "avisos_base": avisos,
              "periodo": {"ini": registro.periodo_ini.strftime("%d/%m/%Y"), "fim": registro.periodo_fim.strftime("%d/%m/%Y")}},
    )
    db.session.add(job)
    db.session.commit()
    get_queue().enqueue(job.id)
    log_action(f"verificacao:{tipo_job}", entity="ExecJob", entity_id=job.id, meta={"registro": registro.id})
    return job, None


@bp.route("/portal", methods=["POST"])
@login_required
def conferir_portal():
    """Le o registro no portal (somente leitura) e compara LINHA A LINHA com o que foi enviado."""
    form = VerificacaoForm()
    registro = Registro.query.filter_by(id=request.form.get("registro", type=int) or 0, user_id=current_user.id,
                                        tipo="comercializacao").first()
    if registro is None:
        flash("Escolha um registro de comercialização.", "warning")
        return redirect(url_for("verificacao.lista"))
    job, motivos = _criar_job_portal(registro, "conferencia")
    if motivos:
        flash(resumo_flash("Não foi possível conferir com o portal:", motivos), "danger")
        return redirect(url_for("verificacao.lista", registro=registro.id))
    flash(f"Leitura do portal enfileirada (job #{job.id}). Abre o resultado quando o worker terminar; nada é alterado no portal.",
          "success")
    return redirect(url_for("verificacao.lista"))


@bp.route("/<int:verificacao_id>/complementar", methods=["POST"])
@login_required
def complementar(verificacao_id: int):
    """Inclui no registro EXISTENTE só as linhas que faltam (nunca exclui nem altera quantidade)."""
    v = _verificacao_do_usuario(verificacao_id)
    registro = db.session.get(Registro, v.registro_id) if v.registro_id else None
    falta = CP.itens_a_complementar((v.resultado or {}).get("linhas_diff") or {"faltando": []}) if (v.resumo or {}).get("fonte") == "portal" else []
    if registro is None or not falta:
        flash("Esta conferência não tem linhas a complementar (use uma conferência lida do portal).", "warning")
        return redirect(url_for("verificacao.ver", verificacao_id=v.id))
    job, motivos = _criar_job_portal(registro, "complemento")
    if motivos:
        flash(resumo_flash("Não foi possível complementar:", motivos), "danger")
        return redirect(url_for("verificacao.ver", verificacao_id=v.id))
    flash(f"Complemento enfileirado (job #{job.id}): o worker lê o portal de novo, inclui só o que ainda faltar e confere o resultado.",
          "success")
    return redirect(url_for("verificacao.lista"))


@bp.route("/<int:verificacao_id>", methods=["GET"])
@login_required
def ver(verificacao_id: int):
    v = _verificacao_do_usuario(verificacao_id)
    registro = db.session.get(Registro, v.registro_id) if v.registro_id else None
    celulas = v.resultado["celulas"]
    divergentes = [c for c in celulas if c["status"] not in V.STATUS_OK]
    linhas = (v.resultado or {}).get("linhas_diff")
    return render_template("verificacao/resultado.html", v=v, registro=registro, celulas=celulas, divergentes=divergentes,
                           ORIGEM_ROTULO=ORIGEM_ROTULO, linhas=linhas, portal=(v.resumo or {}).get("fonte") == "portal",
                           a_complementar=len(CP.itens_a_complementar(linhas)) if linhas else 0)


@bp.route("/<int:verificacao_id>/exportar", methods=["GET"])
@login_required
def exportar(verificacao_id: int):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    v = _verificacao_do_usuario(verificacao_id)
    wb = Workbook()

    def aba(titulo, cabecalho, linhas, primeira=False):
        ws = wb.active if primeira else wb.create_sheet()
        ws.title = titulo
        ws.append(cabecalho)
        for c in ws[1]:
            c.font = Font(bold=True)
        for ln in linhas:
            ws.append(ln)
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(60, max(12, max(len(str(c.value or "")) for c in col) + 2))

    cel = v.resultado["celulas"]
    cab = ["Descrição (MAPA)", "UF", "Enviado", "MAPA", "Diferença (MAPA − enviado)", "Situação", "Observação", "Produtos enviados"]
    linha = lambda c: [c["descricao"], c["uf"], c["nosso"], c["mapa"], c["diff"], c["rotulo"], c["dica"],  # noqa: E731
                       "; ".join(f"{i.get('produto') or i.get('id')} ({i['quantidade']})" for i in c["itens"])]
    aba("Divergências", cab, [linha(c) for c in cel if c["status"] not in V.STATUS_OK], primeira=True)
    aba("Todas as células", cab, [linha(c) for c in cel])
    aba("Por UF", ["UF", "Enviado", "MAPA", "Diferença", "Células divergentes"],
        [[g["uf"], g["nosso"], g["mapa"], g["diff"], g["divergentes"]] for g in v.resultado["por_uf"]])
    aba("Por produto", ["Descrição (MAPA)", "Enviado", "MAPA", "Diferença", "Células divergentes"],
        [[g["descricao"], g["nosso"], g["mapa"], g["diff"], g["divergentes"]] for g in v.resultado["por_produto"]])
    linhas = (v.resultado or {}).get("linhas_diff")
    if linhas:
        aba("Faltando no portal", ["UF", "Descrição", "Produto", "Id", "Quantidade"],
            [[l["uf"], l["descricao"], l.get("produto"), l.get("id"), l["quantidade"]] for l in linhas["faltando"]])
        aba("Sobrando no portal", ["UF", "Produto", "Quantidade", "ID Transação"],
            [[l["uf"], l["produto"], l["quantidade"], l.get("transacao")] for l in linhas["sobrando"]])
        aba("Quantidade diferente", ["UF", "Descrição", "Esperado", "No portal", "Diferença"],
            [[l["uf"], l["descricao"], l["esperado"], l["portal"], l["diff"]] for l in linhas["diferentes"]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"conferencia_{v.id}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/<int:verificacao_id>/excluir", methods=["POST"])
@login_required
def excluir(verificacao_id: int):
    v = _verificacao_do_usuario(verificacao_id)
    db.session.delete(v)
    db.session.commit()
    flash("Conferência excluída.", "success")
    return redirect(url_for("verificacao.lista"))
