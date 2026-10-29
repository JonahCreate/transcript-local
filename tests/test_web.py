from fastapi.testclient import TestClient
import pytest
from transcript_local.web import create_app
from transcript_local.manager import Manager
from transcript_local.store import atomic_json, read_json

class IdleManager(Manager):
    def start(self):pass
    def close(self):pass

@pytest.fixture
def client(store):
    app=create_app(store,token='test-token',manager=IdleManager(store))
    with TestClient(app) as c:
        c.headers.update({'Authorization':'Bearer test-token','X-Local-Request':'1'})
        yield c

def test_static_and_security_headers(client):
    r=client.get('/');assert r.status_code==200
    assert "default-src 'self'" in r.headers['content-security-policy']
    assert client.get('/static/app.js').status_code==200

def test_auth_host_origin_and_csrf(client):
    assert client.get('/api/jobs',headers={'Authorization':''}).status_code==401
    assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==400
    assert client.put('/api/glossary',json={'prompts':[],'rules':[]},headers={'Origin':'http://evil.example'}).status_code==403
    assert client.post('/api/session',headers={'X-Local-Request':''}).status_code==403
    r=client.post('/api/session');assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']

def test_upload_model_required_and_bad_filename(client,store,wav):
    for p in (store.models/'small').glob('*'):p.unlink()
    r=client.post('/api/jobs',files={'file':('a.wav',wav.read_bytes(),'audio/wav')},data={'model':'small'})
    assert r.status_code==409
    assert client.post('/api/jobs',files={'file':('a.exe',b'bad')}).status_code==409
    assert not list(store.root.glob('upload-*'))

def test_multiple_uploads_are_independent_and_dedup(client,wav):
    a=client.post('/api/jobs',files={'file':('中文 錄音.wav',wav.read_bytes())}).json()
    b=client.post('/api/jobs',files={'file':('another.wav',wav.read_bytes())}).json()
    assert a['id']==b['id']
    c=client.post('/api/jobs',files={'file':('another.wav',wav.read_bytes())},data={'force':'true'}).json()
    assert c['id']!=a['id'];assert len(client.get('/api/jobs').json())==2

def test_edit_preview_apply_export_and_conflict(client,store,wav,document):
    j=client.post('/api/jobs',files={'file':('a.wav',wav.read_bytes())}).json()
    path=store.job_dir(j['id'])/'transcript.json';atomic_json(path,document)
    url=f'/api/jobs/{j["id"]}'
    assert client.put('/api/glossary',json={'prompts':['共槽'],'rules':[{'from':'供鈔','to':'共槽'}]}).status_code==200
    p=client.post(url+'/preview').json();assert read_json(path)['revision']==1
    r=client.post(url+'/apply',json={'revision':p['revision'],'glossary_hash':p['glossary_hash'],'ids':[0]})
    assert r.status_code==200;assert r.json()['segments'][0]['edited_text']=='共槽'
    assert client.post(url+'/apply',json={'revision':p['revision'],'glossary_hash':p['glossary_hash'],'ids':[0]}).status_code==409
    for fmt in ['md','txt','srt','json']:
        result=client.get(url+'/export/'+fmt);assert result.status_code==200;assert '共槽' in result.text
    assert read_json(path)['segments'][0]['raw_text']=='供钞'
    assert client.put(url+'/transcript',json={'revision':1,'segments':[]}).status_code==409

def test_repeated_edits_export_current_revision_with_distinct_filename(client,store,wav,document):
    job=client.post('/api/jobs',files={'file':('我的 錄音.wav',wav.read_bytes())}).json()
    atomic_json(store.job_dir(job['id'])/'transcript.json',document)
    url=f'/api/jobs/{job["id"]}'
    first=client.put(url+'/transcript',json={'revision':1,'segments':[{'id':0,'text':'第一次修改','reviewed':True}]})
    assert first.status_code==200
    old_export=client.get(url+'/export/md')
    assert '第一次修改' in old_export.text
    second=client.put(url+'/transcript',json={'revision':2,'segments':[{'id':0,'text':'第二次修改','reviewed':True}]})
    assert second.status_code==200
    new_export=client.get(url+'/export/md')
    assert '第二次修改' in new_export.text
    assert '第一次修改' not in new_export.text
    assert old_export.headers['content-disposition'] != new_export.headers['content-disposition']
    assert '%E6%88%91%E7%9A%84' in new_export.headers['content-disposition']
    assert '%E7%AC%AC3%E7%89%88' in new_export.headers['content-disposition']

