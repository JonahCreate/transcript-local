"""Check the official remote without fetching, installing or stopping anything."""
import os
import re
import subprocess

OFFICIAL = 'https://github.com/JonahCreate/transcript-local.git'


def check_remote(current):
    if not re.fullmatch(r'[0-9a-f]{40}', current or ''):
        return {'check_status':'unknown','can_update':False,'check_message':'目前是本機開發版本，無法確認是否需要更新。','current_revision':current}
    try:
        result=subprocess.run(['git','ls-remote',OFFICIAL,'refs/heads/main'],stdin=subprocess.DEVNULL,
                              capture_output=True,text=True,timeout=15,env=dict(os.environ,GIT_TERMINAL_PROMPT='0'))
        if result.returncode:raise RuntimeError('無法連線 GitHub')
        remote=result.stdout.split()[0]
        if not re.fullmatch(r'[0-9a-f]{40}',remote):raise ValueError('版本回應無效')
        newer=remote!=current
        return {'check_status':'update_available' if newer else 'up_to_date','can_update':newer,
                'check_message':'有新版本可以更新。' if newer else '已是最新版本。',
                'current_revision':current,'remote_revision':remote}
    except (OSError,ValueError,RuntimeError,IndexError,subprocess.TimeoutExpired):
        return {'check_status':'check_failed','can_update':False,'check_message':'目前無法檢查更新，請確認網路後重試。','current_revision':current}
