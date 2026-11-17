"""Proofreading suggestions from a local Ollama model. Loopback only: nothing leaves this Mac.

The model never rewrites a segment. It may only name a short exact phrase and its
replacement; check() drops everything else, so no text can be lost or reworded.
"""
import json
import os
import platform
import shutil
import subprocess
import threading
import urllib.request
from .review import text

OLLAMA = 'http://127.0.0.1:11434'
# (minimum GB of memory, model). advice() suggests the largest one this Mac can hold
# while a transcription model is loaded too.
TIERS = [(0, 'qwen2.5:3b'), (16, 'qwen2.5:7b'), (32, 'qwen2.5:14b'), (64, 'qwen2.5:32b')]
# A change that touches one of these can flip the meaning, so a person must listen first.
RISKY = set('不沒無未別非否勿莫零一二三四五六七八九十百千萬億兩0123456789')
BATCH = 12
SYSTEM = ('你是繁體中文逐字稿的校對員。這些文字來自語音辨識。'
          '只找出辨識造成的錯字、同音字、被聽錯的詞。'
          '不要改寫、潤飾、刪減、補字、改標點或改語氣；口語、贅詞、重複都保留。'
          '不要把口語換成書面語，例如「塊」不要改成「元」。'
          '專有名詞以詞彙表為準。沒有把握就不要列。'
          '每一項的 from 必須是該片段裡一字不差的原文，而且越短越好；to 是修正後的文字；reason 用一句話說明。')
SCHEMA = {'type': 'object', 'required': ['fixes'], 'properties': {'fixes': {'type': 'array', 'items': {
    'type': 'object', 'required': ['id', 'from', 'to', 'reason', 'confidence'], 'properties': {
        'id': {'type': 'integer'}, 'from': {'type': 'string'}, 'to': {'type': 'string'},
        'reason': {'type': 'string'}, 'confidence': {'type': 'string', 'enum': ['high', 'medium']}}}}}}


def call(path, body=None, timeout=2):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(OLLAMA + path, data=data, headers={'Content-Type': 'application/json'})
    # No proxy: this must never be routed off the machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def installed():
    """Chat models Ollama has as {name: bytes}, or None when Ollama is not running."""
    try:
        models = call('/api/tags')['models']
    except (OSError, ValueError, KeyError):
        return None
    return {m['name']: m.get('size', 0) for m in models
            if m.get('details', {}).get('family') != 'bert' and 'embed' not in m['name']}


def pick(models, suggested, ram):
    """The model to use: an explicit choice, else the best fitting tier, else the largest that fits memory."""
    if not models:
        return None
    wanted = os.environ.get('IHT_AI_MODEL')
    if wanted in models:
        return wanted
    fitting = [name for _, name in TIERS[:[name for _, name in TIERS].index(suggested) + 1] if name in models]
    if fitting:
        return fitting[-1]
    # Leave more than half the memory for the transcription model and the rest of the Mac.
    roomy = [name for name, size in models.items() if size <= ram * .4 * 1024 ** 3]
    return max(roomy, key=models.get) if roomy else min(models, key=models.get)


