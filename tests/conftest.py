import json
import wave
from pathlib import Path
import pytest
from transcript_local.store import Store, atomic_json
from transcript_local.models import REQUIRED

@pytest.fixture(autouse=True)
def isolated_huggingface_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'hf-cache'))

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'data')
    for name in ['small','medium']:
        directory=s.models/name;directory.mkdir()
        for filename in (*REQUIRED,'vocabulary.txt'):(directory/filename).write_text('{}')
        atomic_json(directory/'installed.json',{'revision':'test-revision','repo':'test'})
    return s

@pytest.fixture
def wav(tmp_path):
    p=tmp_path/'中文 錄音.wav'
    with wave.open(str(p),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*16000)
    return p

@pytest.fixture
def document():
    return {'revision':1,'duration':2,'segments':[
        {'id':0,'start':0.1,'end':1.0,'raw_text':'供钞','traditional_text':'供鈔','edited_text':None,'reviewed':False,'review_reasons':['辨識把握較低'],'words':[]},
        {'id':1,'start':1.1,'end':2.0,'raw_text':'你好','traditional_text':'你好','edited_text':None,'reviewed':False,'review_reasons':[],'words':[]}]}
