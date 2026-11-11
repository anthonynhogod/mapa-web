# app/runner/exceptions.py
class FalhaNoJavascript(Exception):
    def __init__(self, dia=None, erro=None):
        super().__init__(f"Falha JS no dia={dia}: {erro}")

class FalhaExecucaoNavegador(Exception):
    def __init__(self, dia=None, erro=None):
        super().__init__(f"Falha execução navegador no dia={dia}: {erro}")