# app/blueprints/admin/constantes.py
"""
CRUD das constantes que antes eram dicionários hardcoded.

Qualquer gravação aqui invalida o cache do resolver e é registrada na
auditoria — mudar um vínculo altera o que é enviado ao portal do MAPA.
"""
from flask import render_template, request, redirect, url_for, flash
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Diagnostico, DiagnosticoAlias,
    ParteAfetada, ParteAfetadaAlias,
    Destino, DestinoAlias,
    CondenaParte,
    ProdutoVenda, ProdutoVendaAlias,
    EstadoVenda, EstadoVendaAlias,
    TipoLancamento,
)
from app.security import admin_required
from app.utils.audit import log_action
from app.utils.format import normalize_str
from app.logic.constantes import invalidar_cache


# Cada aba declara o que precisa para reaproveitar as mesmas views.
SECOES = {
    "diagnosticos": {
        "label": "Diagnósticos",
        "model": Diagnostico,
        "alias_model": DiagnosticoAlias,
        "alias_fk": "diagnostico_id",
        "campo_nome": "descricao_mapa",
        "tem_id_mapa": False,
        "cache": "diagnostico",
        "singular": "Diagnóstico",
        "extras": [
            {"campo": "id_api", "rotulo": "ID na API (GET /diagnosticos)", "rotulo_curto": "ID na API", "numerico": True, "opcional": True,
             "ajuda": "Preenchido por “Sincronizar catálogos” em Admin > API do MAPA, ou à mão."},
        ],
    },
    "partes": {
        "label": "Partes Afetadas",
        "model": ParteAfetada,
        "alias_model": ParteAfetadaAlias,
        "alias_fk": "parte_id",
        "campo_nome": "nome",
        "tem_id_mapa": True,
        "id_mapa_opcional": True,
        "cache": "parte",
        "singular": "Parte afetada",
        "rotulo_id_mapa": "Índice no select do portal (legado/navegador)",
        "rotulo_id_mapa_curto": "Índice portal",
        "extras": [
            {"campo": "id_api", "rotulo": "ID na API (GET /partes-afetadas)", "rotulo_curto": "ID na API", "numerico": True, "opcional": True},
        ],
    },
    "destinos": {
        "label": "Destinos",
        "model": Destino,
        "alias_model": DestinoAlias,
        "alias_fk": "destino_id",
        "campo_nome": "nome",
        "tem_id_mapa": True,
        "id_mapa_opcional": True,
        "cache": "destino",
        "singular": "Destino",
        "rotulo_id_mapa": "Índice no select do portal (legado/navegador)",
        "rotulo_id_mapa_curto": "Índice portal",
        "extras": [
            {"campo": "id_api", "rotulo": "ID na API (GET /destino-condenacoes)", "rotulo_curto": "ID na API", "numerico": True, "opcional": True},
        ],
    },
    # ---- Comercializacao (De -> Para das planilhas de vendas) ----
    "produtos-venda": {
        "label": "Produtos (vendas)",
        "model": ProdutoVenda,
        "alias_model": ProdutoVendaAlias,
        "alias_fk": "produto_id",
        "campo_nome": "nome",
        "tem_id_mapa": True,
        "cache": "produto_venda",
        "singular": "Produto de venda",
        "grupo": "comercializacao",
        "rotulo_nome": "Produto na planilha (De)",
        "id_mapa_opcional": True,
        "rotulo_id_mapa": "ID do produto no portal (legado/navegador)",
        "rotulo_id_mapa_curto": "ID portal",
        "ajuda_id_mapa": "Casa com o id=NNN da linha do produto padronizado no portal. Só o modo navegador usa.",
        "extras": [
            {"campo": "cod_api", "rotulo": "cod_produto na API (GET /produtos)", "rotulo_curto": "Código API", "numerico": True, "opcional": True,
             "ajuda": "Código enviado no webservice. Sem ele o produto fica como pendência."},
            {"campo": "descricao_busca", "rotulo": "Descrição usada na busca do portal (legado/navegador)", "rotulo_curto": "Busca no portal",
             "ajuda": "Texto digitado no campo de busca do produto padronizado."},
        ],
    },
    "estados-venda": {
        "label": "Estados (vendas)",
        "model": EstadoVenda,
        "alias_model": EstadoVendaAlias,
        "alias_fk": "estado_id",
        "campo_nome": "nome",
        "tem_id_mapa": True,
        "cache": "estado_venda",
        "singular": "Estado de venda",
        "grupo": "comercializacao",
        "rotulo_nome": "UF na planilha (De)",
        "rotulo_id_mapa": "Índice da UF no select do portal (Para)",
        "ajuda_id_mapa": "Posição da opção no select de UF (0 = placeholder; AC = 1 ... TO = 27).",
        "extras": [],
    },
}


