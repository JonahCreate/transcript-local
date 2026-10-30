from types import SimpleNamespace
import pytest
from transcript_local.update_check import check_remote
from transcript_local.updater import Updater
from transcript_local.manager import Manager


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


def test_current_version_does_not_queue_or_lock(store,monkeypatch):
    monkeypatch.setenv('IHT_MANAGED','1')
    monkeypatch.setattr('transcript_local.updater.check_remote',lambda current:{'check_status':'up_to_date','can_update':False,'check_message':'已是最新版本。'})
    manager=Manager(store);updater=Updater(manager)
    assert updater.start() is False
    assert not manager.updating
    assert not (store.root/'runtime/update-request.json').exists()
    assert not (store.root/'runtime/update.json').exists()
