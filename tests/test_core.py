import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from transcript_local.store import Store, atomic_json, clean_name, read_json, digest
from transcript_local.models import Models, available, resolve_model, REQUIRED
from transcript_local.manager import Manager
from transcript_local.review import export, save_edits, preview
from transcript_local.worker import run

def install_fake_qwen(store):
    from transcript_local.models import QWEN_REQUIRED, ALIGNER_REQUIRED
    for name, files in [('qwen3',QWEN_REQUIRED),('qwen3-aligner',ALIGNER_REQUIRED)]:
        directory=store.models/name;directory.mkdir()
        for filename in files:(directory/filename).write_text('{}')
        atomic_json(directory/'installed.json',{'revision':name+'-revision','repo':'test'})

def test_qwen_requires_both_models_and_pins_aligner(store):
    install_fake_qwen(store)
    assert available(store,'qwen3')
    settings=Manager(store).settings('qwen3','zh',False)
    assert settings['aligner_revision']=='qwen3-aligner-revision'
    (store.models/'qwen3-aligner'/'model.safetensors').unlink()
    assert not available(store,'qwen3')
    with pytest.raises(ValueError,match='時間對齊'):Manager(store).settings('qwen3','zh',False)

def test_qwen_alignment_preserves_punctuation_and_rejects_mismatch():
    from transcript_local.worker import qwen_rows
    result=SimpleNamespace(text='你好，世界！Hello.',segments=[{'text':'你好','start':0.1,'end':0.5},{'text':'世界','start':0.6,'end':1.0},{'text':'Hello','start':1.2,'end':1.8}])
    rows=qwen_rows(result,30)
    assert ''.join(row['text'] for row in rows)==result.text
    assert rows[0]['words'][0]['start']==30.1
    assert rows[0]['avg_logprob'] is None
    mixed=SimpleNamespace(text='AI-powered，價格 1,000 元。',segments=[{'text':'AIpowered','start':0.1,'end':0.5},{'text':'價','start':0.6,'end':0.7},{'text':'格','start':0.7,'end':0.8},{'text':'1000','start':0.8,'end':1.0},{'text':'元','start':1.0,'end':1.2}])
    assert ''.join(row['text'] for row in qwen_rows(mixed,0))==mixed.text
    with pytest.raises(RuntimeError,match='不一致'):
        qwen_rows(SimpleNamespace(text='你好',segments=[{'text':'錯誤','start':0,'end':1}]),0)

def test_qwen_worker_checkpoint_and_edits(store,wav,monkeypatch):
    install_fake_qwen(store)
    class Session:
        def __init__(self,model):pass
        def transcribe(self,samples,**kwargs):
            assert kwargs['return_timestamps'] and kwargs['language']=='Chinese'
            return SimpleNamespace(text='测试共槽。',segments=[{'start':0.1,'end':0.8,'text':'测试共槽'}])
    monkeypatch.setitem(sys.modules,'mlx_qwen3_asr',SimpleNamespace(Session=Session))
    monkeypatch.setitem(sys.modules,'mlx_qwen3_asr.forced_aligner',SimpleNamespace(ForcedAligner=lambda path: object()))
    job=Manager(store).import_file(wav,model='qwen3');run(store,job['id'])
    path=store.job_dir(job['id']);doc=read_json(path/'transcript.json')
    assert doc['segments'][0]['traditional_text']=='測試共槽。'
    doc['segments'][0]['edited_text']='人工校稿';atomic_json(path/'transcript.json',doc)
    run(store,job['id'])
    assert read_json(path/'transcript.json')['segments'][0]['edited_text']=='人工校稿'
    assert (path/'chunk-0000.json').exists()

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
    bad_stdin=(store.root/'unreadable-stdin').open('wb')
    def worker(command,**kw):
        # Simulate a parent whose terminal input is no longer readable.
        kw.setdefault('stdin',bad_stdin)
        # The first attempt sleeps; resume observes its checkpoint and finishes.
        root,job_id=command[-2:]
        script="""import sys,time,json,pathlib
assert sys.stdin.read() == ''
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
    finally:
        m.close()
        bad_stdin.close()

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


def cached_model(root, name, revision):
    from transcript_local.models import CATALOG
    repo = root / ('models--' + CATALOG[name]['repo'].replace('/', '--'))
    snapshot = repo / 'snapshots' / revision
    snapshot.mkdir(parents=True)
    for filename in (*REQUIRED, 'vocabulary.txt'):
        (snapshot / filename).write_text('{}')
    (repo / 'refs').mkdir(exist_ok=True)
    (repo / 'refs' / 'main').write_text(revision)
    return snapshot


def test_existing_huggingface_cache_is_ready_without_copy_or_network(tmp_path, monkeypatch, wav):
    cache = tmp_path / 'hf-cache'
    monkeypatch.setenv('HF_HUB_CACHE', str(cache))
    revision = 'a' * 40
    snapshot = cached_model(cache, 'medium', revision)
    store = Store(tmp_path / 'fresh')
    model = resolve_model(store, 'medium')
    assert model == {'path': snapshot, 'revision': revision, 'source': 'cache'}
    assert Models(store).status()['models'][0]['ready']
    assert not (store.models / 'medium').exists()
    job = Manager(store).import_file(wav, model='medium')
    assert job['model_revision'] == revision


def test_cached_revision_pinned_during_resume(tmp_path, monkeypatch, wav):
    cache = tmp_path / 'hf-cache'
    monkeypatch.setenv('HF_HUB_CACHE', str(cache))
    revision = 'b' * 40
    cached_model(cache, 'small', revision)
    store = Store(tmp_path / 'fresh')
    manager = Manager(store)
    job = manager.import_file(wav, model='small')
    manager.cancel(job['id'])
    local = store.models / 'small'; local.mkdir()
    for filename in (*REQUIRED, 'vocabulary.json'): (local / filename).write_text('{}')
    atomic_json(local / 'installed.json', {'revision': 'different-revision'})
    assert resolve_model(store, 'small')['source'] == 'app'
    assert resolve_model(store, 'small', revision)['source'] == 'cache'
    assert manager.resume(job['id'])['status'] == 'queued'


def test_incomplete_cache_is_not_reported_ready(tmp_path, monkeypatch):
    cache = tmp_path / 'hf-cache'
    monkeypatch.setenv('HF_HUB_CACHE', str(cache))
    snapshot = cached_model(cache, 'medium', 'c' * 40)
    (snapshot / 'model.bin').unlink()
    store = Store(tmp_path / 'fresh')
    assert not available(store, 'medium')


def test_worker_temporary_start_failure_retries_only_once(store,wav,monkeypatch):
    import transcript_local.manager as module
    real=subprocess.Popen
    def failed_worker(command,**kwargs):
        return real([sys.executable,'-c',"import sys;sys.stderr.write('Fatal Python error: init_sys_streams: Bad file descriptor');sys.exit(1)"],**kwargs)
    monkeypatch.setattr(module.subprocess,'Popen',failed_worker)
    m=Manager(store);m.start();job=m.import_file(wav,model='small')
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            current=store.get(job['id'])
            if current['status']=='failed':break
            time.sleep(.05)
        assert current['status']=='failed'
        assert current['recovery_attempts']==1
    finally:m.close()


def test_crash_recovery_never_resumes_cancelled_task(store,wav,monkeypatch):
    manager=Manager(store);job=manager.import_file(wav,model='small');manager.cancel(job['id'])
    monkeypatch.setenv('IHT_RECOVER_CRASH','1')
    manager.start()
    try:assert store.get(job['id'])['status']=='cancelled'
    finally:manager.close()
