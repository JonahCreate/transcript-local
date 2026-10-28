import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from transcript_local.store import Store, atomic_json, clean_name, read_json, digest
from transcript_local.models import Models, available, REQUIRED
from transcript_local.manager import Manager
from transcript_local.review import export, save_edits, preview
from transcript_local.worker import run

@pytest.mark.parametrize('name',['../x.txt','', 'x.exe','..','a'*230+'.wav'])
def test_bad_filename(name):
    with pytest.raises(ValueError):clean_name(name)

@pytest.mark.parametrize('name',['會議 錄音.m4a','a.mp3','a.wav','a.MP4'])
def test_good_filename(name):assert clean_name(name)==name

def test_arbitrary_job_path_rejected(store):
    with pytest.raises(ValueError):store.job_dir('../secret')

def test_no_user_models_required_for_empty_store(tmp_path):
    s=Store(tmp_path/'new');assert not available(s,'small')
    with pytest.raises(ValueError):Manager(s).settings('small','zh',False)

def test_partial_model_is_not_ready(store):
    (store.models/'small'/REQUIRED[0]).unlink();assert not available(store,'small')

def test_model_download_failure_can_retry(store,monkeypatch):
    import huggingface_hub
    def fail(*a,**kw):raise RuntimeError('offline')
    monkeypatch.setattr(huggingface_hub.HfApi,'model_info',fail)
    m=Models(store)
    for _ in range(2):
        m.download('small');m.thread.join();assert m.status()['download']['status']=='failed'

def test_source_preserved_dedup_and_new_version(store,wav,document):
    before=digest(wav);m=Manager(store)
    first=m.import_file(wav,model='small');second=m.import_file(wav,model='small')
    assert first['id']==second['id'];assert digest(wav)==before
    path=store.job_dir(first['id']);atomic_json(path/'transcript.json',document)
    third=m.import_file(wav,model='small',force=True)
    assert third['id']!=first['id'];assert read_json(path/'transcript.json')==document

def test_settings_and_prompt_change_signature(store,wav):
    m=Manager(store);a=m.import_file(wav,model='small')
    atomic_json(store.root/'glossary.json',{'prompts':['共槽'],'rules':[]})
    b=m.import_file(wav,model='small');assert a['signature']!=b['signature']

def test_edit_preserves_raw_and_conflicts(store,document):
    path=store.root/'doc.json';atomic_json(path,document)
    updated=save_edits(path,{'revision':1,'segments':[{'id':0,'text':'共槽','reviewed':True}]})
    assert updated['segments'][0]['raw_text']=='供钞';assert updated['revision']==2
    with pytest.raises(ValueError):save_edits(path,{'revision':1,'segments':[]})
    assert read_json(path)['segments'][0]['edited_text']=='共槽'

def test_preview_is_not_mutation(document):
    before=json.dumps(document)
    result=preview(document,[{'from':'供鈔','to':'共槽'}])
    assert result[0]['after']=='共槽';assert json.dumps(document)==before

@pytest.mark.parametrize('fmt',['md','txt','srt','json'])
def test_exports_include_edit_and_valid_timing(document,fmt):
    document['segments'][0]['edited_text']='共槽'
    result=export({'name':'測試','model':'small'},document,fmt)
    assert '共槽' in result
    if fmt=='srt':assert '00:00:00,100 --> 00:00:01,000' in result
    if fmt=='json':assert json.loads(result)['segments'][0]['raw_text']=='供钞'

def test_lease_and_restart_interrupted(store,wav):
    a=Manager(store);a.start()
    try:
        with pytest.raises(RuntimeError):Manager(store).start()
    finally:a.close()
    job=Manager(store).import_file(wav,model='small');job['status']='running';store.save(job)
    m=Manager(store);m.start()
    try:assert store.get(job['id'])['status']=='interrupted'
    finally:m.close()

def test_queued_cancel_resume_and_recoverable_clear(store,wav):
    m=Manager(store);j=m.import_file(wav,model='small')
    m.cancel(j['id']);assert store.get(j['id'])['status']=='cancelled'
    m.resume(j['id']);assert store.get(j['id'])['status']=='queued'
    m.cancel(j['id']);m.delete(j['id'])
    assert not store.job_dir(j['id']).exists();assert list((store.root/'trash').iterdir())

