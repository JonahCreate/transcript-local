"""Install reversible launchers without replacing unrelated shell customizations."""
import argparse
import os
import shutil
from pathlib import Path
import shlex
import subprocess

MARKER = '# IHT managed launcher'

def install_revision(project):
    """Identify the installed code without treating unrelated personal files as changes."""
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=project,capture_output=True,text=True)
    if head.returncode:return 'installed'
    status=subprocess.run(['git','status','--porcelain','--untracked-files=all','--',
                           'src/transcript_local','pyproject.toml','uv.lock'],cwd=project,capture_output=True,text=True)
    if status.returncode:return 'installed'
    return head.stdout.strip()+('-local' if status.stdout.strip() else '')


def install(project, home=None):
    home = Path(home or Path.home()); project = Path(project).resolve()
    bin_dir = home / '.local/bin'; bin_dir.mkdir(parents=True, exist_ok=True)
    scripts = {
        'iht': '#!/bin/sh\n' + MARKER + '\nexec /bin/bash ' + shlex.quote(str(project / 'start.sh')) + ' "$@"\n',
        'transcript-local': '#!/bin/sh\n' + MARKER + '\nexec /bin/bash ' + shlex.quote(str(project / 'start.sh')) + ' "$@"\n',
        'tr': '#!/bin/sh\n' + MARKER + '\nif [ "$#" -eq 0 ]; then exec ' + '/bin/bash ' + shlex.quote(str(project / 'start.sh')) + ' menu; fi\nexec /usr/bin/tr "$@"\n',
        'end': '#!/bin/sh\n' + MARKER + '\nif [ "${1-}" != tr ]; then echo "用法：end tr [--force]" >&2; exit 2; fi\nshift\nexec ' + '/bin/bash ' + shlex.quote(str(project / 'start.sh')) + ' stop "$@"\n',
    }
    conflicts = []
    for name, content in scripts.items():
        if name=='end' and Path(os.environ.get('SHELL','/bin/zsh')).name=='zsh':
            conflicts.append(name);continue
        path = bin_dir / name
        if path.is_symlink() or (path.exists() and (not path.is_file() or MARKER not in path.read_text(errors='replace'))):
            conflicts.append(name); continue
        path.write_text(content); path.chmod(0o755)
    config = home / '.zshrc'
    marker = '# IHT: local command directory'
    existing = config.read_text() if config.exists() else ''
    if marker not in existing:
        backup=home/'.zshrc.iht-backup'
        if config.exists() and not backup.exists():shutil.copy2(config,backup);backup.chmod(0o600)
        with config.open('a') as stream: stream.write('\n' + marker + '\nexport PATH="$HOME/.local/bin:$PATH"\n')
    return conflicts


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--project', required=True); args = parser.parse_args()
    conflicts = install(args.project)
    from .store import Store, atomic_json, read_json
    from .service import runtime, busy_lease, _supervisor_registered
    store=Store();rt=runtime(store)
    if not busy_lease(store) and not _supervisor_registered(store):
        revision=install_revision(args.project)
        previous=read_json(rt/'current.json')
        if previous:atomic_json(rt/'previous-install.json',previous)
        atomic_json(rt/'current.json',{'project':str(Path(args.project).resolve()),'python':str(Path(args.project).resolve()/'.venv/bin/python'),'revision':revision})
    else:print('現有服務仍在執行，尚未切換安裝版本。請先儲存校稿並停止服務，再重新執行 ./setup.sh。')
    print('IHT 指令已設定；重新開啟終端機後輸入 tr。')
    if conflicts: print('名稱已被使用或由 shell 保留，未覆蓋：' + ', '.join(conflicts) + '。可使用專案 start.sh menu / stop [--force]。')

if __name__ == '__main__': main()