def _secao(nome):
    cfg = SECOES.get(nome)
    if not cfg:
        return None
    return cfg


def register(bp):
    """Registra as rotas de constantes no blueprint admin já existente."""
    _registrar_tipos_lancamento(bp)

    @bp.app_template_filter("norm")
    def _norm_filter(txt):
        return normalize_str(txt or "")

    @bp.context_processor
    def _contagens():
        """`contar(secao)` para os selos das abas (consulta so quando a aba e renderizada)."""
        def contar(chave):
            try:
                if chave == "condenas":
                    return CondenaParte.query.count()
                if chave == "tipos-lancamento":
                    return TipoLancamento.query.count()
                return SECOES[chave]["model"].query.count()
            except Exception:
                return "·"
        return {"contar": contar}

    # ------------------------------------------------------------------
    # Listagem
    # ------------------------------------------------------------------
    @bp.route("/constantes")
    @admin_required
    def constantes():
        return redirect(url_for("admin.constantes_secao", secao="diagnosticos"))

    @bp.route("/constantes/<secao>")
    @admin_required
    def constantes_secao(secao):
        if secao == "condenas":
            itens = CondenaParte.query.order_by(CondenaParte.nome).all()
            return render_template(
                "admin/constantes/condenas.html",
                secoes=SECOES, secao_atual=secao, itens=itens,
            )

        cfg = _secao(secao)
        if not cfg:
            flash("Seção de constantes inexistente.", "warning")
            return redirect(url_for("admin.constantes"))

        busca = (request.args.get("q") or "").strip()
        status = (request.args.get("status") or "").strip()
        model = cfg["model"]
        campo = getattr(model, cfg["campo_nome"])

        qs = model.query
        if busca:
            qs = qs.filter(campo.ilike(f"%{busca}%"))
        if status == "ativo":
            qs = qs.filter(model.ativo.is_(True))
        elif status == "inativo":
            qs = qs.filter(model.ativo.is_(False))
        itens = qs.order_by(campo).all()

        return render_template(
            "admin/constantes/lista.html",
            secoes=SECOES, secao_atual=secao, cfg=cfg,
            itens=itens, busca=busca, status=status,
        )

    # ------------------------------------------------------------------
    # Criar / editar
    # ------------------------------------------------------------------
    @bp.route("/constantes/<secao>/novo", methods=["GET", "POST"])
    @bp.route("/constantes/<secao>/<int:item_id>/editar", methods=["GET", "POST"])
    @admin_required
    def constante_form(secao, item_id=None):
        cfg = _secao(secao)
        if not cfg:
            flash("Seção de constantes inexistente.", "warning")
            return redirect(url_for("admin.constantes"))

        model = cfg["model"]
        item = db.session.get(model, item_id) if item_id else None
        if item_id and not item:
            flash(f"{cfg['singular']} não encontrado.", "warning")
            return redirect(url_for("admin.constantes_secao", secao=secao))

        if request.method == "POST":
            nome = (request.form.get("nome") or "").strip()
            ativo = request.form.get("ativo") == "1"
            obs = (request.form.get("obs") or "").strip() or None

            if not nome:
                flash("Informe o nome.", "warning")
                return redirect(request.url)

            extras = {}
            for ex in cfg.get("extras", []):
                valor = (request.form.get(ex["campo"]) or "").strip()
                if ex.get("numerico"):
                    if not valor and ex.get("opcional"):
                        extras[ex["campo"]] = None
                        continue
                    if not valor.isdigit() or int(valor) <= 0:
                        flash(f"{ex['rotulo']}: informe um inteiro maior que zero.", "warning")
                        return redirect(request.url)
                    extras[ex["campo"]] = int(valor)
                    continue
                if not valor and not ex.get("opcional"):
                    flash(f"Informe: {ex['rotulo']}.", "warning")
                    return redirect(request.url)
                extras[ex["campo"]] = valor or None

            id_mapa = None
            if cfg["tem_id_mapa"]:
                raw = (request.form.get("id_mapa") or "").strip()
                if not raw and cfg.get("id_mapa_opcional"):
                    id_mapa = None
                elif not raw.isdigit() or int(raw) <= 0:
                    flash("Informe um ID do MAPA válido (inteiro maior que zero).", "warning")
                    return redirect(request.url)
                else:
                    id_mapa = int(raw)

            novo = item is None
            if novo:
                item = model()
                db.session.add(item)

            setattr(item, cfg["campo_nome"], nome)
            item.ativo = ativo
            item.obs = obs
            if cfg["tem_id_mapa"]:
                item.id_mapa = id_mapa
            for campo, valor in extras.items():
                setattr(item, campo, valor)

            try:
                db.session.flush()
                # a própria grafia oficial precisa resolver para si mesma
                _garantir_alias(cfg, item, nome)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("Já existe um registro com esse nome ou apelido.", "danger")
                return redirect(request.url)

            invalidar_cache(cfg["cache"])
            log_action(
                f"constante:{'create' if novo else 'update'}",
                entity=model.__name__, entity_id=item.id,
                meta={"nome": nome, "id_mapa": id_mapa, "ativo": ativo, **extras},
            )
            flash(f"{cfg['singular']} salvo.", "success")
            return redirect(url_for("admin.constantes_secao", secao=secao))

        return render_template(
            "admin/constantes/form.html",
            secoes=SECOES, secao_atual=secao, cfg=cfg, item=item,
        )

    # ------------------------------------------------------------------
    # Excluir
    # ------------------------------------------------------------------
    @bp.route("/constantes/<secao>/<int:item_id>/excluir", methods=["POST"])
    @admin_required
    def constante_excluir(secao, item_id):
        cfg = _secao(secao)
        if not cfg:
            flash("Seção de constantes inexistente.", "warning")
            return redirect(url_for("admin.constantes"))

        if request.form.get("confirm") != "yes":
            flash("Confirmação ausente. Exclusão cancelada.", "warning")
            return redirect(url_for("admin.constantes_secao", secao=secao))

        item = db.session.get(cfg["model"], item_id)
        if not item:
            flash(f"{cfg['singular']} não encontrado.", "warning")
            return redirect(url_for("admin.constantes_secao", secao=secao))

        nome = getattr(item, cfg["campo_nome"])
        db.session.delete(item)
        db.session.commit()

        invalidar_cache(cfg["cache"])
        log_action("constante:delete", entity=cfg["model"].__name__,
                   entity_id=item_id, meta={"nome": nome})
        flash(f"{cfg['singular']} excluído.", "success")
        return redirect(url_for("admin.constantes_secao", secao=secao))

    # ------------------------------------------------------------------
    # Aliases
    # ------------------------------------------------------------------
    @bp.route("/constantes/<secao>/<int:item_id>/alias", methods=["POST"])
    @admin_required
    def constante_alias_novo(secao, item_id):
        cfg = _secao(secao)
        if not cfg:
            flash("Seção de constantes inexistente.", "warning")
            return redirect(url_for("admin.constantes"))

        item = db.session.get(cfg["model"], item_id)
        if not item:
            flash(f"{cfg['singular']} não encontrado.", "warning")
            return redirect(url_for("admin.constantes_secao", secao=secao))

        alias = (request.form.get("alias") or "").strip()
        chave = normalize_str(alias)
        if not chave:
            flash("Informe um apelido válido.", "warning")
            return redirect(url_for("admin.constante_form", secao=secao, item_id=item_id))

        existente = cfg["alias_model"].query.filter_by(alias_norm=chave).first()
        if existente:
            flash("Esse apelido já está vinculado a outro registro.", "danger")
            return redirect(url_for("admin.constante_form", secao=secao, item_id=item_id))

        novo = cfg["alias_model"](alias=alias, alias_norm=chave)
        setattr(novo, cfg["alias_fk"], item.id)
        db.session.add(novo)
        db.session.commit()

        invalidar_cache(cfg["cache"])
        log_action("constante:alias_create", entity=cfg["alias_model"].__name__,
                   entity_id=novo.id, meta={"alias": alias, "alvo_id": item.id})
        flash("Apelido adicionado.", "success")
        return redirect(url_for("admin.constante_form", secao=secao, item_id=item_id))

    @bp.route("/constantes/<secao>/alias/<int:alias_id>/excluir", methods=["POST"])
    @admin_required
    def constante_alias_excluir(secao, alias_id):
        cfg = _secao(secao)
        if not cfg:
            flash("Seção de constantes inexistente.", "warning")
            return redirect(url_for("admin.constantes"))

        alias = db.session.get(cfg["alias_model"], alias_id)
        if not alias:
            flash("Apelido não encontrado.", "warning")
            return redirect(url_for("admin.constantes_secao", secao=secao))

        item_id = getattr(alias, cfg["alias_fk"])
        item = db.session.get(cfg["model"], item_id)

        # sem apelido nenhum o registro fica inalcançável na resolução
        if item and normalize_str(getattr(item, cfg["campo_nome"])) == alias.alias_norm:
            flash("Não é possível remover o apelido que corresponde ao próprio nome.", "danger")
            return redirect(url_for("admin.constante_form", secao=secao, item_id=item_id))

        db.session.delete(alias)
        db.session.commit()

        invalidar_cache(cfg["cache"])
        log_action("constante:alias_delete", entity=cfg["alias_model"].__name__,
                   entity_id=alias_id, meta={"alias": alias.alias, "alvo_id": item_id})
        flash("Apelido removido.", "success")
        return redirect(url_for("admin.constante_form", secao=secao, item_id=item_id))

    # ------------------------------------------------------------------
    # Condenas
    # ------------------------------------------------------------------
    @bp.route("/constantes/condenas/<int:item_id>/editar", methods=["POST"])
    @admin_required
    def condena_editar(item_id):
        item = db.session.get(CondenaParte, item_id)
        if not item:
            flash("Parte de condena não encontrada.", "warning")
            return redirect(url_for("admin.constantes_secao", secao="condenas"))

        raw = (request.form.get("slots") or "").strip()
        try:
            slots = [int(p.strip()) for p in raw.split(",") if p.strip()]
        except ValueError:
            flash("Slots inválidos. Use números separados por vírgula (ex.: 1, 2, 3).", "danger")
            return redirect(url_for("admin.constantes_secao", secao="condenas"))

        if not slots:
            flash("Informe ao menos um slot.", "warning")
            return redirect(url_for("admin.constantes_secao", secao="condenas"))

        item.slots = slots
        item.ativo = request.form.get("ativo") == "1"
        db.session.commit()

        invalidar_cache("condena")
        log_action("constante:update", entity="CondenaParte", entity_id=item.id,
                   meta={"nome": item.nome, "slots": slots})
        flash("Parte de condena atualizada.", "success")
        return redirect(url_for("admin.constantes_secao", secao="condenas"))


