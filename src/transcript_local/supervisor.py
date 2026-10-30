from http.client import HTTPException as HTTPReadError
"""Stable stdlib-only service supervisor, executed outside the application venv."""
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request


def read(path, default=None):
    try: return json.loads(path.read_text())
    except (OSError, ValueError, HTTPReadError): return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def rotate(path):
    if path.exists() and path.stat().st_size > 5 * 1024**2:
        for index in (2, 1):
            old = path.with_suffix(f'.log.{index}')
            if old.exists(): old.replace(path.with_suffix(f'.log.{index + 1}'))
        path.replace(path.with_suffix('.log.1'))


def api(info, path='/api/health', body=None):
    if not isinstance(info.get('port'), int): raise ValueError('missing port')
    headers = {'Authorization': 'Bearer ' + info['token'], 'X-Local-Request': '1', 'Content-Type': 'application/json'}
    request = urllib.request.Request(f'http://127.0.0.1:{info["port"]}{path}', headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response) if response.status != 204 else None


def descendants(pid):
    """Find current descendants of our live Popen child, not saved PIDs."""
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid='], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    pairs = [tuple(map(int, row.split())) for row in result.stdout.splitlines() if len(row.split()) == 2]
    found = {pid}
    while True:
        more = {p for p, parent in pairs if parent in found}
        if more <= found: break
        found |= more
    return found - {pid}


