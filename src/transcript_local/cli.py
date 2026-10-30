from http.client import HTTPException as HTTPReadError
import argparse
import os
import platform
import secrets
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from .store import Store, SUPPORTED, atomic_json, read_json
from .models import Models, available
from .manager import Manager
from .review import export, FORMAT_NAMES


def main(argv=None):
    parser=argparse.ArgumentParser(description='I Hate Transcription — Less typing. More living.')
    parser.add_argument('--data-dir',type=Path,help='指定獨立資料目錄（預設 Application Support）')
    sub=parser.add_subparsers(dest='command')
    sub.add_parser('menu',help='互動操作選單')
    sub.add_parser('open',help='開啟背景服務工作台')
    sub.add_parser('update',help='透過獨立管理程序安全更新')
    stop_parser=sub.add_parser('stop',help='停止背景服務')
    stop_parser.add_argument('--force',action='store_true')
    serve=sub.add_parser('serve',help='開啟本機網頁')
    serve.add_argument('--no-browser',action='store_true')
    serve.add_argument('--port',type=int,default=0,help='預設自動選擇可用連接埠')
    sub.add_parser('doctor',help='檢查環境與模型')
    models=sub.add_parser('download-model',help='明確下載模型')
    models.add_argument('name',choices=['medium','small'])
    trans=sub.add_parser('transcribe',help='轉錄檔案或資料夾，完成後退出')
    trans.add_argument('path',type=Path)
    trans.add_argument('--model',choices=['medium','small'],default='medium')
    trans.add_argument('--language',choices=['zh','en','auto'],default='zh')
    trans.add_argument('--enhance',action='store_true')
    trans.add_argument('--recursive',action='store_true')
    trans.add_argument('--force',action='store_true',help='建立新版本，保留所有舊任務與校稿')
    trans.add_argument('--output-dir',type=Path,help='另外匯出到此目錄，各任務獨立子資料夾')
    args=parser.parse_args(argv)
    store=Store(args.data_dir)
    if args.command in {None,'menu'}:
        from .menu import menu
        return menu(store)
    if args.command=='open':
        from .service import ensure
        ensure(store);print('已開啟工作台。');return 0
    if args.command=='update':
        from .service import ensure, request
        info=ensure(store,False);checked=request(info,'/api/update/check',{})
        if not checked.get('can_update'):print(checked.get('check_message','無法確認版本。'));return 0
        result=request(info,'/api/update',{})
        print(result.get('message') or result.get('check_message'));return 0
    if args.command=='stop':
        from .service import stop
        print(stop(store,args.force));return 0
    if args.command=='doctor':
        good=platform.system()=='Darwin' and platform.machine()=='arm64'
        print('正式支援平台：'+('符合' if good else '未驗證；正式支援 Apple Silicon Mac'))
        print('FFmpeg：'+(shutil.which('ffmpeg') or '未安裝，請執行 brew install ffmpeg'))
        print('資料位置：'+str(store.root))
        for name in ['medium','small']:print(f'{name}：'+('可離線使用' if available(store,name) else '尚未下載'))
        return 0 if shutil.which('ffmpeg') else 1
    if args.command=='download-model':
        models=Models(store);models.download(args.name)
        print('開始下載模型，可中斷後重試。',flush=True)
        models.thread.join()
        state=models.status()['download']
        print(state['error'] or '模型下載完成')
        return int(state['status']=='failed')
    if args.command=='transcribe':
        if not shutil.which('ffmpeg'):parser.error('找不到 FFmpeg，請執行 brew install ffmpeg')
        path=args.path.expanduser().resolve()
        if not path.exists():parser.error('找不到輸入路徑')
        files=[path] if path.is_file() else sorted(p for p in (path.rglob('*') if args.recursive else path.iterdir()) if p.is_file() and p.suffix.lower() in SUPPORTED)
        if not files:parser.error('資料夾中沒有支援的音訊或影片')
        manager=Manager(store);manager.start()
        ids=[];failures=0
        try:
            for source in files:
                try:
                    job=manager.import_file(source,model=args.model,language=args.language,enhance=args.enhance,force=args.force)
                    ids.append(job['id'])
                    if job['status'] in {'failed','cancelled','interrupted'}:manager.resume(job['id'])
                except Exception as e:print(f'{source.name}：{e}',file=sys.stderr);failures+=1
            previous={}
            while True:
                pending=False
                for job_id in dict.fromkeys(ids):
                    job=store.get(job_id);progress=read_json(store.job_dir(job_id)/'progress.json',{})
                    message=f'{job["name"]}：{job["status"]} {progress.get("message", "")}'
                    if previous.get(job_id)!=message:print(message,flush=True);previous[job_id]=message
                    pending |= job['status'] in {'queued','running','cancelling'}
                if not pending:break
                time.sleep(.5)
            for job_id in dict.fromkeys(ids):
                job=store.get(job_id)
                if job['status']!='complete':
                    print(job.get('error') or '任務未完成',file=sys.stderr);failures+=1;continue
                doc=read_json(store.job_dir(job_id)/'transcript.json')
                dest=(args.output_dir.expanduser().resolve()/job_id if args.output_dir else store.job_dir(job_id)/'exports')
                dest.mkdir(parents=True,exist_ok=True)
                # Do not overwrite previous exported edits; each export has a new revision directory.
                import uuid
                dest=dest/(f'revision-{doc["revision"]}-'+uuid.uuid4().hex[:8]);dest.mkdir()
                for fmt,name in FORMAT_NAMES.items():(dest/name).write_text(export(job,doc,fmt),encoding='utf-8')
                print('輸出：'+str(dest))
            return int(failures>0)
        except KeyboardInterrupt:
            print('\n已停止，完成片段已保存；再次執行可續跑。')
            return 130
        finally:manager.close()
    import uvicorn
    from .web import create_app
    from .service import healthy, busy_lease, runtime
    existing=healthy(store)
    if existing:
        if not getattr(args,'no_browser',False):webbrowser.open(f'http://127.0.0.1:{existing["port"]}/#session={existing["token"]}')
        print('服務已啟動；請使用 tr 選單管理。');return 0
    if busy_lease(store):raise RuntimeError('另一個舊服務仍在使用資料。請先儲存修改並停止原服務。')
    token=os.environ.get('IHT_SERVICE_TOKEN') if os.environ.get('IHT_MANAGED')=='1' else None
    token=token or secrets.token_urlsafe(32)
    instance=secrets.token_hex(16)
    port=int(os.environ.get('IHT_SERVICE_PORT','0')) if os.environ.get('IHT_MANAGED')=='1' else getattr(args,'port',0)
    sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);sock.bind(('127.0.0.1',port));sock.listen(128)
    url=f'http://127.0.0.1:{sock.getsockname()[1]}/#session={token}'
    info={'port':sock.getsockname()[1],'token':token,'instance_id':instance,'pid':os.getpid()}
    rt=runtime(store)
    def ready():
        atomic_json(rt/'service.json',info)
        if os.environ.get('IHT_MANAGED')!='1':print('本機網頁（請勿分享工作階段連結）：'+url,flush=True)
        if not getattr(args,'no_browser',False):webbrowser.open(url)
    def shutdown():
        atomic_json(rt/'exit-intent.json',{'instance_id':instance})
        server.should_exit=True
    app=create_app(store,token,on_ready=ready,on_stop=shutdown,instance_id=instance)
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
    try:server.run(sockets=[sock])
    finally:sock.close()
    return 0

def entrypoint():
    try:return main()
    except KeyboardInterrupt:return 0
    except (RuntimeError,ValueError,OSError,HTTPReadError) as exc:
        print('無法完成操作：'+str(exc),file=sys.stderr)
        return 1

if __name__=='__main__':sys.exit(entrypoint())
