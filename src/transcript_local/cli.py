import argparse
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
    parser=argparse.ArgumentParser(description='本機逐字稿與人工校閱；不需 API key。預設開啟網頁。')
    parser.add_argument('--data-dir',type=Path,help='指定獨立資料目錄（預設 Application Support）')
    sub=parser.add_subparsers(dest='command')
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
    token=secrets.token_urlsafe(32)
    sock=socket.socket();sock.bind(('127.0.0.1',getattr(args,'port',0)));sock.listen(128)
    url=f'http://127.0.0.1:{sock.getsockname()[1]}/#session={token}'
    print('本機網頁（請勿分享此工作階段連結）：'+url,flush=True)
    app=create_app(store,token)
    if not getattr(args,'no_browser',False):
        threading.Timer(1,lambda:webbrowser.open(url)).start()
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
    try:server.run(sockets=[sock])
    finally:sock.close()
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,ValueError) as e:
        print(str(e),file=sys.stderr);sys.exit(1)
