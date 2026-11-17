from contextlib import asynccontextmanager
import hashlib
import os
import re
import json
from pathlib import Path
import secrets
import shutil
import threading
import subprocess
import time
from typing import Literal
from urllib.parse import quote
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .states import LABELS, actions
from .store import Store, atomic_json, clean_name, read_json, now
from . import __version__
from .manager import Manager
from .models import Models
from .updater import Updater
from .review import FORMAT_NAMES, export, learned, preview, save_edits
from .assistant import Assistant, advice

ExportFormat = Literal['md','txt','srt','json']

class FolderExport(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)
    format: ExportFormat = 'md'

    destination: str = Field(max_length=100)
    revisions: dict[str, int] = Field(default_factory=dict, max_length=1000)


class ExportDestination(BaseModel):
    destination: str = Field(max_length=100)


def choose_export_folder():
    script = """
    try
        return POSIX path of (choose folder with prompt "選擇逐字稿匯出資料夾")
    on error errorText number errorNumber
        if errorNumber is -128 then return ""
        error errorText number errorNumber
    end try
    """
    result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True,
                            text=True, check=True, timeout=300)
    return result.stdout.rstrip('\r\n') or None


def write_export(folder, filename, content):
    stem, suffix = os.path.splitext(filename)
    number = 1
    while True:
        name = filename if number == 1 else f'{stem} ({number}){suffix}'
        target = folder / name
        try:
            stream = target.open('x', encoding='utf-8')
        except FileExistsError:
            number += 1
            continue
        try:
            with stream:
                stream.write(content)
        except OSError:
            target.unlink(missing_ok=True)
            raise
        return name


class ModelChoice(BaseModel):
    name: Literal['medium','small','qwen3']

class Edit(BaseModel):
    id: int
    text: str = Field(max_length=50000)
    reviewed: bool

class Edits(BaseModel):
    revision: int
    segments: list[Edit] = Field(max_length=50000)

class Draft(BaseModel):
    window_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    base_revision: int
    segments: list[Edit] = Field(max_length=50000)
    position: float = Field(default=0,ge=0,le=10000000)
    segment_position: float = Field(default=0,ge=0,le=10000000)

class Preferences(BaseModel):
    job_id: str | None = None
    position: float = Field(default=0,ge=0,le=10000000)
    segment_position: float = Field(default=0,ge=0,le=10000000)

class Rule(BaseModel):
    source: str = Field(alias='from', min_length=1, max_length=200)
    to: str = Field(max_length=200)

class Glossary(BaseModel):
    prompts: list[str] = Field(max_length=100)
    rules: list[Rule] = Field(max_length=100)

    @model_validator(mode='after')
    def bounded(self):
        if sum(map(len,self.prompts))>1500:raise ValueError('辨識提示總長度不可超過 1500 字')
        return self

class Apply(BaseModel):
    revision: int
    glossary_hash: str
    ids: list[int]


