from dataclasses import dataclass
from typing import List, Dict, Optional, Literal
from flask import Request
from app.utils.format import para_int, parse_peso_br, normalize_str
import pandas as pd
import json
import re
import logging
from typing import Any

#silenciar aviso
pd.set_option('future.no_silent_downcasting', True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

@dataclass
class Issue:
    level: Literal["error", "warning"]
    where: str          # ex: "header", "row[12].lote"
    message: str

def parse_emergencia(value, diagnostico=None) -> int:
    value = str(value).strip().lower()
    emergencia = 1 if value in ("sim", "s", "1", "true") else 0

    # Diagnósticos que forçam emergência = 2
    DIAGNOSTICOS_CRITICOS = {normalize_str('MORTO NO PRÉ ABATE'), normalize_str('MORTO NO TRANSPORTE')} 

    if diagnostico and normalize_str(diagnostico) in DIAGNOSTICOS_CRITICOS:
        emergencia = 2

    return emergencia

DIAG_CANON = {
    # todas as variantes → sem "/"
    "cont gastrointestinal e biliar": "contaminacao gastrointestinalbiliar",
    "cont gastrointestinal biliar":  "contaminacao gastrointestinalbiliar",
    "cont. gastrointestinal e biliar":"contaminacao gastrointestinalbiliar",
    "contaminacao gastrointestinal e biliar": "contaminacao gastrointestinalbiliar",
    "contaminacao gastrointestinal biliar":   "contaminacao gastrointestinalbiliar",
    "contaminacao gastrointestinal/biliar":   "contaminacao gastrointestinalbiliar",
    "contaminacao gastrointestinalbiliar":    "contaminacao gastrointestinalbiliar",
    # não GI permanece distinta
    "contaminacao nao gastrointestinal": "contaminacao nao gastrointestinal",
}


def canonical_diag(nome: str) -> str:
    """
    Normaliza a string e aplica mapeamento de sinônimos.
    Retorna SEMPRE a forma canônica já normalizada (sem '/').
    """
    k = normalize_str(nome or "")
    return DIAG_CANON.get(k, k)


@dataclass
class RowDif:
    lote: int
    diagnostico: str
    destino: str
    parte: str
    emergencia: int

    @classmethod
    def valide_row(cls, row: dict) -> bool:
        try:
            lote = int(row.get("lote", 0))
            if lote <= 0:
                return False

            if not str(row.get("diagnostico", "")).strip():
                return False
            if not str(row.get("destino", "")).strip():
                return False
            if not str(row.get("parte", "")).strip():
                return False

            parse_emergencia(row.get("emergencia", "0"))  # valida formato
            return True
        except Exception as e:
            return False

    @classmethod
    def from_row(cls, row: dict):
        return cls(
            lote=int(row.get("lote", 0)),
            diagnostico=str(row.get("diagnostico", "")).strip(),
            destino=str(row.get("destino", "")).strip(),
            parte=str(row.get("parte", "")).strip(),
            emergencia=parse_emergencia(row.get("emergencia", "0"), row.get("diagnostico", ""))
        )

class DifValidator:
    CABECARIOS_VALIDOS = {
        "codigo": ["codigo", "codigo produtor", "numero produtor"],
        "nome_produtor": ["nome", "nome produtor", "produtor"],
        "cidade": ["cidade", "cidade origem", "origem"],
        "lote": ["lote", "tatuagem", "lote tatuagem"],
        "numero": ["numero", "n"],
        "diagnostico": ["diagnosticos", "diagnostico", "lesao", "lesoes", "lesoes detectadas", "diagnosticos detectados"],
        "destino": ["destinos", "destino"],
        "parte": ["peca", "parte", "parte afetada"],
        "emergencia": ["emergencia", "emer"]
    }

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.filtered_df: Optional[pd.DataFrame] = None
        self.valid_rows: List[RowDif] = []
        self.errors: List[Issue] = []
        self.warnings: List[Issue] = []

        self.normalized_valid_headers = {
            key: set(normalize_str(v) for v in valores)
            for key, valores in self.CABECARIOS_VALIDOS.items()
        }

        try:
            self._find_header()
            self.validate_rows()
        except Exception as e:
            # Captura qualquer falha inesperada do pipeline
            msg = f"Falha inesperada no DifValidator: {e}"
            logger.exception(msg)
            self.errors.append(Issue("error", "validator", msg))

    # ------------- utils de fallback -------------
    @staticmethod
    def _coerce_int(val, default=0) -> int:
        try:
            return int(float(val))
        except Exception:
            return default

    @staticmethod
    def _nz(s, default="") -> str:
        s = str(s or "").strip()
        return s if s else default

    # ------------- header detection -------------
    def _find_header(self):
        max_colunas = min(3, len(self.df.columns))
        indexs = []

        for i in range(min(15, len(self.df))):
            for j in range(max_colunas):
                celula = normalize_str(self.df.iat[i, j])
                if not celula:
                    continue
                for _, valores in self.normalized_valid_headers.items():
                    if celula in valores:
                        indexs.append(i)

        if not indexs:
            self.warnings.append(Issue("warning", "header", "Cabeçalho não detectado por unificação. Tentando fallback de 1 linha."))
            # Fallback: tenta usar a primeira linha não vazia como header direto
            self._fallback_single_row_header()
            return

        min_index, max_index = min(indexs), max(indexs)
        unified_headers = self._get_unified_rows(min_index, max_index)
        mapped = self._map_columns(unified_headers)
        if not mapped:
            self.warnings.append(Issue("warning", "header", "Mapeamento de cabeçalho falhou na unificação. Tentando fallback de 1 linha."))
            self._fallback_single_row_header()

    def _fallback_single_row_header(self):
        # Procura a primeira linha que tenha algum conteúdo textual
        header_row_idx = None
        for i in range(min(10, len(self.df))):
            row_vals = [normalize_str(v) for v in list(self.df.iloc[i].values)]
            if any(row_vals):
                header_row_idx = i
                break
        if header_row_idx is None:
            self.errors.append(Issue("error", "header", "Não foi possível identificar nenhuma linha de cabeçalho."))
            return

        # Usa a linha como nomes de colunas normalizados e recorta o restante
        try:
            new_cols = [normalize_str(c) for c in self.df.iloc[header_row_idx].values]
            temp = self.df.iloc[header_row_idx+1:].copy()
            # Ajusta tamanho de colunas se necessário
            if len(new_cols) != temp.shape[1]:
                self.warnings.append(Issue("warning", "header", f"Número de colunas em header({len(new_cols)}) difere do corpo({temp.shape[1]}). Tentando truncar/alinha-las."))
                min_cols = min(len(new_cols), temp.shape[1])
                new_cols = new_cols[:min_cols]
                temp = temp.iloc[:, :min_cols]
            temp.columns = new_cols

            # Agora mapeia pelos sinônimos conhecidos
            selected = {}
            for chave, sinonimos in self.CABECARIOS_VALIDOS.items():
                for c in temp.columns:
                    if any(sn in c for sn in sinonimos):
                        selected[chave] = c
                        break

            needed = ["lote", "diagnostico", "destino", "parte", "emergencia"]
            if not any(k in selected for k in needed):
                self.errors.append(Issue("error", "header", "Fallback de 1 linha não encontrou nenhuma coluna esperada."))
                return
            cols = [selected[k] for k in needed if k in selected]
            self.filtered_df = temp[cols].copy()
            self.filtered_df.columns = [k for k in needed if k in selected]
            self.filtered_df = self.filtered_df.fillna(0).infer_objects(copy=False)
            for col in ["diagnostico", "destino", "parte"]:
                if col in self.filtered_df.columns:
                    self.filtered_df[col] = self.filtered_df[col].apply(normalize_str)
        except Exception as e:
            self.errors.append(Issue("error", "header_fallback", f"Erro no fallback de cabeçalho: {e}"))

    def _get_unified_rows(self, min_index: int, max_index: int) -> dict:
        rows = {i: [] for i in range(len(self.df.columns))}
        for i, col in enumerate(self.df.columns):
            for cell in self.df[col][min_index:max_index + 1]:
                cell_n = normalize_str(cell)
                if cell_n:
                    rows[i].append(cell_n)
        return {k: " ".join(v).strip() for k, v in rows.items()}

    def _map_columns(self, unified_rows: dict) -> bool:
        colunas_selecionadas = {}
        for idx, nome_col in unified_rows.items():
            for chave, sinonimos in self.CABECARIOS_VALIDOS.items():
                if any(termo in nome_col for termo in sinonimos):
                    colunas_selecionadas[chave] = idx
        chaves_finais = ["lote", "diagnostico", "destino", "parte", "emergencia"]
        colunas_finais = [colunas_selecionadas[c] for c in chaves_finais if c in colunas_selecionadas]

        if not colunas_finais:
            return False

        linha_inicio = max(colunas_selecionadas.values())
        self.filtered_df = self.df.iloc[linha_inicio + 1:, colunas_finais].copy()
        self.filtered_df.columns = [c for c in chaves_finais if c in colunas_selecionadas]
        self.filtered_df = self.filtered_df.fillna(0).infer_objects(copy=False)
        for col in ["diagnostico", "destino", "parte"]:
            if col in self.filtered_df.columns:
                self.filtered_df[col] = self.filtered_df[col].apply(normalize_str)
        return True

    # ------------- validação de linhas -------------
    def validate_rows(self):
        if self.filtered_df is None:
            self.valid_rows = []
            return []

        valid_rows: List[RowDif] = []
        for i, row in self.filtered_df.iterrows():
            row_dict = row.to_dict()

            # Garantir chaves
            for key in ["lote", "diagnostico", "destino", "parte", "emergencia"]:
                if key not in row_dict:
                    row_dict[key] = 0 if key in ("lote", "emergencia") else ""

            try:
                if RowDif.valide_row(row_dict):
                    
                    valid_rows.append(RowDif.from_row(row_dict))
                else:
                    self.warnings.append(Issue("warning", f"row[{i}] = {row_dict}", "Linha descartada por validação (campos obrigatórios ausentes ou lote <= 0)."))
            except Exception as e:
                self.errors.append(Issue("error", f"row[{i}]", f"Exceção ao validar linha: {e}"))

        self.valid_rows = valid_rows
        return valid_rows

    # ------------- saída (com fallbacks) -------------
    def to_records(self,
                   destino_default: str = "liberado",
                   emergencia_default: int = 0,
                   strict: bool = True,
                   best_effort_when_empty: bool = True) -> List[Dict]:
        """
        Converte para lista de dicionários padronizados.
        - strict=True: usa apenas self.valid_rows.
        - best_effort_when_empty=True: se não houver válidas, tenta montar a partir de filtered_df com defaults.
        """
        if self.valid_rows:
            return [
                {
                    "descricao": canonical_diag(r.diagnostico or r.parte),
                    "parte afetada": r.parte,
                    "quantidade": 1,
                    "lote": int(r.lote),
                    "emergencia": int(r.emergencia if r.emergencia is not None else emergencia_default),
                    "destino": (r.destino or destino_default),
                }
                for r in self.valid_rows
                if (r.diagnostico or r.parte)
            ]


        if strict:
            # sem registros válidos e modo estrito: retorna vazio
            if self.filtered_df is not None and not len(self.valid_rows):
                self.warnings.append(Issue("warning", "records", "Nenhuma linha válida encontrada em modo estrito."))
            return []

        # best-effort: tentar montar a partir de filtered_df
        if best_effort_when_empty and self.filtered_df is not None and not self.filtered_df.empty:
            self.warnings.append(Issue("warning", "records_best_effort", "Gerando registros em modo best-effort com defaults."))
            records: List[Dict] = []
            for i, row in self.filtered_df.iterrows():
                lote = self._coerce_int(row.get("lote", 0), default=0)
                diagnostico = self._nz(row.get("diagnostico", ""))
                parte = self._nz(row.get("parte", ""))
                destino = self._nz(row.get("destino", destino_default)) or destino_default
                try:
                    emergencia = parse_emergencia(row.get("emergencia", "0"), diagnostico)
                except Exception:
                    emergencia = int(emergencia_default)
                    self.warnings.append(Issue("warning", f"row[{i}].emergencia", "Falha ao parsear emergência no best-effort; usando default."))

                descricao = canonical_diag(diagnostico or parte)
                if lote <= 0 or not descricao:
                    # ainda assim, mantém aviso
                    self.warnings.append(Issue("warning", f"row[{i}]", "Linha ignorada no best-effort (lote <= 0 ou descrição vazia)."))
                    continue

                records.append({
                    "descricao": descricao,
                    "parte afetada": parte,
                    "quantidade": 1,
                    "lote": int(lote),
                    "emergencia": int(emergencia),
                    "destino": destino
                })
            return records

        return []

    def to_json(self,
                destino_default: str = "liberado",
                emergencia_default: int = 0,
                indent: Optional[int] = None,
                strict: bool = True,
                best_effort_when_empty: bool = True,
                wrap: bool = False) -> str:
        """
        Serializa para JSON.
        - wrap=False (padrão): retorna só o array de registros (compatível com seu pipeline atual).
        - wrap=True: retorna metadados (errors/warnings/counts) além dos registros.
        """
        records = self.to_records(destino_default=destino_default,
                                  emergencia_default=emergencia_default,
                                  strict=strict,
                                  best_effort_when_empty=best_effort_when_empty)
        if not wrap:
            return json.dumps(records, ensure_ascii=False, indent=indent)

        payload = {
            "records": records,
            "meta": {
                "errors": [issue.__dict__ for issue in self.errors],
                "warnings": [issue.__dict__ for issue in self.warnings],
                "counts": {
                    "input_rows": int(len(self.df)) if self.df is not None else 0,
                    "filtered_rows": int(len(self.filtered_df)) if self.filtered_df is not None else 0,
                    "valid_rows": int(len(self.valid_rows)),
                    "output_records": int(len(records)),
                }
            }
        }
        return json.dumps(payload, ensure_ascii=False, indent=indent)

class SifValidator:
    CABECARIOS_VALIDOS = {
        "lote": ["lote", "lotes", "lesao", "lesao lotes", "lotes lesao"]
    }

    def __init__(self, df: pd.DataFrame):
        self.df = df.fillna(0).infer_objects(copy=False)
        self.filtered_df: Optional[pd.DataFrame] = None
        self.valid_rows: List[Dict] = []  # não usamos Row no SIF, mas deixamos aqui por consistência
        self.header_start: Optional[int] = None
        self.header_end: Optional[int] = None

        # NOVO: coleta de problemas
        self.errors: List[Issue] = []
        self.warnings: List[Issue] = []

        self.normalized_valid_headers = {
            key: set(normalize_str(v) for v in valores)
            for key, valores in self.CABECARIOS_VALIDOS.items()
        }

        try:
            self._find_header()
        except Exception as e:
            msg = f"Falha inesperada no SifValidator: {e}"
            logger.exception(msg)
            self.errors.append(Issue("error", "validator", msg))

    # ---------------- utilidades ----------------
    @staticmethod
    def _valide_lote(lote: str) -> bool:
        try:
            return int(str(lote).strip()) > 0
        except Exception:
            return False

    # ---------------- header detection ----------------
    def _find_header(self):
        max_colunas = min(3, len(self.df.columns))
        indexs = []

        # varre as primeiras linhas e colunas prováveis
        for i in range(min(15, len(self.df))):
            for j in range(max_colunas):
                celula = normalize_str(self.df.iat[i, j])
                if not celula:
                    continue
                for _, valores in self.normalized_valid_headers.items():
                    if celula in valores:
                        indexs.append(i)

        if not indexs:
            # fallback: tenta com uma linha só
            self.warnings.append(Issue("warning", "header", "Cabeçalho do SIF não detectado por unificação. Tentando fallback de 1 linha."))
            self._fallback_single_row_header()
            return

        # guarda o intervalo do cabeçalho
        min_index, max_index = min(indexs), max(indexs)
        self.header_start = min_index
        self.header_end = max_index

        unified_headers = self._get_unified_rows(min_index, max_index)
        mapped = self._map_columns(unified_headers)
        if not mapped:
            self.warnings.append(Issue("warning", "header", "Mapeamento do SIF falhou na unificação. Tentando fallback de 1 linha."))
            self._fallback_single_row_header()

    def _fallback_single_row_header(self):
        # primeira linha com algum conteúdo
        header_row_idx = None
        for i in range(min(10, len(self.df))):
            row_vals = [normalize_str(v) for v in list(self.df.iloc[i].values)]
            if any(row_vals):
                header_row_idx = i
                break
        if header_row_idx is None:
            self.errors.append(Issue("error", "header", "Não foi possível identificar nenhuma linha de cabeçalho (SIF)."))
            return

        try:
            new_cols = []
            for c in list(self.df.iloc[header_row_idx].values):
                if isinstance(c, (int, float)) and not isinstance(c, bool):
                    new_cols.append(str(int(c)))
                else:
                    new_cols.append(normalize_str(c))

            temp = self.df.iloc[header_row_idx+1:].copy()

            if len(new_cols) != temp.shape[1]:
                self.warnings.append(Issue("warning", "header", f"Número de colunas no header({len(new_cols)}) difere do corpo({temp.shape[1]}). Alinhando/truncando."))
                min_cols = min(len(new_cols), temp.shape[1])
                new_cols = new_cols[:min_cols]
                temp = temp.iloc[:, :min_cols]

            temp.columns = new_cols

            # tentar achar a coluna 'lote' pelos sinônimos
            idx_lote_name = None
            for c in temp.columns:
                cn = normalize_str(c)
                if any(sn in cn for sn in self.CABECARIOS_VALIDOS["lote"]):
                    idx_lote_name = c
                    break
            if idx_lote_name is None:
                # fallback: assume primeira coluna é 'lote'
                idx_lote_name = temp.columns[0]
                self.warnings.append(Issue("warning", "header", f"Coluna 'lote' não encontrada pelos sinônimos. Assumindo '{idx_lote_name}' como lote."))

            # reordena p/ ter 'lote' primeiro
            cols = [idx_lote_name] + [c for c in temp.columns if c != idx_lote_name]
            self.filtered_df = temp[cols].copy()
            self.filtered_df.columns = ["lote"] + [c for c in temp.columns if c != idx_lote_name]
            self.filtered_df["lote"] = self.filtered_df["lote"].apply(normalize_str)
            self.filtered_df = self.filtered_df.reset_index(drop=True)

        except Exception as e:
            self.errors.append(Issue("error", "header_fallback", f"Erro no fallback do SIF: {e}"))

    def _get_unified_rows(self, min_index: int, max_index: int) -> dict:
        rows = {i: [] for i in range(len(self.df.columns))}
        for i, col in enumerate(self.df.columns):
            for cell in self.df[col][min_index:max_index + 1]:
                if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    cell_n = str(int(cell))
                else:
                    cell_n = normalize_str(cell)
                if cell_n:
                    rows[i].append(cell_n)
        return {k: " ".join(v).strip() for k, v in rows.items()}

    def _map_columns(self, unified_rows: dict) -> bool:
        colunas_selecionadas = {}
        # Mapeia sinônimos e também números detectados no "cabeçalho unificado"
        for idx, nome_col in unified_rows.items():
            for chave, sinonimos in self.CABECARIOS_VALIDOS.items():
                if any(termo in nome_col for termo in sinonimos):
                    colunas_selecionadas.setdefault(chave, idx)
            if self._valide_lote(nome_col):  # colunas de lotes numéricos
                colunas_selecionadas.setdefault(nome_col, idx)

        if "lote" not in colunas_selecionadas:
            return False

        idx_lote = colunas_selecionadas["lote"]
        coluna_lote = self.df.iloc[:, idx_lote].apply(normalize_str)

        linha_inicio = (self.header_end or 0) + 1

        # find última ocorrência de "total lote"
        mask_total = coluna_lote.str.contains("total lote", na=False)
        idxs_total = coluna_lote[mask_total].index
        if len(idxs_total) > 0:
            linha_fim = idxs_total.max()
        else:
            linha_fim = len(self.df) - 1
            self.warnings.append(Issue("error", "footer", "Nenhuma linha 'TOTAL LOTE' encontrada; usando final da planilha como limite."))

        if linha_fim < linha_inicio:
            self.warnings.append(Issue("error", "range", "Intervalo de dados inválido após o cabeçalho no SIF."))
            return False

        chaves = list(colunas_selecionadas.keys())
        colunas_indices = [colunas_selecionadas[k] for k in chaves]

        self.filtered_df = self.df.iloc[linha_inicio:linha_fim + 1, colunas_indices].copy()
        self.filtered_df.columns = chaves
        self.filtered_df["lote"] = self.filtered_df["lote"].apply(normalize_str)
        self.filtered_df = self.filtered_df.reset_index(drop=True)
        return True

@dataclass
class Section:
    parte: str
    start_idx: int
    total_idx: int

class SifSelectionMapper:
    PARTES_PERMITIDAS = {
        "cabeca": ["cabeca", "papada"],
        "utero": ["utero"],
        "intestino": ["intestino", "estomago", "bexiga"],
        "lingua": ["lingua"],
        "coracao": ["coracao"],
        "pulmao": ["pulmao"],
        "figado": ["figado"],
        "carcaca": ["carcaca"],
        "rim": ["rim", "rins"]
    }

    # IMPORTANTe: para GI/Biliar, padronizamos com SLASH, pois o pipeline de comandos mapeia
    # "contaminacao gastrointestinal/biliar" -> "CONTAMINAÇÃO GASTROINTESTINAL E BILIAR" via map_descricao.

    DIAG_CANON = {
        # GI/Biliar – todas as variantes convergem aqui:
        "cont gastrointestinal e biliar": "contaminacao gastrointestinalbiliar",
        "cont gastrointestinal biliar":  "contaminacao gastrointestinalbiliar",
        "contaminacao gastrointestinal e biliar": "contaminacao gastrointestinalbiliar",
        "contaminacao gastrointestinal biliar":   "contaminacao gastrointestinalbiliar",
        "contaminacao gastrointestinal/biliar":   "contaminacao gastrointestinalbiliar",
        "contaminacao gastrointestinalbiliar":    "contaminacao gastrointestinalbiliar",
        # Não GI:
        "contaminacao nao gastrointestinal": "contaminacao nao gastrointestinal",
        # Outros exemplos:
        "alteracao restrita": "alteracao restrita",
    }


    def __init__(self, df_or_validator: pd.DataFrame | SifValidator):

        # aceita um df bruto ou um validator pronto (para carregar meta)
        if isinstance(df_or_validator, SifValidator):
            self.validator = df_or_validator
            self.df = df_or_validator.filtered_df
        else:
            self.validator = SifValidator(df_or_validator)
            self.df = self.validator.filtered_df

        self.sections: List[Section] = []
        self.warnings: List[Issue] = []  # avisos do mapper
        self.errors: List[Issue] = []    # erros do mapper

        # Sem dados filtrados: nada a fazer
        if self.df is None or self.df.empty:
            self.warnings.append(
                Issue("warning", "mapper.init",
                      "Nenhum dado filtrado disponível no SIF (df vazio ou header não mapeado).")
            )
            self.lote_cols = []
            return

        # [CORE FIX] Garante a existência de 'lote'; se não houver, assume a primeira coluna como descrição.
        if "lote" not in self.df.columns:
            self.warnings.append(
                Issue("warning", "mapper.init",
                      "DataFrame SIF sem coluna 'lote'; assumindo a primeira coluna como descrição.")
            )
            cols = list(self.df.columns)
            if cols:  # renomeia a primeira coluna para 'lote'
                cols[0] = "lote"
                self.df.columns = cols

        # Demais colunas tratadas como lotes/valores
        self.lote_cols = [c for c in self.df.columns if c != "lote"]

        self._build_sections()


    def _build_sections(self):
        # [CORE FIX] Se ainda não há 'lote', não tenta construir seções
        if "lote" not in self.df.columns:
            self.warnings.append(
                Issue("warning", "mapper.sections",
                      "Coluna 'lote' ausente após normalização; pulando identificação de seções.")
            )
            return

        sparte, start_idx, total_idx = "", 0, 0
        partes_norm = {p: [normalize_str(x) for x in xs]
                       for p, xs in self.PARTES_PERMITIDAS.items()}

        for index, row in enumerate(self.df["lote"]):
            tokens = str(row).split(" ")
            # identifica início de uma seção por palavras que batem com PARTES_PERMITIDAS
            for parte, arr in partes_norm.items():
                for palavra in tokens:
                    if normalize_str(palavra) in arr:
                        sparte = parte
                        start_idx = index
                        break
                else:
                    continue
                break

            if normalize_str(str(row)) == "total lote":
                total_idx = index
                self.sections.append(
                    Section(parte=sparte, start_idx=start_idx, total_idx=total_idx)
                )

        if not self.sections:
            self.warnings.append(
                Issue("error", "mapper.sections",
                      "Nenhuma seção identificada (sem 'TOTAL LOTE'?). Best-effort poderá ser necessário.")
            )

    def to_records(self,
                   destino_default: str = "liberado",
                   emergencia_default: int = 0,
                   incluir_total: bool = False,
                   strict: bool = True,
                   best_effort_when_empty: bool = True,
                   return_json: bool = False,
                   wrap: bool = False,
                   indent: Optional[int] = None):

        records: List[Dict] = []

        if self.df is None or self.df.empty:
            payload = self._wrap_payload(records) if wrap else (
                json.dumps(records, ensure_ascii=False, indent=indent) if return_json else records
            )
            return payload

        if self.sections:
            for s in self.sections:
                start = s.start_idx + 1
                end = s.total_idx + (1 if incluir_total else 0)
                for r in range(start, end):
                    # segura: só acessamos 'lote' se existir
                    if "lote" not in self.df.columns:
                        continue
                    desc = str(self.df["lote"].iloc[r])
                    desc_norm = normalize_str(desc)

                    if not desc_norm or "total lote" in desc_norm:
                        continue
                    for c in self.lote_cols:
                        val = self.df.at[r, c]
                        try:
                            v = float(val)
                        except Exception:
                            continue
                        if v > 0:
                            try:
                                lote_int = int(float(c))
                            except Exception:
                                lote_int = c
                            records.append({
                                "descricao": canonical_diag(desc),
                                "parte afetada": s.parte,
                                "quantidade": int(v),
                                "lote": lote_int,
                                "emergencia": int(emergencia_default),
                                "destino": destino_default
                            })

        elif not strict and best_effort_when_empty:

            self.warnings.append(
                Issue("warning", "mapper.best_effort",
                      "Gerando registros SIF em modo best-effort sem seções.")
            )

            partes_norm = {p: [normalize_str(x) for x in xs]
                           for p, xs in self.PARTES_PERMITIDAS.items()}

            # [CORE FIX] Use 'lote' se existir; caso contrário, use a primeira coluna como descrição
            desc_series = self.df["lote"] if "lote" in self.df.columns else self.df.iloc[:, 0]

            for r, row_val in desc_series.items():
                desc = str(row_val)
                desc_norm = normalize_str(desc)
                if not desc_norm or "total lote" in desc_norm:
                    continue

                # infere parte pela primeira palavra conhecida
                detected_parte = ""
                tokens = desc_norm.split()
                for token in tokens:
                    for parte, arr in partes_norm.items():
                        if token in arr:
                            detected_parte = parte
                            break
                    if detected_parte:
                        break

                for c in self.lote_cols:
                    val = self.df.at[r, c]
                    try:
                        v = float(val)
                    except Exception:
                        continue
                    if v > 0:
                        try:
                            lote_int = int(float(c))
                        except Exception:
                            lote_int = c
                        records.append({
                            "descricao": canonical_diag(desc),
                            "parte afetada": detected_parte,
                            "quantidade": int(v),
                            "lote": lote_int,
                            "emergencia": int(emergencia_default),
                            "destino": destino_default
                        })

        if wrap:
            payload = self._wrap_payload(records)
            return json.dumps(payload, ensure_ascii=False, indent=indent) if return_json else payload

        if return_json:
            return json.dumps(records, ensure_ascii=False, indent=indent)

        return records

    def to_json(self,
                destino_default: str = "liberado",
                emergencia_default: int = 0,
                incluir_total: bool = False,
                strict: bool = True,
                best_effort_when_empty: bool = True,
                wrap: bool = False,
                indent: Optional[int] = None) -> str:
        return self.to_records(destino_default=destino_default,
                               emergencia_default=emergencia_default,
                               incluir_total=incluir_total,
                               strict=strict,
                               best_effort_when_empty=best_effort_when_empty,
                               return_json=True,
                               wrap=wrap,
                               indent=indent)

    def _wrap_payload(self, records: List[Dict]) -> Dict:
        # agrega meta do validator + do mapper
        errors = [issue.__dict__ for issue in (self.validator.errors if hasattr(self, "validator") else [])] + \
                 [issue.__dict__ for issue in self.errors]
        warnings = [issue.__dict__ for issue in (self.validator.warnings if hasattr(self, "validator") else [])] + \
                   [issue.__dict__ for issue in self.warnings]

        return {
            "records": records,
            "meta": {
                "errors": errors,
                "warnings": warnings,
                "counts": {
                    "input_rows": int(len(self.validator.df)) if hasattr(self, "validator") and self.validator.df is not None else 0,
                    "filtered_rows": int(len(self.df)) if self.df is not None else 0,
                    "sections": int(len(self.sections)) if hasattr(self, "sections") else 0,
                    "output_records": int(len(records)),
                }
            }
        }

# Mantém o mesmo Issue, normalize_str, para_int, parse_peso_br, logger já importados no topo do arquivo.
@dataclass
class RowGta:
    data: Optional[pd.Timestamp]
    numero_gta: str
    serie: str
    machos: int
    femeas: int
    lote: int
    peso_medio: float

    @property
    def total(self) -> int:
        return int(max(0, (self.machos or 0)) + max(0, (self.femeas or 0)))


class GtaValidator:
    """
    Validador para planilhas GTA. Agora inclui as mesmas validações do seu GtaFormater:
      - ClearNull: remove linhas com <5 valores não-nulos;
      - ConferirNumeros: Nr.GTA duplicado => erro (e drop no modo não-estrito);
      - ConferirPesoM: não-nulo, constante, e 10 <= peso <= 150;
      - ConferirSerie: todas as séries devem ser string;
      - ConferirSexos: por linha, limites de 0<=(machos+femeas) e <=150 por sexo;
      - AjustarOrdem: por lote; dentro do lote: machos desc, femeas desc.
    """

    CABECALHOS_VALIDOS = {
        "data": ["data", "dt", "emissao", "data emissao", "dt emissao"],
        "numero_gta": ["nr.gta", "nº gta", "numero gta", "nr gta", "gta", "numero", "n"],
        "serie": ["serie", "série", "sr", "sr."],
        "machos": ["quantidade machos", "qtd machos", "machos", "m", "qtd m", "qtde machos"],
        "femeas": ["quantidade femeas", "quantidade fêmeas", "qtd femeas", "qtd fêmeas", "femeas", "fêmeas", "f", "qtd f"],
        "lote": ["lote", "tatuagem", "lote tatuagem"],
        "peso_medio": ["peso medio", "peso médio", "peso medio kg", "peso médio kg", "peso", "peso kg", "peso medio (kg)"],
    }

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        self.df = self.df.fillna(0).infer_objects(copy=False)

        self.errors: List[Issue] = []
        self.warnings: List[Issue] = []
        self.filtered_df: Optional[pd.DataFrame] = None
        self.valid_rows: List[RowGta] = []

        # controle de comportamento
        self.strict_on_global = True     # bloqueia saída se houver erros "globais"
        self.drop_duplicated_on_non_strict = True  # no modo não-estrito, remove duplicados mantendo a 1ª ocorrência

        self.normalized_valid_headers = {
            key: set(normalize_str(v) for v in values)
            for key, values in self.CABECALHOS_VALIDOS.items()
        }

        try:
            self._find_header()
            # === Validações "globais" de estrutura/dados (espelhando GtaFormater) ===
            if self.filtered_df is not None and not self.filtered_df.empty:
                self._clear_null_rows(min_non_null=5)
                self._check_duplicated_gta_numbers()
                self._check_peso_medio_consistency()
                self._check_serie_types()
            # === Validação linha-a-linha com limites de sexo e regras mínimas ===
            self._validate_rows()
        except Exception as e:
            msg = f"Falha inesperada no GtaValidator: {e}"
            logger.exception(msg)
            self.errors.append(Issue("error", "validator", msg))

    # -------------------------
    # Detectar/Mapear Cabeçalho
    # -------------------------


    def _find_header(self):
        # 1) tente mapear direto das colunas (se você já aplicou o _map_from_columns)
        if hasattr(self, "_map_from_columns") and self._map_from_columns():
            return

        # 2) fallback: detecção por células (com proteção para números)
        max_colunas = min(4, len(self.df.columns))
        indexs = []

        for i in range(min(15, len(self.df))):
            for j in range(max_colunas):
                cell = self.df.iat[i, j]
                if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    celula = str(int(cell))
                else:
                    celula = normalize_str(cell)
                if not celula:
                    continue
                for _, valores in self.normalized_valid_headers.items():
                    if celula in valores:
                        indexs.append(i)

        if not indexs:
            self.warnings.append(Issue("warning", "header",
                                    "Cabeçalho GTA não detectado por unificação. Tentando fallback de 1 linha."))
            self._fallback_single_row_header()
            return

        min_index, max_index = min(indexs), max(indexs)
        unified = self._get_unified_rows(min_index, max_index)
        mapped = self._map_columns(unified)
        if not mapped:
            self.warnings.append(Issue("warning", "header",
                                    "Mapeamento GTA falhou na unificação. Tentando fallback de 1 linha."))
            self._fallback_single_row_header()

    def _fallback_single_row_header(self):
        header_row_idx = None
        for i in range(min(10, len(self.df))):
            row_vals = [normalize_str(v) for v in list(self.df.iloc[i].values)]
            if any(row_vals):
                header_row_idx = i
                break

        if header_row_idx is None:
            self.errors.append(Issue("error", "header", "Não foi possível identificar nenhuma linha de cabeçalho (GTA)."))
            return

        try:
            new_cols = []
            for c in list(self.df.iloc[header_row_idx].values):
                if isinstance(c, (int, float)) and not isinstance(c, bool):
                    new_cols.append(str(int(c)))
                else:
                    new_cols.append(normalize_str(c))

            temp = self.df.iloc[header_row_idx + 1:].copy()
            if len(new_cols) != temp.shape[1]:
                self.warnings.append(Issue(
                    "warning", "header",
                    f"Nº de colunas no header({len(new_cols)}) difere do corpo({temp.shape[1]}). Alinhando/truncando."
                ))
                min_cols = min(len(new_cols), temp.shape[1])
                new_cols = new_cols[:min_cols]
                temp = temp.iloc[:, :min_cols]

            temp.columns = new_cols

            selected = {}
            for chave, sinonimos in self.CABECALHOS_VALIDOS.items():
                for c in temp.columns:
                    if any(sn in normalize_str(c) for sn in sinonimos):
                        selected[chave] = c
                        break

            needed_any = ["lote", "machos", "femeas"]
            if not any(k in selected for k in needed_any):
                self.errors.append(Issue("error", "header", "Fallback de 1 linha não encontrou colunas mínimas esperadas (lote/machos/femeas)."))
                return

            display_order = ["data", "numero_gta", "serie", "machos", "femeas", "lote", "peso_medio"]
            cols = [selected[k] for k in display_order if k in selected]
            self.filtered_df = temp[cols].copy()
            self.filtered_df.columns = [k for k in display_order if k in selected]
            self.filtered_df = self.filtered_df.fillna(0).infer_objects(copy=False)

        except Exception as e:
            self.errors.append(Issue("error", "header_fallback", f"Erro no fallback de cabeçalho GTA: {e}"))

    def _get_unified_rows(self, min_index: int, max_index: int) -> dict:
        rows = {i: [] for i in range(len(self.df.columns))}
        for i, col in enumerate(self.df.columns):
            for cell in self.df[col][min_index:max_index + 1]:
                if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    cell_n = str(int(cell))
                else:
                    cell_n = normalize_str(cell)
                if cell_n:
                    rows[i].append(cell_n)
        return {k: " ".join(v).strip() for k, v in rows.items()}

    
    def _map_from_columns(self) -> bool:
        """
        Mapeia as colunas já lidas pelo pandas (df.columns) para os nomes canônicos.
        Não recorta linhas. Retorna True em caso de sucesso.
        """
        if self.df is None or self.df.empty or len(self.df.columns) == 0:
            return False

        # índice -> nome normalizado da coluna
        cols_norm = {i: normalize_str(c) for i, c in enumerate(list(self.df.columns))}

        colunas_selecionadas = {}
        for idx, nome_col in cols_norm.items():
            for chave, sinonimos in self.CABECALHOS_VALIDOS.items():
                if any(sn in nome_col for sn in sinonimos):
                    colunas_selecionadas.setdefault(chave, idx)

        # precisamos, no mínimo, de lote/machos/femeas (como na sua validação)
        if not any(k in colunas_selecionadas for k in ["lote", "machos", "femeas"]):
            return False

        chaves_ordenadas = ["data", "numero_gta", "serie", "machos", "femeas", "lote", "peso_medio"]
        presentes = [c for c in chaves_ordenadas if c in colunas_selecionadas]
        idxs = [colunas_selecionadas[c] for c in presentes]

        try:
            self.filtered_df = self.df.iloc[:, idxs].copy()
            self.filtered_df.columns = presentes
            self.filtered_df = self.filtered_df.fillna(0).infer_objects(copy=False)
            return True
        except Exception as e:
            self.warnings.append(Issue("warning", "header.columns_map", f"Falha ao mapear por df.columns: {e}"))
            return False


    # -------------------------
    # Regras globais do GtaFormater
    # -------------------------

    def _clear_null_rows(self, min_non_null: int = 5):
        """Remove linhas com menos de `min_non_null` valores não-nulos (espelha ClearNull)."""
        if self.filtered_df is None or self.filtered_df.empty:
            return
        to_drop = []
        for i in range(len(self.filtered_df)):
            nn = pd.Series(self.filtered_df.iloc[i]).replace("", pd.NA).notna().sum()
            if nn < min_non_null:
                to_drop.append(i)
        if to_drop:
            self.warnings.append(Issue("warning", "clear_null", f"Removendo {len(to_drop)} linha(s) com poucos dados: {to_drop[:5]}{'...' if len(to_drop)>5 else ''}"))
            self.filtered_df = self.filtered_df.drop(index=to_drop, errors="ignore").reset_index(drop=True)

    def _check_duplicated_gta_numbers(self):
        """Erro se Nr.GTA duplicado (espelha _ConferirNumeros)."""
        if self.filtered_df is None or "numero_gta" not in self.filtered_df.columns:
            return
        ser = self.filtered_df["numero_gta"].astype(str)
        dups_mask = ser.duplicated(keep="first")
        if dups_mask.any():
            dups = sorted(ser[dups_mask].unique().tolist())
            self.errors.append(Issue("error", "global.duplicados", f"Nr.GTA duplicado encontrado: {dups[:10]}{'...' if len(dups)>10 else ''}"))
            # no modo não-estrito, remove duplicados mantendo o primeiro
            if not self.strict_on_global and self.drop_duplicated_on_non_strict:
                count_before = len(self.filtered_df)
                self.filtered_df = self.filtered_df.loc[~dups_mask].reset_index(drop=True)
                count_after = len(self.filtered_df)
                self.warnings.append(Issue("warning", "global.duplicados", f"Removidas {count_before-count_after} linha(s) duplicadas de Nr.GTA mantendo a primeira ocorrência."))

    def _check_peso_medio_consistency(self):
        """Peso médio: não-nulo, parser BR seguro, constante (±0,01), 10 <= mediana <= 150."""
        if self.filtered_df is None or "peso_medio" not in self.filtered_df.columns:
            return

        col_raw = self.filtered_df["peso_medio"]

        # trata branco/nulo
        is_blank = pd.isna(col_raw) | (pd.Series(col_raw).astype(str).str.strip().eq(""))
        if is_blank.any():
            self.errors.append(Issue("error", "global.peso_medio",
                                    "A Gta contém valores de peso médio não preenchidos."))
            return

        def _to_float_br_safe(x) -> float:
            """
            Converte valores como '106,13', '106.13', '106,13 kg', '106 kg' para float.
            - Remove tudo que não for dígito, vírgula, ponto ou sinal.
            - Se há vírgula e ponto: o último separador encontrado é decimal; o outro é milhar.
            - Se só vírgula: trata como decimal.
            - Se só ponto: usa como está.
            """
            s = str(x).strip()
            s = re.sub(r"[^\d,.\-]+", "", s)
            if not s:
                return float("nan")
            if ("," in s) and ("." in s):
                if s.rfind(",") > s.rfind("."):
                    s = s.replace(".", "")
                    s = s.replace(",", ".")
                else:
                    s = s.replace(",", "")
            elif "," in s:
                s = s.replace(",", ".")
            try:
                return float(s)
            except Exception:
                return float("nan")

        # normaliza para float
        colf = col_raw.map(_to_float_br_safe).astype(float)

        if colf.isna().any():
            self.errors.append(Issue("error", "global.peso_medio",
                                    "A Gta contém valores de peso médio inválidos."))
            return

        # Constância com tolerância de ruído (evita divergências por arredondamento)
        tol = 0.01
        med = float(colf.median())
        if (colf - med).abs().max() > tol:
            self.errors.append(Issue("error", "global.peso_medio",
                                    "Peso médio divergente entre linhas."))
            # pode continuar, mas já marca erro

        # Faixa com base na mediana (mais estável)
        if med > 150:
            self.errors.append(Issue("error", "global.peso_medio",
                                    "Peso médio maior que 150."))
        elif med < 10:
            self.errors.append(Issue("error", "global.peso_medio",
                                    "Peso médio menor que 10."))

        # persiste a coluna normalizada
        self.filtered_df["peso_medio"] = colf
        
    def _check_serie_types(self):
        """Todas as séries devem ser string (espelha _ConeferirSerie)."""
        if self.filtered_df is None or "serie" not in self.filtered_df.columns:
            return
        invalid_idxs = []
        for i, v in enumerate(self.filtered_df["serie"]):
            if not isinstance(v, str):
                invalid_idxs.append((i, v))
        if invalid_idxs:
            self.errors.append(Issue("error", "global.serie", f"Série inválida (não-string) em {len(invalid_idxs)} linha(s). Exemplos: {invalid_idxs[:5]}"))
            # no modo não-estrito, converter para string
            if not self.strict_on_global:
                self.filtered_df["serie"] = self.filtered_df["serie"].map(lambda x: "" if x is None else str(x))
                self.warnings.append(Issue("warning", "global.serie", "Séries não-string convertidas para string em modo não-estrito."))

    # -------------------------
    # Validação de linhas
    # -------------------------

    def _parse_data(self, val) -> Optional[pd.Timestamp]:
        try:
            dt = pd.to_datetime(val, dayfirst=True, errors="coerce")
            return pd.Timestamp(dt) if pd.notnull(dt) else None
        except Exception:
            return None

    def _validate_rows(self):
        if self.filtered_df is None or self.filtered_df.empty:
            self.valid_rows = []
            if self.filtered_df is not None:
                self.errors.append(Issue("error", "rows", "Nenhuma linha disponível após mapeamento de cabeçalho (GTA)."))
            return
        # Se houve erros "globais" e estamos em modo estrito, não seguimos
        has_global_errors = any(iss.level == "error" and str(iss.where).startswith("global") for iss in self.errors)
        if has_global_errors and self.strict_on_global:
            self.warnings.append(Issue("warning", "rows", "Validações globais falharam em modo estrito; registros não serão gerados."))
            self.valid_rows = []
            return

        valid: List[RowGta] = []

        for i, row in self.filtered_df.iterrows():
            rd = {k: row.get(k, 0) for k in ["data", "numero_gta", "serie", "machos", "femeas", "lote", "peso_medio"]}

            data = self._parse_data(rd.get("data")) if "data" in self.filtered_df.columns else None
            numero_gta = str(rd.get("numero_gta", "") or "").strip()
            serie = str(rd.get("serie", "") or "").strip()

            machos = para_int(rd.get("machos", 0)) if "machos" in self.filtered_df.columns else 0
            femeas = para_int(rd.get("femeas", 0)) if "femeas" in self.filtered_df.columns else 0
            lote = para_int(rd.get("lote", 0)) if "lote" in self.filtered_df.columns else 0

            # peso já normalizado em _check_peso_medio_consistency; fazer fallback se necessário
            try:
                peso_medio = float(rd.get("peso_medio", 0))
            except Exception:
                try:
                    peso_medio = parse_peso_br(rd.get("peso_medio", 0))
                except Exception:
                    try:
                        peso_medio = float(str(rd.get("peso_medio", 0)).replace(",", "."))
                    except Exception:
                        peso_medio = 0.0

            # --- Regras do GtaFormater: _ConferirSexos (por linha) e mínimos ---
            # limites superiores
            if machos > 250 or femeas > 250:
                self.errors.append(Issue("error", f"row[{i}]", f"Quantidade muito alta de suínos (Nr.GTA {numero_gta})."))
                continue
            # total mínimo
            if machos < 1 and femeas < 1:
                self.errors.append(Issue("error", f"row[{i}]", f"Quantidade de suínos menor que 1 (Nr.GTA {numero_gta})."))
                continue
            # demais mínimos antigos
            if lote <= 0:
                self.errors.append(Issue("error", f"row[{i}]", "Lote inválido (<=0)."))
                continue

            valid.append(RowGta(
                data=data,
                numero_gta=numero_gta,
                serie=serie,
                machos=int(max(0, machos)),
                femeas=int(max(0, femeas)),
                lote=int(lote),
                peso_medio=float(max(0.0, peso_medio)),
            ))

        # --- Ajustar ordem (espelha _AjustarOrdem) ---
        # por lote asc; dentro do lote: machos desc, femeas desc
        valid.sort(key=lambda r: (r.lote, -r.machos, -r.femeas))
        self.valid_rows = valid

    # -------------------------
    # Saída
    # -------------------------

    def to_records(self, strict: bool = True, best_effort_when_empty: bool = True) -> List[Dict]:
        # respeita bloqueio por erros globais em modo estrito
        has_global_errors = any(iss.level == "error" and str(iss.where).startswith("global") for iss in self.errors)
        if has_global_errors and (strict or self.strict_on_global):
            self.warnings.append(Issue("warning", "records", "Erros globais impedem geração de registros em modo estrito."))
            return []

        if self.valid_rows:
            return [
                {
                    "data": (r.data.strftime("%Y-%m-%d") if isinstance(r.data, pd.Timestamp) else None),
                    "numero_gta": r.numero_gta,
                    "serie": r.serie,
                    "machos": int(r.machos),
                    "femeas": int(r.femeas),
                    "total": int(r.total),
                    "lote": int(r.lote),
                    "peso_medio": float(r.peso_medio),
                }
                for r in self.valid_rows
            ]

        if strict:
            if self.filtered_df is not None and not len(self.valid_rows):
                self.errors.append(Issue("warning", "records", "Nenhuma linha válida encontrada em modo estrito (GTA)."))
            return []

        # best-effort (mantido como fallback — aqui não altera suas novas regras globais)
        records: List[Dict] = []
        if best_effort_when_empty and self.filtered_df is not None and not self.filtered_df.empty:
            self.warnings.append(Issue("warning", "records_best_effort", "Gerando registros GTA em modo best-effort."))
            for i, row in self.filtered_df.iterrows():
                lote = para_int(row.get("lote", 0)) if "lote" in self.filtered_df.columns else 0
                machos = para_int(row.get("machos", 0)) if "machos" in self.filtered_df.columns else 0
                femeas = para_int(row.get("femeas", 0)) if "femeas" in self.filtered_df.columns else 0
                if lote <= 0 or (machos + femeas) <= 0:
                    self.warnings.append(Issue("warning", f"row[{i}]", "Linha ignorada no best-effort (lote<=0 ou total=0)."))
                    continue
                try:
                    peso_medio = parse_peso_br(row.get("peso_medio", 0)) if "peso_medio" in self.filtered_df.columns else 0.0
                except Exception:
                    try:
                        peso_medio = float(str(row.get("peso_medio", 0)).replace(",", "."))
                    except Exception:
                        peso_medio = 0.0
                data = None
                if "data" in self.filtered_df.columns:
                    data = self._parse_data(row.get("data"))

                records.append({
                    "data": (data.strftime("%Y-%m-%d") if isinstance(data, pd.Timestamp) else None),
                    "numero_gta": str(row.get("numero_gta", "") or "").strip() if "numero_gta" in self.filtered_df.columns else "",
                    "serie": str(row.get("serie", "") or "").strip() if "serie" in self.filtered_df.columns else "",
                    "machos": int(max(0, machos)),
                    "femeas": int(max(0, femeas)),
                    "total": int(max(0, machos) + max(0, femeas)),
                    "lote": int(lote),
                    "peso_medio": float(peso_medio or 0.0),
                })
        # manter ordenação no resultado também
        records.sort(key=lambda r: (r["lote"], -r["machos"], -r["femeas"]))
        return records

    def to_json(self, *, strict: bool = True, best_effort_when_empty: bool = True,
                wrap: bool = False, indent: Optional[int] = None) -> str:
        records = self.to_records(strict=strict, best_effort_when_empty=best_effort_when_empty)
        if not wrap:
            return json.dumps(records, ensure_ascii=False, indent=indent)
        payload = {
            "records": records,
            "meta": {
                "errors": [iss.__dict__ for iss in self.errors],
                "warnings": [iss.__dict__ for iss in self.warnings],
                "counts": {
                    "input_rows": int(len(self.df)) if self.df is not None else 0,
                    "filtered_rows": int(len(self.filtered_df)) if self.filtered_df is not None else 0,
                    "valid_rows": int(len(self.valid_rows)),
                    "output_records": int(len(records)),
                }
            }
        }
        return json.dumps(payload, ensure_ascii=False, indent=indent)