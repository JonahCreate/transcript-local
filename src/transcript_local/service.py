from http.client import HTTPException as HTTPReadError
"""On-demand macOS service controller. No PID from disk is ever signalled."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from .store import atomic_json, read_json


def runtime(store):
    path = store.root / 'runtime'
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def label(store):
    return 'com.iht.service.' + hashlib.sha256(str(store.root).encode()).hexdigest()[:12]


def request(info, path='/api/health', body=None):
    # Refuse a modified descriptor pointing to any non-loopback host.
    port = info.get('port')
    if not isinstance(port, int) or not 0 < port < 65536:
        raise RuntimeError('無效的服務連接埠')
    headers = {'Authorization': 'Bearer ' + info['token'], 'X-Local-Request': '1'}
    data = None if body is None else json.dumps(body).encode()
    if data is not None: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20 if path in {'/api/update/check','/api/update'} else 3) as response:
        return json.load(response) if response.status != 204 else None


def healthy(store):
    try:info = read_json(runtime(store) / 'service.json', {})
    except (OSError,ValueError,HTTPReadError):return None
    if not isinstance(info,dict):return None
    try:
        result = request(info)
        if result.get('ready') and result['instance_id'] == info['instance_id'] and result['data_path'] == str(store.root):
            return info
    except (OSError, ValueError, KeyError, RuntimeError, HTTPReadError):
        pass
    return None


def busy_lease(store):
    path=store.root / 'instance.lock'
    with path.open('r' if path.exists() else 'a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def project_root():
    value = os.environ.get('TRANSCRIPT_LOCAL_PROJECT')
    path = Path(value).resolve() if value else Path(sys.executable).parent.parent.parent.resolve()
    if not (path / 'start.sh').is_file():
        raise RuntimeError('找不到專案資料夾，請在新位置重新執行 setup.sh。')
    return path


def ensure(store, open_browser=True):
    rt = runtime(store)
    with (rt / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = healthy(store)
        if not info:
            if busy_lease(store):
                raise RuntimeError('有舊服務仍在執行但未提供健康檢查。請先儲存修改，關閉原服務，再重試。')
            if sys.platform != 'darwin':
                raise RuntimeError('背景服務目前支援 macOS；其他平台可使用 serve --no-browser。')
            project = project_root()
            supervisor = rt / 'supervisor.py'
            # Stable stdlib-only supervisor lives outside replaceable releases.
            shutil.copyfile(Path(__file__).with_name('supervisor.py'), supervisor)
            if not (rt / 'current.json').exists():
                atomic_json(rt / 'current.json', {'project': str(project), 'python': str(project / '.venv/bin/python'), 'revision': 'installed'})
            config = {'source': str(project), 'data': str(store.root), 'label': label(store)}
            atomic_json(rt / 'config.json', config)
            arguments = [str(Path(sys._base_executable).resolve()), str(supervisor), str(store.root)]
            plist = {'Label': label(store), 'ProgramArguments': arguments,
                     'RunAtLoad': True, 'KeepAlive': False, 'WorkingDirectory': str(rt),
                     'StandardOutPath': str(rt / 'launch.log'), 'StandardErrorPath': str(rt / 'launch.log'),
                     'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/opt/homebrew/bin:/usr/bin:/bin')}}
            plist_path = rt / 'service.plist'
            plist_path.write_bytes(plistlib.dumps(plist)); plist_path.chmod(0o600)
            domain = f'gui/{os.getuid()}'
            # bootout only the deterministic service label; never an arbitrary PID.
            subprocess.run(['launchctl', 'bootout', f'{domain}/{label(store)}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = subprocess.run(['launchctl', 'bootstrap', domain, str(plist_path)], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError('背景服務無法啟動：' + result.stderr.strip())
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                info = healthy(store)
                if info: break
                time.sleep(.25)
            if not info:
                raise RuntimeError('服務尚未準備完成。請查看選單狀態或 runtime/service.log 後重試。')
    if open_browser:
        webbrowser.open(f'http://127.0.0.1:{info["port"]}/#session={info["token"]}')
    return info


def stop(store, force=False):
    rt = runtime(store)
    control = read_json(rt / 'supervisor.json', {})
    if not control or not _supervisor_registered(store):
        if not busy_lease(store): return '逐字稿工具已停止。'
        raise RuntimeError('無法確認舊服務身分，未終止程序。請在原終端機停止服務。')
    # Only the live supervisor holding its lease can consume this authenticated request.
    atomic_json(rt / 'control.json', {'secret': control['secret'], 'action': 'force' if force else 'stop'})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not busy_lease(store) and not _supervisor_registered(store): return '逐字稿工具已停止，完成片段已保留。'
        time.sleep(.2)
    raise RuntimeError('服務尚未停止。可輸入 iht stop --force 或 transcript-local stop --force。')


def _supervisor_registered(store):
    # A stale descriptor alone is not proof of a running managed instance.
    path = runtime(store) / 'supervisor.lock'
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
