"""One isolated process per recording. Model inference never downloads weights."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unicodedata
import wave
from .store import Store, atomic_json, digest, read_json, now
from .models import resolve_model


def progress(path, **data):
    atomic_json(path/'progress.json', data)
    print(json.dumps(data, ensure_ascii=False), flush=True)

class AlignmentError(RuntimeError):
    pass


def qwen_rows(result, offset):
    """Keep the model's complete text while grouping aligned words into reviewable phrases."""
    text = result.text
    words = result.segments or []
    if text.strip() and not words:
        # Qwen can output punctuation alone in silence; the aligner has no
        # spoken words to time. Never discard actual text on alignment failure.
        if all(char.isspace() or unicodedata.category(char).startswith('P') for char in text):
            return []
        raise AlignmentError('時間對齊未產生結果，含語音文字的片段尚未保存；請查看錯誤並回聽音訊')
    rows = []
    cursor = 0
    group_start = 0
    group_words = []
    # The aligner removes punctuation inside tokens (e.g. "AI-powered" becomes
    # "AIpowered"). Match its cleaned characters back to the original text.
    normalized = []
    positions = []
    for index, char in enumerate(text):
        if char == "'" or unicodedata.category(char)[0] in 'LN':
            for folded in char.casefold():
                normalized.append(folded);positions.append(index)
    normalized = ''.join(normalized)
    normalized_cursor = 0
    for word in words:
        token = word['text'].strip()
        if not token: continue
        clean = ''.join(c.casefold() for c in token if c == "'" or unicodedata.category(c)[0] in 'LN')
        if not clean: continue
        if normalized[normalized_cursor:normalized_cursor+len(clean)] != clean:
            raise AlignmentError('時間對齊結果與辨識文字不一致，已停止以避免遺漏文字')
        normalized_cursor += len(clean)
        end = positions[normalized_cursor-1]+1
        start, finish = float(word['start']), float(word['end'])
        if not (math.isfinite(start) and math.isfinite(finish) and 0 <= start <= finish):
            raise AlignmentError('時間對齊結果含無效時間')
        group_words.append({'start':start+offset, 'end':finish+offset, 'text':word['text']})
        cursor = end
        tail = cursor
        while tail < len(text) and (text[tail].isspace() or text[tail] in '，。！？；、,.!?;:：\"\'）)」』'):
            tail += 1
        if any(c in text[cursor:tail] for c in '。！？.!?；;') or group_words[-1]['end']-group_words[0]['start'] >= 12:
            rows.append({'start':group_words[0]['start']-offset, 'end':group_words[-1]['end']-offset,
                         'text':text[group_start:tail], 'avg_logprob':None, 'compression_ratio':None, 'words':group_words})
            group_start = tail
            group_words = []
    if group_words:
        rows.append({'start':group_words[0]['start']-offset, 'end':group_words[-1]['end']-offset,
                     'text':text[group_start:], 'avg_logprob':None, 'compression_ratio':None, 'words':group_words})
    elif rows and text[group_start:].strip():
        rows[-1]['text'] += text[group_start:]
    if normalized_cursor != len(normalized):
        raise AlignmentError('時間對齊未涵蓋完整辨識文字，已停止以避免遺漏文字')
    return rows

def qwen_transcribe_rows(model, aligner, samples, start, language, prompts):
    """Retry apparently empty 30s results in shorter ranges, without hiding gaps."""
    def transcribe(audio):
        return model.transcribe(audio, language=language, context='、'.join(prompts),
                                return_timestamps=True, forced_aligner=aligner)
    def rows_for(audio, offset):
        result = transcribe(audio)
        try:
            rows = qwen_rows(result, offset)
            duration = len(audio) / 16000
            if any(not 0 <= row['start'] < row['end'] <= duration + .05 for row in rows):
                raise AlignmentError('時間對齊結果超出片段範圍')
            return result, rows
        except AlignmentError:
            return result, [{
                'start': 0., 'end': len(audio) / 16000, 'text': result.text,
                'avg_logprob': None, 'compression_ratio': None, 'words': [],
                'review_reasons': ['文字時間未對齊，此時間為整段音訊範圍，請回聽確認'],
            }]

    _, rows = rows_for(samples, start)
    if rows:return rows
    rows=[]
    split=len(samples)//2
    for begin,stop in ((0,split),(split,len(samples))):
        if stop<=begin:continue
        offset=begin/16000
        part, part_rows = rows_for(samples[begin:stop], start+offset)
        if not part_rows:
            part_rows=[{'start':0.,'end':(stop-begin)/16000,'text':part.text,
                        'display_text':'（此片段未辨識出文字，請回聽確認）',
                        'avg_logprob':None,'compression_ratio':None,'words':[],
                        'review_reasons':['此片段未辨識出文字，請回聽確認']}]
        else:
            for row in part_rows:
                row.setdefault('review_reasons', []).append('切短重試辨識，建議回聽確認')
        for row in part_rows:
            row['start']+=offset;row['end']+=offset
        rows.extend(part_rows)
    return rows