def test_model_version_mismatch_blocks_resume(store,wav):
    m=Manager(store);j=m.import_file(wav,model='small');m.cancel(j['id'])
    atomic_json(store.models/'small'/'installed.json',{'revision':'new'})
    with pytest.raises(ValueError):m.resume(j['id'])

@pytest.mark.parametrize('suffix',['.wav','.mp3','.m4a','.mp4'])
def test_audio_conversion_and_worker_checkpoints(store,wav,monkeypatch,suffix,tmp_path):
    source=wav
    if suffix!='.wav':
        source=tmp_path/('測試 來源'+suffix)
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(wav),str(source)],check=True)
    class Model:
        def __init__(self,*a,**kw):assert kw['local_files_only']
        def transcribe(self,*a,**kw):
            assert kw['word_timestamps']
            r=SimpleNamespace(start=0.,end=.8,text='测试共槽',avg_logprob=-.1,compression_ratio=1.,words=[])
            return iter([r]),None
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Model))
    j=Manager(store).import_file(source,model='small');run(store,j['id'])
    path=store.job_dir(j['id']);doc=read_json(path/'transcript.json')
    assert doc['segments'][0]['traditional_text']=='測試共槽'
    doc['segments'][0]['edited_text']='人工修改';atomic_json(path/'transcript.json',doc)
    run(store,j['id'])
    assert read_json(path/'transcript.json')['segments'][0]['edited_text']=='人工修改'
    assert (path/'chunk-0000.json').exists()

def test_source_change_stops_worker(store,wav):
    j=Manager(store).import_file(wav,model='small')
    (store.job_dir(j['id'])/'source.wav').write_bytes(b'changed')
    with pytest.raises(RuntimeError,match='來源'):run(store,j['id'])

def test_corrupt_audio_no_completed_doc(store,tmp_path):
    source=tmp_path/'bad.wav';source.write_text('not audio')
    j=Manager(store).import_file(source,model='small')
    with pytest.raises(RuntimeError,match='音軌'):run(store,j['id'])
    assert not (store.job_dir(j['id'])/'transcript.json').exists()

def test_video_without_audio(store,tmp_path):
    source=tmp_path/'silent.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=32x32:d=0.2','-an',str(source)],check=True)
    j=Manager(store).import_file(source,model='small')
    with pytest.raises(RuntimeError,match='音軌'):run(store,j['id'])

def test_real_process_cancel_and_queue_continues(store,wav,monkeypatch):
    import transcript_local.manager as module
    real=subprocess.Popen
    def worker(command,**kw):
        # The first attempt sleeps; resume observes its checkpoint and finishes.
        root,job_id=command[-2:]
        script="""import sys,time,json,pathlib
p=pathlib.Path(sys.argv[1])/'jobs'/sys.argv[2]
if not (p/'checkpoint').exists():
 (p/'checkpoint').write_text('saved');time.sleep(30)
(p/'transcript.json').write_text('{"revision":1,"segments":[]}')
"""
        return real([sys.executable,'-c',script,root,job_id],**kw)
    monkeypatch.setattr(module.subprocess,'Popen',worker)
    m=Manager(store);m.start()
    try:
        j=m.import_file(wav,model='small')
        deadline=time.time()+8
        while not (store.job_dir(j['id'])/'checkpoint').exists() and time.time()<deadline:time.sleep(.05)
        assert m.proc is not None
        m.cancel(j['id'])
        deadline=time.time()+8
        while store.get(j['id'])['status']=='cancelling' and time.time()<deadline:time.sleep(.05)
        assert store.get(j['id'])['status']=='cancelled'
        m.resume(j['id'])
        deadline=time.time()+8
        while store.get(j['id'])['status']!='complete' and time.time()<deadline:time.sleep(.05)
        assert store.get(j['id'])['status']=='complete'
    finally:m.close()

def test_reimport_interrupted_job_resumes_instead_of_starting_new(store,wav):
    m=Manager(store);j=m.import_file(wav,model='small')
    m.cancel(j['id'])
    again=m.import_file(wav,model='small')
    assert again['id']==j['id']
    m.resume(again['id'])
    assert store.get(j['id'])['status']=='queued'

def test_model_supports_both_vocabulary_formats(store):
    root=store.models/'small'
    (root/'vocabulary.txt').rename(root/'vocabulary.json')
    assert available(store,'small')
    (root/'vocabulary.json').unlink()
    assert not available(store,'small')