def _registrar_tipos_lancamento(bp):
    """CRUD dos tipos de lancamento da comercializacao (venda, recebimento, expedicao...)."""
    import re as _re

    @bp.route("/constantes/tipos-lancamento")
    @admin_required
    def tipos_lancamento():
        itens = TipoLancamento.query.order_by(TipoLancamento.id).all()
        return render_template("admin/constantes/tipos_lancamento.html",
                               secoes=SECOES, secao_atual="tipos-lancamento", itens=itens, item=None)

    @bp.route("/constantes/tipos-lancamento/novo", methods=["GET", "POST"])
    @bp.route("/constantes/tipos-lancamento/<int:item_id>/editar", methods=["GET", "POST"])
    @admin_required
    def tipo_lancamento_form(item_id=None):
        item = db.session.get(TipoLancamento, item_id) if item_id else None
        if item_id and not item:
            flash("Tipo de lançamento não encontrado.", "warning")
            return redirect(url_for("admin.tipos_lancamento"))

        if request.method == "POST":
            codigo = normalize_str(request.form.get("codigo") or "").replace(" ", "")
            nome = (request.form.get("nome") or "").strip()
            rotulo = (request.form.get("rotulo_portal") or "").strip()
            idx = {}
            for campo in ("tipo_transacao_idx", "ambito_idx", "operador_idx"):
                raw = (request.form.get(campo) or "").strip()
                if not raw.isdigit() or int(raw) <= 0:
                    flash("Os índices dos selects precisam ser inteiros maiores que zero.", "warning")
                    return redirect(request.url)
                idx[campo] = int(raw)
            if item:
                codigo = item.codigo          # o codigo identifica os registros existentes: nao muda
            if not _re.fullmatch(r"[a-z0-9]{2,30}", codigo) or not nome or not rotulo:
                flash("Informe código (letras/números), nome e rótulo do portal.", "warning")
                return redirect(request.url)

            novo = item is None
            if novo:
                item = TipoLancamento(codigo=codigo)
                db.session.add(item)
            item.nome, item.rotulo_portal = nome, rotulo
            item.ativo = request.form.get("ativo") == "1"
            item.obs = (request.form.get("obs") or "").strip() or None
            for campo, valor in idx.items():
                setattr(item, campo, valor)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("Já existe um tipo com esse código.", "danger")
                return redirect(request.url)
            log_action(f"constante:{'create' if novo else 'update'}", entity="TipoLancamento",
                       entity_id=item.id, meta={"codigo": item.codigo, **idx, "ativo": item.ativo})
            flash("Tipo de lançamento salvo.", "success")
            return redirect(url_for("admin.tipos_lancamento"))

        return render_template("admin/constantes/tipos_lancamento.html",
                               secoes=SECOES, secao_atual="tipos-lancamento", itens=None, item=item,
                               novo=item is None)


def _garantir_alias(cfg, item, nome):
    """O nome oficial sempre precisa existir como apelido, senão não resolve."""
    chave = normalize_str(nome)
    if not chave:
        return
    existente = cfg["alias_model"].query.filter_by(alias_norm=chave).first()
    if existente and getattr(existente, cfg["alias_fk"]) == item.id:
        return
    if existente:
        raise IntegrityError("alias em uso", None, None)
    novo = cfg["alias_model"](alias=nome, alias_norm=chave)
    setattr(novo, cfg["alias_fk"], item.id)
    db.session.add(novo)
