import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import shutil
from pathlib import Path
from .store import ACTIVE, atomic_json, clean_name, digest, now, read_json
from .models import available

class Manager:
    def __init__(self, store):
        self.store=store
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.proc=None
        self.current=None
        self.thread=None
        self.lease=None

    def start(self):
        self.lease=(self.store.root/'instance.lock').open('a')
        try:fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close();self.lease=None
            raise RuntimeError('已有另一個 transcript-local 正在使用這個資料目錄，請先關閉它')
        for job in self.store.list():
            if job['status'] in ACTIVE:
                job['status']='interrupted';job['error']='上次程式已結束，按「續跑」接續已完成片段'
                self.store.save(job)
        self.thread=threading.Thread(target=self._loop,daemon=True)
        self.thread.start()

    def settings(self, model, language, enhance):
        if not available(self.store,model):raise ValueError('請先下載所選模型')
        if language not in {'zh','en','auto'}:raise ValueError('不支援的語言設定')
        return {'model':model,'model_revision':read_json(self.store.models/model/'installed.json')['revision'],
                'language':language,'enhance':enhance,'prompts':self.store.glossary()['prompts'],'pipeline_version':1}

    def commit_source(self, staging, name, settings, force=False):
        name=clean_name(name)
        checksum=digest(staging)
        signature=hashlib.sha256((checksum+json.dumps(settings,sort_keys=True)).encode()).hexdigest()
        with self.lock:
            if not force:
                for existing in self.store.list():
                    if existing['signature']==signature:
                        Path(staging).unlink(missing_ok=True)
                        return existing
            job_id=self.store.new_id();path=self.store.job_dir(job_id);path.mkdir()
            suffix=Path(name).suffix.lower()
            Path(staging).replace(path/('source'+suffix))
            job={'id':job_id,'name':name,'suffix':suffix,'source_sha256':checksum,'signature':signature,
                 **settings,'status':'queued','created_at':now(),'error':None}
            self.store.save(job)
            return job

    def import_file(self, source, **options):
        source=Path(source).expanduser().resolve()
        if not source.is_file():raise ValueError('找不到檔案：'+str(source))
        name=clean_name(source.name)
        settings=self.settings(options.get('model','medium'),options.get('language','zh'),options.get('enhance',False))
        staging=self.store.root/('import-'+self.store.new_id())
        try:
            shutil.copyfile(source,staging)
            return self.commit_source(staging,name,settings,options.get('force',False))
        finally:staging.unlink(missing_ok=True)

    def resume(self, job_id):
        with self.lock:
            job=self.store.get(job_id)
            if job['status'] not in {'failed','cancelled','interrupted'}:raise ValueError('這個任務無須續跑')
            if not available(self.store,job['model']):raise ValueError('請先下載模型')
            if read_json(self.store.models/job['model']/'installed.json')['revision']!=job['model_revision']:
                raise ValueError('模型版本已變更，請保留原稿並建立新任務')
            job['status']='queued';job['error']=None;self.store.save(job)
            return job

    def cancel(self, job_id):
        with self.lock:
            job=self.store.get(job_id)
            if job['status'] not in ACTIVE:raise ValueError('任務目前未在執行')
            job['status']='cancelling' if self.current==job_id else 'cancelled'
            self.store.save(job)
            if self.current==job_id and self.proc and self.proc.poll() is None:
                self._terminate(self.proc)
            return job

    @staticmethod
    def _terminate(proc):
        try:
            os.killpg(proc.pid,signal.SIGTERM)
            proc.wait(timeout=5)
        except ProcessLookupError:pass
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait()

    def _loop(self):
        while not self.stop.wait(.3):
            with self.lock:
                waiting=sorted((j for j in self.store.list() if j['status']=='queued'),key=lambda j:j['created_at'])
                if not waiting:continue
                job=waiting[0];self.current=job['id'];path=self.store.job_dir(job['id'])
                job['status']='running';job['error']=None;self.store.save(job)
                try:
                    log=(path/'worker.log').open('ab')
                    env=dict(os.environ,HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
                    self.proc=subprocess.Popen([sys.executable,'-m','transcript_local.worker',str(self.store.root),job['id']],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
                except Exception as exc:
                    job['status']='failed';job['error']=str(exc);self.store.save(job);self.current=None
                    if 'log' in locals():log.close()
                    continue
            code=self.proc.wait()
            log.close()
            with self.lock:
                job=self.store.get(job['id'])
                if self.stop.is_set():job['status']='interrupted'
                elif job['status']=='cancelling':job['status']='cancelled'
                elif code==0 and (path/'transcript.json').exists():job['status']='complete'
                else:
                    job['status']='failed'
                    with (path/'worker.log').open('rb') as stream:
                        stream.seek(max(0,stream.seek(0,2)-1600));job['error']=stream.read().decode('utf-8',errors='replace')
                self.store.save(job);self.current=None;self.proc=None

    def delete(self, job_id):
        with self.lock:
            job=self.store.get(job_id)
            if job['status'] in ACTIVE:raise ValueError('請先取消任務，再清除資料')
            # Move to a local recoverable trash; do not permanently purge recordings.
            trash=self.store.root/'trash';trash.mkdir(exist_ok=True)
            self.store.job_dir(job_id).rename(trash/(job_id+'-'+str(time.time_ns())))

    def close(self):
        self.stop.set()
        with self.lock:
            if self.proc and self.proc.poll() is None:self._terminate(self.proc)
        if self.thread:self.thread.join(timeout=10)
        if self.lease:
            fcntl.flock(self.lease,fcntl.LOCK_UN);self.lease.close();self.lease=None