class Supervisor:
    def __init__(self, root):
        self.root = Path(root).resolve(); self.rt = self.root / 'runtime'
        self.config = read(self.rt / 'config.json')
        self.child = None
        self.stopping = False
        self.switching = False
        self.update_thread = None
        self.build_process = None
        self.secret = secrets.token_urlsafe(32)
        self.web_token = secrets.token_urlsafe(32)
        self.web_port = 0
        self.mutex = threading.RLock()

    def health(self):
        info = read(self.rt / 'service.json', {})
        try:
            if self.child and self.child.poll() is None and info.get('pid') == self.child.pid:
                result = api(info)
                if result.get('ready') and result['instance_id'] == info['instance_id'] and result['data_path'] == str(self.root): return info
        except (OSError, ValueError, KeyError, HTTPReadError): pass
        return None

    def launch(self, current, recover=False, timeout=35):
        (self.rt / 'service.json').unlink(missing_ok=True)
        env = dict(os.environ, TRANSCRIPT_LOCAL_PROJECT=self.config['source'], TRANSCRIPT_LOCAL_DATA=str(self.root),
                   IHT_MANAGED='1', IHT_SERVICE_TOKEN=self.web_token, IHT_SERVICE_PORT=str(self.web_port), IHT_RECOVER_CRASH='1' if recover else '0', IHT_REVISION=current['revision'])
        env.pop('PYTHONPATH', None)
        log_path = self.rt / 'service.log'; rotate(log_path)
        with log_path.open('ab') as log:
            self.child = subprocess.Popen([current['python'], '-m', 'transcript_local.cli', 'serve', '--no-browser'],
                                          cwd=current['project'], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.child.poll() is None:
            info=self.health()
            if info:
                self.web_port=info['port']
                return True
            time.sleep(.2)
        return False

    def terminate(self, force=False):
        if not self.child or self.child.poll() is not None: return
        if force:
            children = descendants(self.child.pid)
            # Reverse order means workers are stopped before their parent.
            for pid in sorted(children, reverse=True):
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass
            self.child.kill()
        else:
            self.child.terminate()
        try: self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if force: raise
            # Normal stop never silently escalates to forced termination.
            return False
        return True

    def state(self, status, message, **extra):
        write(self.rt / 'update.json', {'status': status, 'message': message, **extra})

    def command(self, args, cwd, env=None):
        log_path = self.rt / 'update.log'; rotate(log_path)
        with log_path.open('ab') as log:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            self.build_process=process
            try:
                try: code = process.wait(timeout=900)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(); raise RuntimeError('更新逾時，請檢查網路後重試。')
            finally:self.build_process=None
        if code:
            with log_path.open('rb') as stream:
                stream.seek(max(0, stream.seek(0, 2) - 2500))
                detail = stream.read().decode(errors='replace')
            raise RuntimeError(detail)

    def prepare(self):
        source = Path(self.config['source'])
        remote = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], cwd=source, text=True).strip()
        if remote not in {'https://github.com/JonahCreate/transcript-local.git', 'https://github.com/JonahCreate/transcript-local', 'git@github.com:JonahCreate/transcript-local.git'}:
            raise RuntimeError('更新來源不是官方專案，請先確認 Git remote。')
        self.command(['git', 'fetch', 'origin', 'main'], source)
        revision = subprocess.check_output(['git', 'rev-parse', 'FETCH_HEAD'], cwd=source, text=True).strip()
        current=read(self.rt/'current.json',{})
        if current.get('revision')==revision:return current
        release = self.rt / 'releases' / revision
        if not (release / '.ready').exists():
            if release.exists(): shutil.rmtree(release)
            release.mkdir(parents=True)
            with tempfile.TemporaryDirectory(dir=self.rt) as temporary:
                archive = Path(temporary) / 'source.tar'
                self.command(['git', 'archive', '--format=tar', '--output', str(archive), revision], source)
                with tarfile.open(archive) as bundle: bundle.extractall(release, filter='data')
            env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(release / '.venv'))
            env.pop('PYTHONPATH', None)
            self.command(['uv', 'sync', '--frozen', '--no-editable', '--python', '3.12'], release, env)
            (release / '.ready').touch()
        return {'project': str(release), 'python': str(release / '.venv/bin/python'), 'revision': revision}

    def validate(self, candidate):
        with tempfile.TemporaryDirectory(dir=self.rt, prefix='validation-') as temporary:
            root = Path(temporary)
            env = dict(os.environ, TRANSCRIPT_LOCAL_DATA=str(root), IHT_MANAGED='1', IHT_SERVICE_TOKEN=secrets.token_urlsafe(32), IHT_SERVICE_PORT='0', IHT_RECOVER_CRASH='0', IHT_REVISION=candidate['revision'])
            env.pop('PYTHONPATH', None)
            with (self.rt / 'validation.log').open('ab') as log:
                proc = subprocess.Popen([candidate['python'], '-m', 'transcript_local.cli', 'serve', '--no-browser'], cwd=candidate['project'],
                                        env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 35
                    while time.monotonic() < deadline and proc.poll() is None:
                        info = read(root / 'runtime/service.json', {})
                        try:
                            if info.get('pid') == proc.pid and api(info)['data_path'] == str(root): return
                        except (OSError, ValueError, KeyError, HTTPReadError): pass
                        time.sleep(.2)
                    raise RuntimeError('新版啟動驗證失敗，保留目前版本。')
                finally:
                    if proc.poll() is None: proc.terminate()
                    try: proc.wait(timeout=10)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait()

    def backup(self):
        dest = self.rt / 'backups' / str(time.time_ns()); dest.mkdir(parents=True)
        files = [self.root / 'glossary.json', self.root / 'preferences.json']
        files += list((self.root / 'jobs').glob('*/job.json')) + list((self.root / 'jobs').glob('*/transcript.json'))
        files += list((self.root / 'jobs').glob('*/drafts/*.json'))
        for path in files:
            if path.is_file():
                target = dest / path.relative_to(self.root); target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
        return str(dest)

    def open_page(self):
        info = self.health()
        if info and sys.platform == 'darwin':
            subprocess.Popen(['/usr/bin/open', f'http://127.0.0.1:{info["port"]}/#session={info["token"]}'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def update(self):
        previous = read(self.rt / 'current.json')
        switched = False
        try:
            self.state('preparing', '正在下載與安裝新版，原服務仍保留。')
            candidate = self.prepare()
            if candidate['revision']==previous['revision']:
                self.state('complete','已是最新版本。',revision=candidate['revision'])
                self.unlock()
                return
            if self.stopping: raise RuntimeError('更新已停止。')
            self.state('validating', '正在驗證新版是否可正常啟動。')
            self.validate(candidate)
            if self.stopping: raise RuntimeError('更新已停止。')
            backup = self.backup()
            with self.mutex:
                self.switching = True
                self.state('switching', '新版驗證完成，正在重新開啟。', backup=backup)
                if self.terminate() is False: raise RuntimeError('舊服務未能正常停止，尚未切換版本。')
                switched = True
                if not self.launch(candidate): raise RuntimeError('新版未能啟動，正在恢復前一版本。')
                write(self.rt / 'current.json', candidate)
                self.state('complete', '更新完成，新版本已就緒。', revision=candidate['revision'], backup=backup)
                self.open_page()
        except Exception as exc:
            with self.mutex:
                if switched and not self.stopping:
                    self.terminate(force=True)
                    if self.launch(previous):
                        self.state('rolled_back', '已恢復前一版本。更新原因：' + str(exc)[-2000:])
                        self.open_page()
                    else:
                        self.state('failed', '新版與前一版本均未啟動。請查看日誌：' + str(exc)[-2000:])
                else:
                    self.state('failed', str(exc)[-2500:])
                self.unlock()
        finally:
            self.switching = False

    def unlock(self):
        info=self.health()
        if info:
            try:api(info,'/api/update/unlock',{'secret':self.secret})
            except (OSError,ValueError,HTTPReadError):pass

    def run(self):
        with (self.rt / 'supervisor.lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
            write(self.rt / 'supervisor.json', {'secret': self.secret, 'pid': os.getpid()})
            for name in ('control.json', 'update-request.json'): (self.rt / name).unlink(missing_ok=True)
            current = read(self.rt / 'current.json')
            if not self.launch(current):
                self.terminate(force=True)
                last = read(self.rt / 'recovery.json', {}).get('last_restart', 0)
                if time.time() - last < 600:
                    self.state('failed', '服務再次異常停止，已停止自動恢復。'); return
                write(self.rt / 'recovery.json', {'last_restart': time.time()})
                if not self.launch(current, recover=True):
                    self.state('failed', '服務再次異常停止，請查看 service.log。'); self.terminate(force=True); return
            while not self.stopping:
                control = read(self.rt / 'control.json', {})
                if secrets.compare_digest(str(control.get('secret', '')), self.secret):
                    action = control.get('action')
                    if action in {'stop', 'force'}:
                        if self.update_thread and self.update_thread.is_alive():
                            if action!='force':
                                # A normal stop waits for the release switch.
                                time.sleep(.2); continue
                            self.stopping=True
                            build=self.build_process
                            if build and build.poll() is None:
                                try:os.killpg(build.pid,signal.SIGKILL)
                                except ProcessLookupError:pass
                        with self.mutex:
                            if self.terminate(force=action == 'force') is not False: self.stopping = True
                        (self.rt / 'control.json').unlink(missing_ok=True)
                        continue
                request = read(self.rt / 'update-request.json', {})
                if request.get('secret') == self.secret and not (self.update_thread and self.update_thread.is_alive()):
                    (self.rt / 'update-request.json').unlink(missing_ok=True)
                    self.update_thread = threading.Thread(target=self.update, daemon=True); self.update_thread.start()
                with self.mutex:
                    if not self.switching and not (self.update_thread and self.update_thread.is_alive()) and self.child.poll() is not None:
                        normal = read(self.rt / 'exit-intent.json', {})
                        info = read(self.rt / 'service.json', {})
                        if normal.get('instance_id') == info.get('instance_id') and normal:
                            self.stopping = True; continue
                        last = read(self.rt / 'recovery.json', {}).get('last_restart', 0)
                        if time.time() - last < 600:
                            self.state('failed', '服務再次異常停止，已停止自動恢復。請重新開啟選單查看日誌。')
                            self.stopping = True; continue
                        write(self.rt / 'recovery.json', {'last_restart': time.time()})
                        if not self.launch(read(self.rt / 'current.json'), recover=True):
                            self.state('failed', '服務再次異常停止，已停止自動恢復。請查看 service.log。')
                            self.terminate(force=True); self.stopping = True
                time.sleep(.2)
            if self.child and self.child.poll() is None: self.terminate()


def main():
    os.umask(0o077)
    supervisor = Supervisor(sys.argv[1])
    def shutdown(*_):
        supervisor.stopping = True
        if supervisor.child: supervisor.terminate()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    supervisor.run()

if __name__ == '__main__': main()
