import pytest
from transcript_local.menu import terminal_update


@pytest.mark.parametrize('status',['up_to_date','check_failed','unknown'])
def test_terminal_no_update_never_confirms_or_starts(store,monkeypatch,status):
    actions=[]
    monkeypatch.setattr('transcript_local.menu.check_remote',lambda current:{'check_status':status,'can_update':False,'check_message':'result'})
    monkeypatch.setattr('transcript_local.menu.ensure',lambda *args:actions.append('start'))
    checked,message=terminal_update(store,None,0,lambda text:actions.append('confirm'),lambda text:actions.append('checking'))
    assert actions==['checking']
    assert message=='result'


def test_terminal_confirm_only_when_available_and_idle(store,monkeypatch):
    calls=[];info={'port':1}
    def request(instance,path,body):
        calls.append(path)
        return {'check_status':'update_available','can_update':True} if path.endswith('/check') else {'status':'requested','message':'queued'}
    monkeypatch.setattr('transcript_local.menu.request',request)
    checked,message=terminal_update(store,info,1,lambda text:pytest.fail('active task must not confirm'),lambda text:None)
    assert calls==['/api/update/check']
    calls.clear()
    checked,message=terminal_update(store,info,0,lambda text:False,lambda text:None)
    assert calls==['/api/update/check']
    calls.clear()
    checked,message=terminal_update(store,info,0,lambda text:True,lambda text:None)
    assert calls==['/api/update/check','/api/update']
    assert message=='queued'

def test_terminal_repair_requests_confirmation(store,monkeypatch):
    calls=[]
    def request(instance,path,body):
        calls.append(path)
        return {'check_status':'repair_available','can_update':True} if path.endswith('/check') else {'status':'requested','message':'queued'}
    monkeypatch.setattr('transcript_local.menu.request',request)
    prompts=[]
    terminal_update(store,{'port':1},0,lambda text:prompts.append(text) or True,lambda text:None)
    assert '修復' in prompts[0]
    assert calls==['/api/update/check','/api/update']
