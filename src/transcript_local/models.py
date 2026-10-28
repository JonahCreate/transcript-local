import threading
from .store import atomic_json, read_json

CATALOG = {
    'medium': {'repo': 'Systran/faster-whisper-medium', 'estimated_bytes': 1600000000, 'label': 'Medium · 建議，辨識較完整'},
    'small': {'repo': 'Systran/faster-whisper-small', 'estimated_bytes': 500000000, 'label': 'Small · 較輕量、較快'},
}
REQUIRED = ('model.bin', 'config.json', 'tokenizer.json')
VOCABULARIES = ('vocabulary.json', 'vocabulary.txt')

def available(store, name):
    if name not in CATALOG:
        raise ValueError('未知模型')
    directory = store.models / name
    manifest = read_json(directory / 'installed.json')
    return bool(manifest and manifest.get('revision') and all((directory / f).is_file() and (directory / f).stat().st_size > 0 for f in REQUIRED) and any((directory / f).is_file() and (directory / f).stat().st_size > 0 for f in VOCABULARIES))

class Models:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.task = {'status': 'idle', 'name': None, 'error': None}
        self.thread = None

    def status(self):
        with self.lock:
            task = dict(self.task)
        return {'models': [dict(name=n, **c, ready=available(self.store, n), path=str(self.store.models/n)) for n,c in CATALOG.items()], 'download': task}

    def download(self, name):
        if name not in CATALOG:
            raise ValueError('未知模型')
        with self.lock:
            if self.task['status'] == 'downloading':
                raise ValueError('已有模型正在下載，請等待完成')
            self.task = {'status': 'downloading', 'name': name, 'error': None}
        self.thread = threading.Thread(target=self._run, args=(name,), daemon=True)
        self.thread.start()

    def _run(self, name):
        try:
            from huggingface_hub import HfApi, snapshot_download
            revision = HfApi().model_info(CATALOG[name]['repo']).sha
            snapshot_download(repo_id=CATALOG[name]['repo'], revision=revision,
                              local_dir=str(self.store.models/name), allow_patterns=list(REQUIRED)+list(VOCABULARIES)+['preprocessor_config.json'])
            atomic_json(self.store.models/name/'installed.json', {'revision': revision, 'repo': CATALOG[name]['repo']})
            if not available(self.store, name):
                raise RuntimeError('模型檔案不完整，請重試下載')
            with self.lock:
                self.task = {'status': 'complete', 'name': name, 'error': None}
        except Exception as e:
            with self.lock:
                self.task = {'status': 'failed', 'name': name, 'error': str(e)}
