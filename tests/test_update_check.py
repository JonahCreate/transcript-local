from types import SimpleNamespace
import pytest
import subprocess
from transcript_local.update_check import check_remote
from transcript_local.updater import Updater
from transcript_local.manager import Manager

def test_install_revision_ignores_unrelated_files_but_detects_code_changes(tmp_path):
    from transcript_local.install_commands import install_revision
    def git(*args):
        return subprocess.run(['git',*args],cwd=tmp_path,check=True,capture_output=True,text=True).stdout.strip()
    git('init');git('config','user.email','test@example.invalid');git('config','user.name','Test')
    source=tmp_path/'src'/'transcript_local';source.mkdir(parents=True)
    (source/'app.py').write_text('original')
    (tmp_path/'pyproject.toml').write_text('original')
    git('add','.');git('commit','-m','initial')
    revision=git('rev-parse','HEAD')
    (tmp_path/'personal-notes.txt').write_text('unrelated')
    assert install_revision(tmp_path)==revision
    (source/'app.py').write_text('modified')
    assert install_revision(tmp_path)==revision+'-local'
    (source/'app.py').write_text('original')
    (source/'extra.py').write_text('untracked code')
    assert install_revision(tmp_path)==revision+'-local'

def test_install_with_live_service_explains_how_to_finish(store,tmp_path,monkeypatch,capsys):
    from transcript_local import install_commands, service
    from transcript_local.store import atomic_json, read_json
    rt=service.runtime(store);current={'revision':'installed','project':'old','python':'old'}
    atomic_json(rt/'current.json',current)
    monkeypatch.setenv('TRANSCRIPT_LOCAL_DATA',str(store.root))
    monkeypatch.setattr(install_commands,'install',lambda project:[])
    monkeypatch.setattr(service,'busy_lease',lambda store:True)
    monkeypatch.setattr('sys.argv',['install_commands','--project',str(tmp_path)])
    install_commands.main()
    assert read_json(rt/'current.json')==current
    assert '停止服務' in capsys.readouterr().out

def test_stop_waits_for_supervisor_to_exit_before_reporting_success(store,monkeypatch):
    from transcript_local import service
    from transcript_local.store import atomic_json
    rt=service.runtime(store);atomic_json(rt/'supervisor.json',{'secret':'test'})
    states=iter([True,True,False]);calls=[]
    def registered(_):
        calls.append(True);return next(states)
    monkeypatch.setattr(service,'_supervisor_registered',registered)
    monkeypatch.setattr(service,'busy_lease',lambda _:False)
    monkeypatch.setattr(service.time,'sleep',lambda _:None)
    assert '已停止' in service.stop(store)
    assert len(calls)==3


@pytest.mark.parametrize('remote,expected',[('a'*40,False),('b'*40,True)])
def test_remote_comparison_only_checks_refs(monkeypatch,remote,expected):
    calls=[]
    def run(args,**kwargs):
        calls.append(args);return SimpleNamespace(returncode=0,stdout=remote+'\trefs/heads/main\n')
    monkeypatch.setattr('transcript_local.update_check.subprocess.run',run)
    result=check_remote('a'*40)
    assert result['can_update']==expected
    assert calls[0][1]=='ls-remote'
    assert result['check_status']==('update_available' if expected else 'up_to_date')


def test_unreachable_is_not_reported_latest(monkeypatch):
    monkeypatch.setattr('transcript_local.update_check.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    assert check_remote('a'*40)['check_status']=='check_failed'
    assert not check_remote('local-build')['can_update']

def test_unknown_installed_revision_offers_official_repair_without_assuming_update(monkeypatch):
    monkeypatch.setattr('transcript_local.update_check.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='b'*40+'\trefs/heads/main\n'))
    result=check_remote('installed')
    assert result['check_status']=='repair_available' and result['can_update']
    assert '修復' in result['check_message']
    local=check_remote('a'*40+'-local')
    assert local['can_update'] and '原專案修改會保留' in local['check_message']

def test_unknown_installed_revision_requires_network(monkeypatch):
    monkeypatch.setattr('transcript_local.update_check.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    assert check_remote('installed')['check_status']=='check_failed'


def test_current_version_does_not_queue_or_lock(store,monkeypatch):
    monkeypatch.setenv('IHT_MANAGED','1')
    monkeypatch.setattr('transcript_local.updater.check_remote',lambda current:{'check_status':'up_to_date','can_update':False,'check_message':'已是最新版本。'})
    manager=Manager(store);updater=Updater(manager)
    assert updater.start() is False
    assert not manager.updating
    assert not (store.root/'runtime/update-request.json').exists()
    assert not (store.root/'runtime/update.json').exists()
