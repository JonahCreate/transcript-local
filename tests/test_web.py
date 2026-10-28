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
