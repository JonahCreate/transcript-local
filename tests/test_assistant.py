import json
import time
from fastapi.testclient import TestClient
import transcript_local.assistant as assistant
from transcript_local.review import learned
from transcript_local.store import atomic_json
from transcript_local.web import create_app


def test_model_may_only_swap_short_exact_phrases():
    rows = [{'id': 1, 'start': 0, 'text': '供鈔這個詞不能用四百'}]
    fixes = [{'id': 1, 'from': '供鈔', 'to': '共槽', 'reason': '同音'},
             {'id': 1, 'from': '不能', 'to': '能', 'reason': '否定詞'},
             {'id': 1, 'from': '不在原文', 'to': '改寫'}, {'id': 1, 'from': '供鈔這個詞不能用四百' * 3, 'to': '摘要'},
             {'id': 2, 'from': '供鈔', 'to': '別段'}, 'not a dict']
    [change] = assistant.merge(rows, fixes)
    assert change['after'] == '共槽這個詞能用四百'
    assert [f['risky'] for f in change['fixes']] == [False, True] and change['risky']
    # A whole-sentence answer is cut down to the characters that change, at the right place.
    [trimmed] = assistant.merge([{'id': 3, 'start': 0, 'text': '在說一次，我在家再確認'}], [{'id': 3, 'from': '我在家再確認', 'to': '我在家在確認'}])
    assert trimmed['fixes'][0]['from'] == '再' and trimmed['after'] == '在說一次，我在家在確認'
    assert assistant.near('今天聊品排定位', ['品牌定位', '共槽']) == [{'from': '品排定位', 'to': '品牌定位', 'reason': '和詞彙表的「品牌定位」只差一個字'}]


def test_learned_turns_repeated_edits_into_rules():
    doc = {'segments': [{'traditional_text': '這個品排的供鈔很好，品排', 'edited_text': '這個品牌的共槽很好。品牌'},
                        {'traditional_text': '沒改', 'edited_text': None}]}
    assert learned([doc], []) == [{'from': '品排', 'to': '品牌', 'count': 2}]  # the one-off edit is left out
    assert learned([doc], [{'from': '品排', 'to': '品牌'}]) == []


def test_ai_run_skips_confirmed_segments_and_reports_changes(store, wav, document, monkeypatch):
    asked = []
    def fake(path, body=None, timeout=2):
        if path == '/api/tags': return {'models': [{'name': 'qwen2.5:7b', 'details': {'family': 'qwen2'}}, {'name': 'bge-m3', 'details': {'family': 'bert'}}]}
        asked.append(body)
        return {'message': {'content': json.dumps({'fixes': [{'id': 0, 'from': '供鈔', 'to': '共槽', 'reason': '同音', 'confidence': 'high'}]})}}
    monkeypatch.setattr(assistant, 'call', fake)
    with TestClient(create_app(store, 'token'), headers={'Authorization': 'Bearer token', 'X-Local-Request': '1'}) as client:
        job = client.app.state.manager.import_file(wav, model='small'); client.app.state.manager.cancel(job['id'])
        document['segments'][1]['reviewed'] = True
        atomic_json(store.job_dir(job['id'])/'transcript.json', document)
        status = client.get('/api/status').json()
        assert status['advice']['ai_model'] == 'qwen2.5:7b' and status['stale'] is False
        assert assistant.pick({'big:70b': 40 * 1024**3, 'mid:8b': 5 * 1024**3, 'tiny:1b': 1024**3}, 'qwen2.5:7b', 16) == 'mid:8b'
        assert client.post(f'/api/jobs/{job["id"]}/ai').status_code == 202
        deadline = time.time() + 5
        while (task := client.get('/api/ai').json())['status'] == 'running' and time.time() < deadline: time.sleep(.02)
        assert task['status'] == 'complete' and task['total'] == 1
        assert task['changes'][0]['after'] == '共槽' and '你好' not in asked[0]['messages'][1]['content']
        assert client.get('/api/glossary/suggestions').json() == []
