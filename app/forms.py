# app/forms.py
from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import Optional
from flask_wtf.file import FileAllowed, FileField, FileRequired

# Você pode mover isso para um módulo de constantes se preferir.
MODEL_CHOICES = [
    ("padrao", "Padrão"),
    ("alternativo", "Alternativo"),
]

ALLOWED_EXTENSIONS = ["csv", "txt", "xlsx"]

class UploadFileForm(FlaskForm):
    file = FileField(
        "Arquivo",
        validators=[FileRequired(), FileAllowed(ALLOWED_EXTENSIONS, "Permitidos: CSV, TXT, XLSX")],
    )
    model = SelectField("Modelo", choices=MODEL_CHOICES)
    submit = SubmitField("Enviar")

# Se quiser rótulos específicos por tipo:
class UploadGtaForm(UploadFileForm):
    pass

class UploadDifForm(UploadFileForm):
    pass

class UploadSifForm(UploadFileForm):
    pass


class UploadVendasForm(FlaskForm):
    """Planilha de vendas (comercializacao). O periodo e lido do titulo da planilha;
    os campos de data so sao necessarios quando o titulo nao traz o periodo."""
    file = FileField(
        "Planilha de vendas",
        validators=[FileRequired(), FileAllowed(["xlsx"], "Use um arquivo .xlsx")],
    )
    periodo_ini = StringField("Inicio do periodo (dd/mm/aaaa)", validators=[Optional()])
    periodo_fim = StringField("Fim do periodo (dd/mm/aaaa)", validators=[Optional()])
    obs = StringField("Observacoes", validators=[Optional()])
    submit = SubmitField("Enviar planilha")
