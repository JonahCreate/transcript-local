"""One isolated process per recording. Model inference never downloads weights."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import wave
from .store import Store, atomic_json, digest, read_json, now


def progress(path, **data):
    atomic_json(path/'progress.json', data)
    print(json.dumps(data, ensure_ascii=False), flush=True)


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
            end = min(start+300, duration)
            ranges.append((start,end))
            if end >= duration: break
            start = end-1
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        from faster_whisper import WhisperModel
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
                    model = WhisperModel(str(store.models/job['model']), device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
                progress(path, phase='transcribing', message=f'辨識片段 {index+1}/{len(ranges)}', total=len(ranges), completed=index)
                wav.setpos(round(start*16000))
                samples = np.frombuffer(wav.readframes(round((end-start)*16000)),dtype=np.int16).astype(np.float32)/32768
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
            if not (left <= (start+end)/2 <= right) or not raw['text'].strip(): continue
            reasons=[]
            if merged and start < merged[-1]['end']:
                if raw['text']==merged[-1]['raw_text']: continue
                start=merged[-1]['end'];reasons.append('分段交界，建議回聽')
            if end<=start or not all(math.isfinite(v) for v in (start,end)):continue
            if raw['avg_logprob'] < -1:reasons.append('辨識把握較低')
            if raw['compression_ratio'] > 2.4:reasons.append('疑似重複文字')
            traditional = convert(raw['text']).strip()
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
