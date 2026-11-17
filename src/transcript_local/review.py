from collections import Counter
from difflib import SequenceMatcher
import json
from .store import atomic_json, read_json, now

FORMAT_NAMES = {'md': '逐字稿.md', 'txt': '逐字稿.txt', 'srt': '字幕.srt', 'json': '逐字稿.json'}

def text(segment):
    return segment['edited_text'] if segment.get('edited_text') is not None else segment['traditional_text']

def timestamp(value, subtitle=False):
    ms = max(0, round(value*1000))
    hours, rest = divmod(ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, ms = divmod(rest, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}{"," if subtitle else "."}{ms:03}'

def export(job, doc, fmt):
    if fmt == 'json':
        return json.dumps({'source_name': job['name'], 'model': job['model'], **doc}, ensure_ascii=False, indent=2)
    rows = doc['segments']
    if fmt == 'srt':
        visible = [r for r in rows if text(r).strip()]
        return '\n\n'.join(f'{i}\n{timestamp(r["start"],True)} --> {timestamp(r["end"],True)}\n{text(r)}' for i,r in enumerate(visible,1))+'\n'
    title = job['name']+'｜逐字稿'
    note = '自動辨識與人工編修稿；待確認標記不代表一定錯誤。'
    sections = []
    for r in rows:
        time = f'[{timestamp(r["start"])}–{timestamp(r["end"])}]'
        pending = '〔待確認：'+'；'.join(r['review_reasons'])+'〕' if r['review_reasons'] and not r['reviewed'] else ''
        sections.append((f'### {time}' if fmt == 'md' else time)+'\n\n'+text(r)+(('\n'+pending) if pending else ''))
    return ('# ' if fmt=='md' else '')+title+'\n\n'+note+'\n\n'+'\n\n'.join(sections)+'\n'

def save_edits(path, payload):
    doc = read_json(path)
    if doc is None:
        raise FileNotFoundError('逐字稿尚未完成')
    if payload['revision'] != doc['revision']:
        raise ValueError('另一個視窗已修改逐字稿，請重新載入後編輯')
    indexed = {r['id']: r for r in doc['segments']}
    for item in payload['segments']:
        if item['id'] not in indexed:
            raise ValueError('找不到片段')
    for item in payload['segments']:
        row = indexed[item['id']]
        row['edited_text'] = item['text']
        row['reviewed'] = item['reviewed']
    doc['revision'] += 1
    doc['updated_at'] = now()
    atomic_json(path, doc)
    return doc

def learned(docs, rules):
    """Corrections the user keeps making by hand, offered as glossary rules."""
    known = {rule['from'] for rule in rules}
    counts = Counter()
    for doc in docs:
        for r in doc['segments']:
            before, after = r['traditional_text'], r.get('edited_text')
            if after is None or after == before:
                continue
            for op, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
                # Punctuation-only edits are not vocabulary.
                if op != 'replace' or not (any(ch.isalnum() for ch in before[a:b]) and any(ch.isalnum() for ch in after[c:d])):
                    continue
                # A one-character rule would also hit unrelated words, so carry a neighbour along.
                if b - a < 2 or d - c < 2:
                    if a and c and before[a-1].isalnum():
                        a -= 1; c -= 1
                    elif b < len(before) and d < len(after) and before[b].isalnum():
                        b += 1; d += 1
                old, new = before[a:b], after[c:d]
                if 2 <= len(old) <= 12 and 1 <= len(new) <= 12 and old not in known and '\n' not in old + new:
                    counts[old, new] += 1
    # A single edit is usually about that one sentence; a repeated one is vocabulary.
    return [{'from': old, 'to': new, 'count': count} for (old, new), count in counts.most_common(20) if count > 1]


def preview(doc, rules):
    changes = []
    for r in doc['segments']:
        before = text(r)
        after = before
        for rule in rules:
            after = after.replace(rule['from'], rule['to'])
        if after != before:
            changes.append({'id': r['id'], 'start': r['start'], 'before': before, 'after': after})
    return changes
