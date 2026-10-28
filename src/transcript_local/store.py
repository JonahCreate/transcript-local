from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from datetime import datetime, timezone

SUPPORTED = {'.wav', '.mp3', '.m4a', '.mp4', '.mov', '.aac', '.flac', '.ogg', '.webm', '.mkv', '.aiff', '.opus'}
ACTIVE = {'queued', 'running', 'cancelling'}

def now():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def clean_name(name):
    name = name.replace('\\', '/').split('/')[-1]
    name = ''.join(c for c in name if c.isprintable()).strip()
    if not name or name in {'.', '..'} or len(name.encode()) > 220:
        raise ValueError('檔名無效或過長')
    if Path(name).suffix.lower() not in SUPPORTED:
        raise ValueError('不支援的檔案格式：' + name)
    return name

class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('TRANSCRIPT_LOCAL_DATA', Path.home() / 'Library/Application Support/transcript-local')).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.jobs = self.root / 'jobs'
        self.models = self.root / 'models'
        self.jobs.mkdir(exist_ok=True)
        self.models.mkdir(exist_ok=True)

    def job_dir(self, job_id):
        if not re.fullmatch('[0-9a-f]{32}', job_id):
            raise ValueError('無效的任務編號')
        return self.jobs / job_id

    def get(self, job_id):
        job = read_json(self.job_dir(job_id) / 'job.json')
        if job is None:
            raise FileNotFoundError('找不到任務')
        return job

    def save(self, job):
        atomic_json(self.job_dir(job['id']) / 'job.json', job)

    def list(self):
        jobs = []
        for p in self.jobs.glob('*/job.json'):
            try:
                jobs.append(self.get(p.parent.name))
            except (ValueError, OSError):
                continue
        return sorted(jobs, key=lambda j: j['created_at'], reverse=True)

    def glossary(self):
        return read_json(self.root / 'glossary.json', {'prompts': [], 'rules': []})

    def new_id(self):
        return uuid.uuid4().hex

    def usage(self, path=None):
        total = 0
        for p in (path or self.root).rglob('*'):
            try:
                if p.is_file(): total += p.stat().st_size
            except (FileNotFoundError, PermissionError):
                continue
        return total
