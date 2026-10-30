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

def test_managed_update_request_guards_and_unlock(client,store,wav,monkeypatch):
    monkeypatch.setattr('transcript_local.updater.check_remote',lambda current:{'check_status':'update_available','can_update':True,'check_message':'有新版'})
    monkeypatch.setenv('IHT_MANAGED','1')
    atomic_json(store.root/'runtime/supervisor.json',{'secret':'supervisor-secret'})
    job=client.post('/api/jobs',files={'file':('a.wav',wav.read_bytes())}).json()
    assert client.post('/api/update').status_code==409
    client.post(f'/api/jobs/{job["id"]}/cancel')
    assert client.post('/api/update').status_code==202
    assert read_json(store.root/'runtime/update-request.json')['secret']=='supervisor-secret'
    assert client.post('/api/update').status_code==409
    assert client.put('/api/glossary',json={'prompts':[],'rules':[]}).status_code==409
    assert client.post('/api/update/unlock',json={'secret':'wrong'}).status_code==403
    atomic_json(store.root/'runtime/update.json',{'status':'failed','message':'offline'})
    assert client.post('/api/update/unlock',json={'secret':'supervisor-secret'}).status_code==200
    assert client.put('/api/glossary',json={'prompts':[],'rules':[]}).status_code==200


def test_foreground_cannot_update_live_environment(client):
    assert not client.get('/api/update').json()['available']
    assert client.post('/api/update').status_code==409


def test_drafts_are_separate_and_version_checked_export(client,store,wav,document):
    job=client.post('/api/jobs',files={'file':('a.wav',wav.read_bytes())}).json()
    path=store.job_dir(job['id'])/'transcript.json';atomic_json(path,document)
    base=f'/api/jobs/{job["id"]}'
    draft={'window_id':'first-tab','base_revision':1,'position':200,'segments':[{'id':0,'text':'草稿文字','reviewed':True}]}
    assert client.put(base+'/draft',json=draft).status_code==200
    assert read_json(path)==document
    assert client.get(base+'/drafts').json()[0]['segments'][0]['text']=='草稿文字'
    assert client.put(base+'/transcript',json={'revision':1,'segments':draft['segments']}).status_code==200
    assert client.get(base+'/export/txt?revision=1').status_code==409
    assert '草稿文字' in client.get(base+'/export/txt?revision=2').text
    assert client.get(base+'/drafts').json()[0]['base_revision']==1
    assert client.put('/api/preferences',json={'job_id':job['id'],'position':200}).status_code==200
    assert client.get('/api/preferences').json()['position']==200
    assert client.delete(base+'/drafts/first-tab').status_code==200
    assert client.get(base+'/drafts').json()==[]
    draft['window_id']='../other'
    assert client.put(base+'/draft',json=draft).status_code==422


def test_health_authenticated_and_shutdown(store):
    import threading
    done=threading.Event()
    with TestClient(create_app(store,token='test-token',manager=IdleManager(store),on_stop=done.set)) as c:
        assert c.get('/api/health').status_code==401
        c.headers.update({'Authorization':'Bearer test-token','X-Local-Request':'1'})
        assert c.get('/api/health').json()['ready']
        assert c.post('/api/service/stop').status_code==202
        assert done.wait(2)


def test_no_update_is_noop_even_with_queued_job(client,store,wav,monkeypatch):
    monkeypatch.setattr('transcript_local.updater.check_remote',lambda current:{'check_status':'up_to_date','can_update':False,'check_message':'已是最新版本。'})
    job=client.post('/api/jobs',files={'file':('a.wav',wav.read_bytes())}).json()
    result=client.post('/api/update')
    assert result.status_code==200
    assert not result.json()['can_update']
    assert store.get(job['id'])['status']=='queued'
    assert not (store.root/'runtime/update-request.json').exists()
    assert client.put('/api/glossary',json={'prompts':[],'rules':[]}).status_code==200
