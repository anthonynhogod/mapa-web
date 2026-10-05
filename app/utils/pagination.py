# app/utils/pagination.py
from dataclasses import dataclass
from typing import List, Sequence, Union, Callable
from flask import request
import math

PER_PAGE: int = 10  # fixo

@dataclass
class Pagination:
    page: int           # página atual (>=1)
    per_page: int       # sempre 25 (fixo)
    pages: int          # total de páginas
    total: int          # total de itens
    items: List         # fatia atual

def resolve_page(default: int = 1) -> int:
    try:
        p = int(request.args.get("page", default))
        return p if p > 0 else default
    except Exception:
        return default

def paginate_list(rows: List, page: int, per_page: int = PER_PAGE) -> Pagination:
    total = len(rows)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    end = start + per_page
    return Pagination(page=page, per_page=per_page, pages=pages, total=total, items=rows[start:end])

def paginate_query(query, page: int, db, per_page: int = PER_PAGE) -> Pagination:
    """Flask‑SQLAlchemy 3.x com fallback p/ 2.x."""
    try:
        # 3.x
        pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
        items = list(pagination.items)
        total = pagination.total
        pages = pagination.pages
    except Exception:
        # 2.x
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        items = list(pagination.items)
        total = pagination.total
        pages = pagination.pages
    return Pagination(page=page, per_page=per_page, pages=pages, total=total, items=items)

# ---- Construção da janela numérica (1 … 4 5 [6] 7 8 … 20)
def make_page_series(current: int, pages: int, window: int = 2, edge: int = 1) -> List[Union[int, str]]:
    """
    Retorna uma sequência com páginas e '…', ex.:
      [1, '…', 4, 5, 6, 7, 8, '…', 20]
    - window: quantas ao redor da atual (para cada lado)
    - edge:   quantas no começo/fim sempre visíveis
    """
    if pages <= 1:
        return [1]

    seq = []
    left = max(1, current - window)
    right = min(pages, current + window)

    left_block = set(range(1, edge + 1))
    middle_block = set(range(left, right + 1))
    right_block = set(range(pages - edge + 1, pages + 1))

    def append_range(a, b):
        for i in range(a, b + 1):
            if i >= 1 and i <= pages:
                seq.append(i)

    # Merge inteligente com '…'
    last = 0
    for part in [sorted(left_block), sorted(middle_block), sorted(right_block)]:
        if not part:
            continue
        a, b = part[0], part[-1]
        if a - last > 1:
            seq.append('…')
        append_range(a, b)
        last = b
    # remove '…' inicial se houver
    if seq and seq[0] == '…':
        seq = seq[1:]
    # remove '…' duplicados
    cleaned = []
    for x in seq:
        if cleaned and cleaned[-1] == '…' and x == '…':
            continue
        cleaned.append(x)
    return cleaned

# ---- "Paginador em TXT" (string plana)
def render_paginator_text(
    pagination: Pagination,
    window: int = 2,
    edge: int = 1,
    label_first: str = "<<",
    label_prev: str = "<",
    label_next: str = ">",
    label_last: str = ">>",
) -> str:
    """
    Gera uma linha de texto, por ex.:
    "<< < 1 … 4 5 [6] 7 8 … 20 > >>  |  Página 6/20 — 500 itens"
    """
    cur, pages, total = pagination.page, pagination.pages, pagination.total
    parts: List[str] = []

    # Controles (desabilitados entre parênteses)
    parts.append(label_first if cur > 1 else f"({label_first})")
    parts.append(label_prev if cur > 1 else f"({label_prev})")

    # Série de páginas
    for token in make_page_series(cur, pages, window=window, edge=edge):
        if token == '…':
            parts.append("…")
        elif token == cur:
            parts.append(f"[{token}]")
        else:
            parts.append(str(token))

    parts.append(label_next if cur < pages else f"({label_next})")
    parts.append(label_last if cur < pages else f"({label_last})")

    suffix = f"  |  Página {cur}/{pages} — {total} itens"
    return " ".join(parts) + suffix