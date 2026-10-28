import os
from pathlib import Path
import re
import threading
from .store import atomic_json, read_json

CATALOG = {
    'medium': {'repo': 'Systran/faster-whisper-medium', 'estimated_bytes': 1600000000, 'label': 'Medium · 建議，辨識較完整'},
    'small': {'repo': 'Systran/faster-whisper-small', 'estimated_bytes': 500000000, 'label': 'Small · 較輕量、較快'},
}
REQUIRED = ('model.bin', 'config.json', 'tokenizer.json')
VOCABULARIES = ('vocabulary.json', 'vocabulary.txt')

def _complete(directory):
    return (all((directory / f).is_file() and (directory / f).stat().st_size > 0 for f in REQUIRED)
            and any((directory / f).is_file() and (directory / f).stat().st_size > 0 for f in VOCABULARIES))


def cache_root():
    if os.environ.get('HF_HUB_CACHE'):
        return Path(os.environ['HF_HUB_CACHE']).expanduser()
    return Path(os.environ.get('HF_HOME', Path.home() / '.cache' / 'huggingface')).expanduser() / 'hub'


def resolve_model(store, name, revision=None):
    """Resolve a complete local installation or Hugging Face cache snapshot, offline.

    A job always passes its saved revision, so changing the preferred model location
    cannot silently change an interrupted job's model.
    """
    if name not in CATALOG:
        raise ValueError('未知模型')
    directory = store.models / name
    manifest = read_json(directory / 'installed.json')
    if (manifest and manifest.get('revision') and (revision is None or manifest['revision'] == revision)
            and _complete(directory)):
        return {'path': directory, 'revision': manifest['revision'], 'source': 'app'}

    repo_dir = cache_root() / ('models--' + CATALOG[name]['repo'].replace('/', '--'))
    snapshots = repo_dir / 'snapshots'
    if revision is not None:
        revisions = [revision]
    else:
        main = repo_dir / 'refs' / 'main'
        try:
            preferred = main.read_text(encoding='utf-8').strip()
        except (FileNotFoundError, OSError):
            preferred = ''
        revisions = [preferred] if preferred else []
        if snapshots.is_dir():
            revisions += sorted((p.name for p in snapshots.iterdir() if p.is_dir()), reverse=True)
    for candidate in dict.fromkeys(revisions):
        if not re.fullmatch(r'[0-9a-f]{40,64}', candidate):
            continue
        path = snapshots / candidate
        if _complete(path):
            return {'path': path, 'revision': candidate, 'source': 'cache'}
    return None


def available(store, name):
    return resolve_model(store, name) is not None

class Models:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.task = {'status': 'idle', 'name': None, 'error': None}
        self.thread = None

    def status(self):
        with self.lock:
            task = dict(self.task)
        models = []
        for name, config in CATALOG.items():
            resolved = resolve_model(self.store, name)
            models.append(dict(name=name, **config, ready=resolved is not None,
                               source=resolved['source'] if resolved else None,
                               path=str(resolved['path'] if resolved else self.store.models / name)))
        return {'models': models, 'download': task}

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
            if not _complete(self.store.models/name):
                raise RuntimeError('模型檔案不完整，請重試下載')
            with self.lock:
                self.task = {'status': 'complete', 'name': name, 'error': None}
        except Exception as e:
            with self.lock:
                self.task = {'status': 'failed', 'name': name, 'error': str(e)}
