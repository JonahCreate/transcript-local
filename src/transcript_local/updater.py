import os
import subprocess
import threading
from pathlib import Path
from .store import ACTIVE

class Updater:
    def __init__(self, manager, restart=None, models=None):
        self.manager = manager
        self.restart = restart
        self.models = models
        self.project = Path(os.environ['TRANSCRIPT_LOCAL_PROJECT']).resolve() if os.environ.get('TRANSCRIPT_LOCAL_PROJECT') else None
        self.state = {'status': 'idle', 'message': ''}

    def status(self):
        return {**self.state, 'available': bool(self.project and (self.project / 'update.sh').is_file() and self.restart)}

    def start(self):
        with self.manager.lock:
            if not self.status()['available']:
                raise ValueError('無法找到專案資料夾，請使用 start.sh 啟動。')
            if self.state['status'] == 'running':
                raise ValueError('更新正在進行中。')
            if self.models and self.models.status()['download']['status'] == 'downloading':
                raise ValueError('模型正在下載，請等待下載完成。')
            if any(j['status'] in ACTIVE for j in self.manager.store.list()):
                raise ValueError('有任務正在處理或排隊，請先等待完成或停止。')
            self.manager.updating = True
            self.state = {'status': 'running', 'message': '正在更新，請保持網頁和程式開啟。'}
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            result = subprocess.run([str(self.project / 'update.sh')], cwd=self.project, stdin=subprocess.DEVNULL, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
            if result.returncode:
                raise RuntimeError(result.stdout[-3000:] or '更新失敗')
            self.state = {'status': 'complete', 'message': '更新完成，正在重新開啟。'}
            self.restart()
        except Exception as exc:
            with self.manager.lock:
                self.manager.updating = False
                self.state = {'status': 'failed', 'message': str(exc)[-3000:]}
