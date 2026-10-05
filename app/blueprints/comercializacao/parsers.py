"""Parser de planilha por tipo de lancamento.

Para habilitar recebimento/expedicao: escreva o validador (mesmo envelope de
`validar_vendas`: {"records": [{"produto","uf","quantidade",...}], "meta": {...}}), registre-o
aqui e cadastre o tipo em Admin > Constantes > Tipos de lancamento. Tipo sem parser aparece no
upload como "layout ainda nao suportado".
"""
from typing import Callable, Dict, Optional

from .parser import validar_vendas

PARSERS: Dict[str, Callable] = {
    "venda": validar_vendas,
    # "recebimento": validar_recebimentos,
    # "expedicao": validar_expedicoes,
}


def parser_para(codigo: str) -> Optional[Callable]:
    return PARSERS.get(codigo)