def advice():
    """What this Mac can run, for the setup page."""
    ram = round(os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE') / 1024 ** 3)
    try:
        chip = subprocess.run(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        chip = ''
    suggested = [name for need, name in TIERS if ram >= need][-1]
    models = installed()
    return {'chip': chip or platform.machine(), 'ram_gb': ram,
            'asr': 'medium' if ram >= 16 else 'small', 'asr_alternative': 'qwen3' if ram >= 24 else None,
            'ai_suggested': suggested, 'ai_installed': models is not None or bool(shutil.which('ollama')),
            'ai_running': models is not None, 'ai_models': list(models or []), 'ai_model': pick(models, suggested, ram)}


def near(segment, terms):
    """Glossary terms the text almost contains: same length, one character off. No model needed."""
    fixes = []
    for term in terms:
        if len(term) < 3 or term in segment:
            continue
        for start in range(len(segment) - len(term) + 1):
            window = segment[start:start + len(term)]
            if sum(a != b for a, b in zip(window, term)) == 1:
                fixes.append({'from': window, 'to': term, 'reason': f'和詞彙表的「{term}」只差一個字'})
                break
    return fixes


def check(original, fix):
    """One suggestion as a safe change, or None if it is anything but a short exact swap."""
    old, new = str(fix.get('from', '')), str(fix.get('to', '')).strip()
    at = original.find(old)
    if not old.strip() or not new or old == new or at < 0 or len(old) > 40 or len(new) > 40:
        return None
    # Models tend to quote the whole sentence; keep only the characters that really change.
    head = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
    tail = next((i for i, (a, b) in enumerate(zip(old[head:][::-1], new[head:][::-1])) if a != b),
                min(len(old), len(new)) - head)
    core_old, core_new = old[head:len(old) - tail], new[head:len(new) - tail]
    if core_old and core_new:
        old, new, at = core_old, core_new, at + head
    if len(old) > 12 or len(new) > 12:
        return None
    risky = sorted(c for c in old if c in RISKY) != sorted(c for c in new if c in RISKY)
    return {'from': old, 'to': new, 'reason': str(fix.get('reason', ''))[:120], 'risky': risky, 'at': at}


def merge(rows, fixes):
    """Apply the surviving fixes segment by segment and describe each changed segment."""
    changes = []
    for row in rows:
        after, kept = row['text'], []
        for fix in fixes:
            safe = check(after, fix) if isinstance(fix, dict) and fix.get('id') == row['id'] else None
            if safe:
                at = safe.pop('at')
                after = after[:at] + safe['to'] + after[at + len(safe['from']):]
                kept.append(safe)
        if kept:
            changes.append({'id': row['id'], 'start': row['start'], 'before': row['text'], 'after': after,
                            'fixes': kept, 'risky': any(f['risky'] for f in kept)})
    return changes


class Assistant:
    """One proofreading run at a time: idle -> running -> complete | failed (see states.py)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.task = {'status': 'idle'}
        self.cancelled = threading.Event()

    def status(self):
        with self.lock:
            return dict(self.task)

    def start(self, job_id, doc, glossary):
        info = advice()
        if not info['ai_model']:
            raise ValueError('找不到可用的本機 AI 模型。請到「環境與模型」查看設定方式。')
        # Segments the user already confirmed are never asked about again.
        rows = [{'id': r['id'], 'start': r['start'], 'text': text(r)} for r in doc['segments']
                if not r['reviewed'] and text(r).strip()]
        if not rows:
            raise ValueError('沒有尚未確認的片段。')
        with self.lock:
            if self.task['status'] == 'running':
                raise ValueError('AI 校稿正在進行中。')
            self.task = {'status': 'running', 'job_id': job_id, 'revision': doc['revision'], 'model': info['ai_model'],
                         'done': 0, 'total': len(rows), 'changes': [], 'error': None}
        self.cancelled.clear()
        threading.Thread(target=self._run, args=(rows, glossary, info['ai_model']), daemon=True).start()
        return self.status()

    def cancel(self):
        self.cancelled.set()

    def _run(self, rows, glossary, model):
        known = list(dict.fromkeys(glossary['prompts'] + [rule['to'] for rule in glossary['rules']]))
        terms = '、'.join(known)
        try:
            for index in range(0, len(rows), BATCH):
                if self.cancelled.is_set():
                    raise RuntimeError('已停止。')
                batch = rows[index:index + BATCH]
                question = '詞彙表：' + (terms or '（無）') + '\n片段：' + json.dumps(
                    [{'id': r['id'], 'text': r['text']} for r in batch], ensure_ascii=False)
                answer = call('/api/chat', {'model': model, 'stream': False, 'format': SCHEMA,
                                            'options': {'temperature': 0, 'num_ctx': 8192},
                                            'messages': [{'role': 'system', 'content': SYSTEM},
                                                         {'role': 'user', 'content': question}]}, timeout=600)
                fixes = json.loads(answer['message']['content']).get('fixes', [])
                # The glossary comparison goes first, so the user's own terms win over the model.
                certain = [dict(fix, id=r['id']) for r in batch for fix in near(r['text'], known)]
                found = merge(batch, certain + (fixes if isinstance(fixes, list) else []))
                with self.lock:
                    self.task['changes'] += found
                    self.task['done'] = min(len(rows), index + BATCH)
            with self.lock:
                self.task['status'] = 'complete'
        except Exception as exc:  # a failed run must never take the service down
            with self.lock:
                self.task.update(status='failed', error=str(exc))
