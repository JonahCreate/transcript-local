"""Authenticated update requests handed to the independent supervisor."""
import os
from .store import ACTIVE, atomic_json, read_json

UPDATING = {'requested', 'preparing', 'validating', 'switching'}

class Updater:
    def __init__(self, manager, restart=None, models=None):
        self.manager = manager
        self.models = models
        self.rt = manager.store.root / 'runtime'

    def status(self):
        state = read_json(self.rt / 'update.json', {'status': 'idle', 'message': ''})
        return {**state, 'available': os.environ.get('IHT_MANAGED') == '1' and (self.rt / 'supervisor.json').is_file()}

    def start(self):
        with self.manager.lock:
            if not self.status()['available']:
                raise ValueError('請從 tr 選單開啟背景服務，才能使用安全更新。')
            if self.status()['status'] in UPDATING:
                raise ValueError('更新正在進行中。')
            if self.models and self.models.status()['download']['status'] == 'downloading':
                raise ValueError('模型正在下載，請等待下載完成。')
            if any(j['status'] in ACTIVE for j in self.manager.store.list()):
                raise ValueError('有任務正在處理或排隊，請先等待完成或停止。')
            control = read_json(self.rt / 'supervisor.json')
            self.manager.updating = True
            atomic_json(self.rt / 'update.json', {'status': 'requested', 'message': '已送出更新要求，正在準備。'})
            atomic_json(self.rt / 'update-request.json', {'secret': control['secret']})
