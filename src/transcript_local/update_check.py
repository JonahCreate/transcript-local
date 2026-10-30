"""Check the official remote without fetching, installing or stopping anything."""
import os
import re
import subprocess

OFFICIAL = 'https://github.com/JonahCreate/transcript-local.git'


def check_remote(current):
    local_changes = bool(re.fullmatch(r'[0-9a-f]{40}-local', current or ''))
    repair = current == 'installed' or local_changes
    if not repair and not re.fullmatch(r'[0-9a-f]{40}', current or ''):
        return {'check_status':'unknown','can_update':False,'check_message':'無法確認目前安裝版本。請先儲存校稿並停止服務，再到專案資料夾執行 ./setup.sh。','current_revision':current}
    try:
        result=subprocess.run(['git','ls-remote',OFFICIAL,'refs/heads/main'],stdin=subprocess.DEVNULL,
                              capture_output=True,text=True,timeout=15,env=dict(os.environ,GIT_TERMINAL_PROMPT='0'))
        if result.returncode:raise RuntimeError('無法連線 GitHub')
        remote=result.stdout.split()[0]
        if not re.fullmatch(r'[0-9a-f]{40}',remote):raise ValueError('版本回應無效')
        newer=remote!=current
        return {'check_status':'repair_available' if repair else 'update_available' if newer else 'up_to_date','can_update':newer,
                'check_message':('目前可能包含本機程式修改；可安裝官方版本，原專案修改會保留。' if local_changes else '無法確認目前版本，可安裝官方最新版本修復。') if repair else '有新版本可以更新。' if newer else '已是最新版本。',
                'current_revision':current,'remote_revision':remote}
    except (OSError,ValueError,RuntimeError,IndexError,subprocess.TimeoutExpired):
        return {'check_status':'check_failed','can_update':False,'check_message':'目前無法檢查更新，請確認網路後重試。','current_revision':current}
