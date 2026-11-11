import threading, queue
from dataclasses import dataclass
from app import db

@dataclass
class _ExecutorQueue:
    q: queue.Queue
    started: bool = False

    def enqueue(self, job_id: int):
        self.q.put(job_id)

_queue_holder = _ExecutorQueue(queue.Queue())
_active_threads = {}  # worker_name -> (thread, event)

def get_queue() -> _ExecutorQueue:
    return _queue_holder

def start_pool(app, num_workers: int = 2, profile: str = "balanced", force_restart: bool = False):
    """Inicia N workers em threads. Sempre permite iniciar, para existentes apenas se force_restart."""
    from app.models import WorkerSession
    try:
        app = app._get_current_object()
    except Exception:
        pass
    
    # Se forçando, para todos os workers ativos
    if force_restart:
        for name, (t, event) in list(_active_threads.items()):
            event.set()  # Sinaliza parada
            t.join(timeout=5)  # Aguarda
            worker = WorkerSession.query.filter_by(name=name).first()
            if worker:
                worker.status = "PARADO"
                db.session.commit()
        _active_threads.clear()
        _queue_holder.started = False  # Reseta flag
    
    # Sempre inicia novos, pulando duplicatas ativas
    from .worker import Worker
    for i in range(num_workers):
        worker_name = f"worker-{i}"
        if worker_name in _active_threads:
            continue  # Já ativo, pula
        
        stop_event = threading.Event()
        t = threading.Thread(target=Worker(app, name=worker_name, profile=profile, stop_event=stop_event).run, daemon=True)
        _active_threads[worker_name] = (t, stop_event)
        t.start()
    
    _queue_holder.started = True  # Sempre marca como iniciado