def test_no_path_read_endpoint(client):
    assert client.get('/api/files?path=/etc/passwd').status_code==404
    assert client.get('/api/jobs/not-an-id/audio').status_code==400
    assert client.get('/static/../store.py').status_code==404

def test_replacement_requires_nonempty_source(client):
    assert client.put('/api/glossary',json={'prompts':[],'rules':[{'from':'','to':'a'}]}).status_code==422

def test_pending_job_cannot_be_removed(client,wav):
    j=client.post('/api/jobs',files={'file':('a.wav',wav.read_bytes())}).json()
    url=f'/api/jobs/{j["id"]}'
    assert client.delete(url).status_code==409
    assert client.post(url+'/cancel').status_code==200
    assert client.delete(url).status_code==200

def test_audio_range_playback(client,store,wav):
    j=client.post('/api/jobs',files={'file':('a.wav',wav.read_bytes())}).json()
    (store.job_dir(j['id'])/'audio.wav').write_bytes(wav.read_bytes())
    r=client.get(f'/api/jobs/{j["id"]}/audio',headers={'Range':'bytes=0-43'})
    assert r.status_code==206;assert len(r.content)==44

def test_web_update_guards_tasks_and_restarts(client,store,wav,monkeypatch,tmp_path):
    import threading
    from types import SimpleNamespace
    from transcript_local import updater as updater_module
    project=tmp_path/'project';project.mkdir();(project/'update.sh').write_text('')
    monkeypatch.setenv('TRANSCRIPT_LOCAL_PROJECT',str(project))
    done=threading.Event()
    monkeypatch.setattr(updater_module.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=''))
    app=create_app(store,token='test-token',manager=IdleManager(store),restart=done.set)
    with TestClient(app) as c:
        c.headers.update({'Authorization':'Bearer test-token','X-Local-Request':'1'})
        job=c.post('/api/jobs',files={'file':('a.wav',wav.read_bytes())}).json()
        assert c.post('/api/update').status_code==409
        assert c.post(f'/api/jobs/{job["id"]}/cancel').status_code==200
        assert c.post('/api/update').status_code==202
        assert done.wait(2)
        assert c.get('/api/update').json()['status']=='complete'
        assert c.put('/api/glossary',json={'prompts':[],'rules':[]}).status_code==409

def test_web_update_reports_failure_and_unlocks(store,monkeypatch,tmp_path):
    import threading
    from types import SimpleNamespace
    from transcript_local import updater as updater_module
    project=tmp_path/'project';project.mkdir();(project/'update.sh').write_text('')
    monkeypatch.setenv('TRANSCRIPT_LOCAL_PROJECT',str(project))
    finished=threading.Event()
    def failed(*args,**kwargs):
        finished.set()
        return SimpleNamespace(returncode=1,stdout='更新失敗：請檢查網路')
    monkeypatch.setattr(updater_module.subprocess,'run',failed)
    app=create_app(store,token='test-token',manager=IdleManager(store),restart=lambda:None)
    with TestClient(app) as c:
        c.headers.update({'Authorization':'Bearer test-token','X-Local-Request':'1'})
        assert c.post('/api/update').status_code==202
        assert finished.wait(2)
        for _ in range(100):
            result=c.get('/api/update').json()
            if result['status']=='failed':break
        assert result['status']=='failed'
        assert '網路' in result['message']
        assert c.put('/api/glossary',json={'prompts':[],'rules':[]}).status_code==200