def run(store, job_id):
    job = store.get(job_id)
    path = store.job_dir(job_id)
    source = path / ('source'+job['suffix'])
    if digest(source) != job['source_sha256']:
        raise RuntimeError('來源副本有變動，不能沿用進度；請建立新任務')
    audio = path/'audio.wav'
    if not audio.exists():
        progress(path, phase='preparing', message='準備可辨識與回聽的音訊')
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(source), '-map', '0:a:0', '-vn']
        if job['enhance']:
            cmd += ['-af', 'highpass=f=75,afftdn=nr=6:nf=-28:tn=1,dynaudnorm=f=150:g=9:p=0.7']
        cmd += ['-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(path/'audio.partial.wav')]
        prep = subprocess.run(cmd, capture_output=True, text=True)
        if prep.returncode:
            raise RuntimeError('無法讀取音軌；請確認檔案未損壞且含音訊。'+prep.stderr[-800:])
        (path/'audio.partial.wav').replace(audio)
    with wave.open(str(audio)) as wav:
        duration = wav.getnframes()/wav.getframerate()
        if duration <= 0:
            raise RuntimeError('音軌長度為零')
        ranges = []
        start = 0.0
        while start < duration:
            end = min(start+(30 if job['model']=='qwen3' else 300), duration)
            ranges.append((start,end))
            if end >= duration: break
            start = end-1
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        import numpy as np
        from opencc import OpenCC
        progress(path, phase='loading', message='載入本機模型', total=len(ranges), completed=0)
        model = None
        chunks = []
        for index,(start,end) in enumerate(ranges):
            checkpoint = path/f'chunk-{index:04}.json'
            saved = read_json(checkpoint)
            if saved:
                chunks.append(saved)
            else:
                if model is None:
                    resolved = resolve_model(store, job['model'], job['model_revision'])
                    if not resolved: raise RuntimeError('原本使用的模型版本已不存在，請恢復該版本或建立新任務')
                    if job['model']=='qwen3':
                        from mlx_qwen3_asr import Session
                        from mlx_qwen3_asr.forced_aligner import ForcedAligner
                        aligner_model=resolve_model(store,'qwen3-aligner',job['aligner_revision'])
                        if not aligner_model:raise RuntimeError('原本使用的時間對齊模型版本已不存在')
                        model=Session(model=str(resolved['path']))
                        aligner=ForcedAligner(str(aligner_model['path']))
                    else:
                        from faster_whisper import WhisperModel
                        model = WhisperModel(str(resolved['path']), device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
                progress(path, phase='transcribing', message=f'辨識片段 {index+1}/{len(ranges)}', total=len(ranges), completed=index)
                wav.setpos(round(start*16000))
                samples = np.frombuffer(wav.readframes(round((end-start)*16000)),dtype=np.int16).astype(np.float32)/32768
                if job['model']=='qwen3':
                    rows=qwen_transcribe_rows(model,aligner,samples,start,{'zh':'Chinese','en':'English','auto':None}[job['language']],job['prompts'])
                else:
                    segments,_ = model.transcribe(samples, language=None if job['language']=='auto' else job['language'],
                        beam_size=5, condition_on_previous_text=False, vad_filter=True, word_timestamps=True,
                        initial_prompt='、'.join(job['prompts']) or None)
                    rows = []
                    for s in segments:
                        rows.append({'start': s.start, 'end': s.end, 'text': s.text, 'avg_logprob': s.avg_logprob,
                                     'compression_ratio': s.compression_ratio,
                                     'words': [{'start': w.start+start, 'end': w.end+start, 'text': w.word, 'probability': w.probability} for w in s.words or []]})
                saved = {'start': start, 'end': end, 'segments': rows}
                atomic_json(checkpoint, saved)
                chunks.append(saved)
            progress(path, phase='transcribing', message=f'已完成片段 {index+1}/{len(ranges)}', total=len(ranges), completed=index+1)
    convert = OpenCC('s2twp').convert
    merged = []
    for index,chunk in enumerate(chunks):
        left = (chunks[index-1]['end']+chunk['start'])/2 if index else 0
        right = (chunk['end']+chunks[index+1]['start'])/2 if index+1<len(chunks) else duration
        for raw in chunk['segments']:
            start,end = raw['start']+chunk['start'],min(duration,raw['end']+chunk['start'])
            if not (left <= (start+end)/2 <= right) or not raw.get('display_text',raw['text']).strip(): continue
            reasons=list(raw.get('review_reasons',[]))
            if merged and start < merged[-1]['end']:
                if not reasons and raw['text']==merged[-1]['raw_text']: continue
                start=merged[-1]['end'];reasons.append('分段交界，建議回聽')
            if end<=start or not all(math.isfinite(v) for v in (start,end)):continue
            if raw['avg_logprob'] is not None and raw['avg_logprob'] < -1:reasons.append('辨識把握較低')
            if raw['compression_ratio'] is not None and raw['compression_ratio'] > 2.4:reasons.append('疑似重複文字')
            traditional = convert(raw.get('display_text',raw['text'])).strip()
            for old,new in [('隻要','只要'),('隻有','只有'),('隻是','只是'),('隻能','只能')]:traditional=traditional.replace(old,new)
            merged.append({'id':len(merged), 'start':start, 'end':end, 'raw_text':raw['text'],
                           'traditional_text':traditional,'edited_text':None,'reviewed':False,
                           'review_reasons':reasons,'words':raw['words']})
    if not merged:raise RuntimeError('未辨識到文字；請檢查音量、語言，或建立啟用降噪的新任務')
    if digest(source) != job['source_sha256']:raise RuntimeError('來源副本在處理期間變更，停止輸出')
    # Never overwrite an existing manually edited document on recovery.
    if not (path/'transcript.json').exists():
        atomic_json(path/'transcript.json',{'revision':1,'duration':duration,'segments':merged,'updated_at':now()})
    progress(path, phase='complete', message='逐字稿完成，可以開始校稿', total=len(ranges), completed=len(ranges))

if __name__ == '__main__':
    try:
        run(Store(sys.argv[1]), sys.argv[2])
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