def create_app(store=None, token=None, manager=None, restart=None, on_ready=None, on_stop=None, instance_id=None):
    store=store or Store()
    token=token or secrets.token_urlsafe(32)
    manager=manager or Manager(store)
    models=Models(store)
    updater=Updater(manager,restart,models)
    assistant=Assistant()
    started=time.time()
    def stale():
        # In the development channel the page is read fresh from disk while this process keeps old code.
        return any(p.stat().st_mtime>started for p in Path(__file__).parent.glob('*.py'))
    instance_id=instance_id or secrets.token_hex(16)
    cookie_name='iht_session_'+hashlib.sha256(str(store.root).encode()).hexdigest()[:12]
    edit_lock=threading.RLock()
    static=Path(__file__).parent/'static'

    @asynccontextmanager
    async def lifespan(app):
        manager.start()
        app.state.ready=True
        if on_ready:on_ready()
        try:yield
        finally:
            app.state.ready=False
            manager.close()

    app=FastAPI(title='I Hate Transcription', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    app.state.token=token
    app.state.manager=manager

    @app.middleware('http')
    async def headers(request, call_next):
        if request.url.path.startswith('/api/') and request.method not in {'GET','HEAD'}:
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return Response('不接受其他網站的請求',status_code=403)
            if request.headers.get('x-local-request')!='1':
                return Response('缺少本機請求標記',status_code=403)
        if request.url.path.startswith('/api/') and request.method not in {'GET','HEAD'} and manager.updating and request.url.path not in {'/api/update','/api/update/unlock'} and not request.url.path.endswith('/draft'):
            return Response('程式正在更新，請稍候',status_code=409)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
        return response

    def auth(request:Request):
        supplied=request.headers.get('authorization','').removeprefix('Bearer ') or request.cookies.get(cookie_name,'')
        if not secrets.compare_digest(supplied,token):raise HTTPException(401,'工作階段已結束，請使用啟動時的連結重新開啟')

    def job(job_id):
        try:return store.get(job_id)
        except FileNotFoundError as e:raise HTTPException(404,str(e))
        except ValueError as e:raise HTTPException(400,str(e))

    def doc(job_id):
        job(job_id)
        data=read_json(store.job_dir(job_id)/'transcript.json')
        if data is None:raise HTTPException(409,'逐字稿尚未完成')
        return data

    @app.exception_handler(ValueError)
    async def bad_input(request, exc):
        return Response(str(exc),status_code=409)

    @app.get('/')
    def index():return FileResponse(static/'index.html')

    app.mount('/static',StaticFiles(directory=static),name='static')

    @app.post('/api/session',dependencies=[Depends(auth)])
    def session():
        response=Response(status_code=204)
        response.set_cookie(cookie_name,token,httponly=True,samesite='strict')
        return response

    @app.get('/api/health',dependencies=[Depends(auth)])
    def health():
        return {'version':__version__,'instance_id':instance_id,'data_path':str(store.root),'ready':getattr(app.state,'ready',False),'revision':os.environ.get('IHT_REVISION','installed')}

    @app.post('/api/service/stop',dependencies=[Depends(auth)],status_code=202)
    def stop_service():
        if not on_stop:raise ValueError('此服務無法透過網頁停止')
        threading.Timer(.3,on_stop).start()
        return {'message':'正在停止服務，完成片段會保留。'}

    @app.post('/api/update/unlock',dependencies=[Depends(auth)])
    def unlock_update(payload:dict):
        secret=read_json(store.root/'runtime/supervisor.json',{}).get('secret','')
        if not secret or not secrets.compare_digest(secret,str(payload.get('secret',''))):raise HTTPException(403,'僅管理程序可解鎖')
        manager.updating=False
        return {'ok':True}

    @app.get('/api/status',dependencies=[Depends(auth)])
    def status():
        disk=shutil.disk_usage(store.root)
        return {'data_path':str(store.root),'used_bytes':store.usage(),'free_bytes':disk.free,
                'trash_bytes':store.usage(store.root/'trash') if (store.root/'trash').exists() else 0,
                'ffmpeg':bool(shutil.which('ffmpeg')),'advice':advice(),'stale':stale(),**models.status()}

    @app.get('/api/update',dependencies=[Depends(auth)])
    def update_status():return updater.status()

    @app.post('/api/update/check',dependencies=[Depends(auth)])
    def check_update():
        if os.environ.get('IHT_REVISION') == 'development':
            return {'check_status': 'check_failed', 'can_update': False,
                    'message': '請在 tr 選單切回正式版，再檢查更新。'}
        return updater.check()

    def writable():
        if manager.updating:raise ValueError('程式正在更新，請稍候')

    @app.post('/api/update',dependencies=[Depends(auth)],status_code=202)
    def update_start(response:Response):
        if os.environ.get('IHT_REVISION') == 'development':
            raise HTTPException(409, '請切回正式版再更新。')
        checked=updater.check()
        with edit_lock:started=updater.start(checked)
        if not started:response.status_code=200
        return updater.status()

    @app.post('/api/models/download',dependencies=[Depends(auth)],status_code=202)
    def download(choice:ModelChoice):
        with manager.lock:
            writable()
            models.download(choice.name)
        return models.status()

    @app.get('/api/jobs',dependencies=[Depends(auth)])
    def jobs():
        return [dict(j,progress=read_json(store.job_dir(j['id'])/'progress.json',{}),label=LABELS.get(j['status'],j['status']),actions=actions(j['status'])) for j in store.list()]

    @app.post('/api/jobs',dependencies=[Depends(auth)],status_code=202)
    def upload(file:UploadFile=File(...), model:Literal['medium','small','qwen3']=Form('medium'),
               language:Literal['zh','en','auto']=Form('zh'), enhance:bool=Form(False), force:bool=Form(False)):
        name=clean_name(file.filename or '')
        settings=manager.settings(model,language,enhance)
        staging=store.root/('upload-'+store.new_id())
        try:
            size=0
            with staging.open('wb') as dest:
                while block:=file.file.read(1024*1024):
                    size+=len(block)
                    if size>20*1024**3:raise HTTPException(413,'單檔上限為 20 GB')
                    if shutil.disk_usage(store.root).free<len(block)+50*1024**2:raise HTTPException(507,'磁碟空間不足')
                    dest.write(block)
            if not size:raise ValueError('檔案是空的')
            return manager.commit_source(staging,name,settings,force)
        finally:
            staging.unlink(missing_ok=True)
            file.file.close()

    @app.post('/api/jobs/{job_id}/cancel',dependencies=[Depends(auth)])
    def cancel(job_id:str):
        job(job_id);return manager.cancel(job_id)

    @app.post('/api/jobs/{job_id}/resume',dependencies=[Depends(auth)])
    def resume(job_id:str):
        job(job_id);return manager.resume(job_id)

    @app.delete('/api/jobs/{job_id}',dependencies=[Depends(auth)])
    def clear(job_id:str):
        job(job_id);manager.delete(job_id)
        return {'message':'已移至本機資料目錄的 trash 資料夾；可手動還原或清空'}

    @app.get('/api/jobs/{job_id}/transcript',dependencies=[Depends(auth)])
    def transcript(job_id:str):return doc(job_id)

    @app.put('/api/jobs/{job_id}/transcript',dependencies=[Depends(auth)])
    def edit(job_id:str, payload:Edits):
        job(job_id)
        with edit_lock:
            writable()
            return save_edits(store.job_dir(job_id)/'transcript.json',payload.model_dump())

    @app.get('/api/jobs/{job_id}/drafts',dependencies=[Depends(auth)])
    def drafts(job_id:str):
        job(job_id)
        return sorted([read_json(p) for p in (store.job_dir(job_id)/'drafts').glob('*.json')],key=lambda x:x['saved_at'],reverse=True)

    @app.put('/api/jobs/{job_id}/draft',dependencies=[Depends(auth)])
    def draft_save(job_id:str,payload:Draft):
        with edit_lock:
            data=doc(job_id)
            ids={r['id'] for r in data['segments']}
            if any(r.id not in ids for r in payload.segments):raise ValueError('草稿包含不存在的片段')
            value={**payload.model_dump(),'saved_at':now()}
            atomic_json(store.job_dir(job_id)/'drafts'/(payload.window_id+'.json'),value)
            return value

    @app.delete('/api/jobs/{job_id}/drafts/{window_id}',dependencies=[Depends(auth)])
    def draft_clear(job_id:str,window_id:str):
        job(job_id)
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}',window_id):raise ValueError('無效的草稿編號')
        with edit_lock:(store.job_dir(job_id)/'drafts'/(window_id+'.json')).unlink(missing_ok=True)
        return {'ok':True}

    @app.get('/api/preferences',dependencies=[Depends(auth)])
    def preferences():return read_json(store.root/'preferences.json',{})

    @app.put('/api/preferences',dependencies=[Depends(auth)])
    def preferences_save(payload:Preferences):
        if payload.job_id:job(payload.job_id)
        with edit_lock:
            writable()
            atomic_json(store.root/'preferences.json',payload.model_dump())
        return payload.model_dump()

    @app.get('/api/jobs/{job_id}/audio',dependencies=[Depends(auth)])
    def audio(job_id:str):
        job(job_id)
        path=store.job_dir(job_id)/'audio.wav'
        if not path.exists():raise HTTPException(404,'回聽音訊尚未準備完成')
        return FileResponse(path,media_type='audio/wav')

    def export_filename(current_job, current_doc, fmt):
        source_name = Path(current_job['name']).stem.replace(':', '：')
        source_name = source_name.encode()[:180].decode('utf-8', errors='ignore').rstrip(' .')
        return f'{source_name}－第{current_doc["revision"]}版－{FORMAT_NAMES[fmt]}'

    @app.get('/api/jobs/{job_id}/export/{fmt}',dependencies=[Depends(auth)])
    def download_export(job_id: str, fmt: ExportFormat, revision: int | None = None):
        current_job = job(job_id)
        current_doc = doc(job_id)
        if revision is not None and current_doc['revision'] != revision:
            raise ValueError('稿件版本已變更，請重新載入後匯出。')
        filename = export_filename(current_job, current_doc, fmt)
        return Response(export(current_job, current_doc, fmt),
                        media_type='application/json' if fmt=='json' else 'text/plain; charset=utf-8',
                        headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename)})

    export_settings = store.root / 'export-folder.json'
    export_choices = {}
    picker_lock = threading.Lock()

    def export_destination(key):
        path = export_choices.get(key)
        if not path or not Path(path).is_dir():
            raise HTTPException(409, '匯出資料夾已無法使用，請更改位置。')
        return Path(path)

    def folder_choice(path, *, remembered=False):
        key = secrets.token_urlsafe(24)
        if len(export_choices) >= 256:
            export_choices.pop(next(iter(export_choices)), None)
        export_choices[key] = path
        return {'destination': key, 'path': path, 'remembered': remembered}

    @app.get('/api/export-folder', dependencies=[Depends(auth)])
    def default_export_folder():
        path = read_json(export_settings, {}).get('path')
        return folder_choice(path, remembered=True) if path else None

    @app.post('/api/export-folder/choose', dependencies=[Depends(auth)])
    def pick_export_folder():
        if not picker_lock.acquire(blocking=False):
            raise HTTPException(409, '資料夾選擇視窗已經開啟。')
        try:
            path = choose_export_folder()
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(503, '無法開啟 macOS 資料夾選擇視窗。') from exc
        finally:
            picker_lock.release()
        if path is None:
            return None
        return folder_choice(path)

    @app.put('/api/export-folder', dependencies=[Depends(auth)])
    def remember_export_folder(payload: ExportDestination):
        folder = export_destination(payload.destination)
        atomic_json(export_settings, {'path': str(folder)})
        return {**payload.model_dump(), 'path': str(folder), 'remembered': True}

    @app.post('/api/export-folder/open', dependencies=[Depends(auth)])
    def open_export_folder(payload: ExportDestination):
        folder = export_destination(payload.destination)
        try:
            subprocess.run(['/usr/bin/open', str(folder)], check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(503, '無法在 Finder 開啟匯出資料夾。') from exc
        return {'opened': True}

    @app.post('/api/jobs/export-to-folder', dependencies=[Depends(auth)])
    def export_to_folder(payload: FolderExport):
        folder = export_destination(payload.destination)
        saved, failed = [], []
        for job_id in dict.fromkeys(payload.ids):
            name = job_id
            try:
                current_job = job(job_id)
                name = current_job['name']
                if current_job['status'] != 'complete':
                    raise HTTPException(409, '任務尚未完成。')
                current_doc = doc(job_id)
                expected = payload.revisions.get(job_id)
                if expected is not None and expected != current_doc['revision']:
                    raise HTTPException(409, '稿件版本已變更，請重新載入後匯出。')
                filename = export_filename(current_job, current_doc, payload.format)
                saved.append(write_export(folder, filename, export(current_job, current_doc, payload.format)))
            except HTTPException as exc:
                failed.append({'id': job_id, 'name': name, 'reason': exc.detail})
            except OSError as exc:
                reason = '磁碟空間不足，請釋放空間或更改位置。' if exc.errno == 28 else '無法寫入，請檢查權限或更改位置。'
                failed.append({'id': job_id, 'name': name, 'reason': reason})
        return {'saved': saved, 'failed': failed, 'path': str(folder)}

    @app.get('/api/glossary',dependencies=[Depends(auth)])
    def glossary():return store.glossary()

    @app.put('/api/glossary',dependencies=[Depends(auth)])
    def glossary_save(payload:Glossary):
        with edit_lock:
            writable()
            value=payload.model_dump(by_alias=True)
            atomic_json(store.root/'glossary.json',value)
            return value

    @app.get('/api/glossary/suggestions',dependencies=[Depends(auth)])
    def glossary_suggestions():
        docs=[d for d in (read_json(store.job_dir(j['id'])/'transcript.json') for j in store.list()) if d]
        return learned(docs,store.glossary()['rules'])

    @app.post('/api/jobs/{job_id}/ai',dependencies=[Depends(auth)],status_code=202)
    def ai_start(job_id:str):
        writable()
        return assistant.start(job_id,doc(job_id),store.glossary())

    @app.get('/api/ai',dependencies=[Depends(auth)])
    def ai_status():return assistant.status()

    @app.post('/api/ai/cancel',dependencies=[Depends(auth)])
    def ai_cancel():
        assistant.cancel();return assistant.status()

    def glossary_hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

    @app.post('/api/jobs/{job_id}/preview',dependencies=[Depends(auth)])
    def replacements(job_id:str):
        with edit_lock:
            data=doc(job_id);g=store.glossary()
            return {'revision':data['revision'],'glossary_hash':glossary_hash(g),'changes':preview(data,g['rules'])}

    @app.post('/api/jobs/{job_id}/apply',dependencies=[Depends(auth)])
    def apply(job_id:str, payload:Apply):
        with edit_lock:
            writable()
            data=doc(job_id);g=store.glossary()
            if data['revision']!=payload.revision or glossary_hash(g)!=payload.glossary_hash:raise ValueError('文字或詞彙表已變動，請重新預覽')
            changes={r['id']:r for r in preview(data,g['rules'])}
            if any(i not in changes for i in payload.ids):raise ValueError('預覽已失效')
            return save_edits(store.job_dir(job_id)/'transcript.json',{'revision':data['revision'],'segments':[
                {'id':i,'text':changes[i]['after'],'reviewed':next(r['reviewed'] for r in data['segments'] if r['id']==i)} for i in dict.fromkeys(payload.ids)]})
    return app
