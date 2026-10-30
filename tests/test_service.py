import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
from transcript_local.service import healthy, runtime, request, stop
from transcript_local.store import atomic_json, read_json
from transcript_local.supervisor import Supervisor, read
from transcript_local.install_commands import install


def wait_for(check, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=check()
        if result:return result
        time.sleep(.1)
    raise AssertionError('Timed out')

def test_update_never_deletes_current_incomplete_release(store,monkeypatch):
    rt=runtime(store);revision='a'*40;release=rt/'releases'/revision
    release.mkdir(parents=True);marker=release/'my-changes';marker.write_text('keep')
    atomic_json(rt/'config.json',{'source':str(Path.cwd())})
    atomic_json(rt/'current.json',{'project':str(release),'revision':revision+'-local'})
    supervisor=Supervisor(store.root)
    monkeypatch.setattr(supervisor,'command',lambda *a,**k:None)
    monkeypatch.setattr(subprocess,'check_output',lambda args,**kw:'https://github.com/JonahCreate/transcript-local.git\n' if 'remote' in args else revision+'\n')
    with pytest.raises(RuntimeError,match='保護'):
        supervisor.prepare()
    assert marker.read_text()=='keep'


@pytest.fixture
def supervised(store):
    rt=runtime(store)
    atomic_json(rt/'config.json',{'source':str(Path.cwd()),'data':str(store.root),'label':'test'})
    atomic_json(rt/'current.json',{'project':str(Path.cwd()),'python':sys.executable,'revision':'test'})
    script=Path(__file__).resolve().parents[1]/'src/transcript_local/supervisor.py'
    with (rt/'test-supervisor.log').open('ab') as log:
        proc=subprocess.Popen([sys.executable,str(script),str(store.root)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        wait_for(lambda:healthy(store));yield proc
    finally:
        if proc.poll() is None:
            try:stop(store,True)
            except Exception:proc.terminate()
        try:proc.wait(timeout=15)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()


def test_service_survives_controller_and_normal_stop(store,supervised):
    first=healthy(store);second=healthy(store)
    assert first['instance_id']==second['instance_id']
    result=request(first,'/api/jobs');assert result==[]
    assert '已停止' in stop(store)
    supervised.wait(timeout=10)
    assert not healthy(store)


def test_supervisor_recovers_crash_once(store,supervised):
    first=healthy(store)
    os.kill(first['pid'],signal.SIGKILL)
    second=wait_for(lambda:(value if (value:=healthy(store)) and value['instance_id']!=first['instance_id'] else None))
    assert read_json(runtime(store)/'recovery.json')['last_restart']>0
    os.kill(second['pid'],signal.SIGKILL)
    supervised.wait(timeout=15)
    assert not healthy(store)
    assert '再次異常' in read_json(runtime(store)/'update.json')['message']


def test_force_stop_uses_live_supervisor_not_stale_pid(store,supervised):
    assert '已停止' in stop(store,True)
    supervised.wait(timeout=10)
    atomic_json(runtime(store)/'service.json',{'pid':os.getpid(),'port':1,'token':'stale','instance_id':'stale'})
    assert '已停止' in stop(store,True)


def test_independent_update_rolls_back_candidate_failure(store,monkeypatch):
    rt=runtime(store);old={'python':'old','project':'old','revision':'old'};new={'python':'new','project':'new','revision':'new'}
    atomic_json(rt/'config.json',{'source':str(Path.cwd())});atomic_json(rt/'current.json',old)
    supervisor=Supervisor(store.root);calls=[]
    monkeypatch.setattr(supervisor,'prepare',lambda:new)
    monkeypatch.setattr(supervisor,'validate',lambda c:None)
    monkeypatch.setattr(supervisor,'backup',lambda:'backup')
    monkeypatch.setattr(supervisor,'terminate',lambda force=False:True)
    monkeypatch.setattr(supervisor,'launch',lambda c:calls.append(c) or c==old)
    monkeypatch.setattr(supervisor,'open_page',lambda:None)
    monkeypatch.setattr(supervisor,'health',lambda:None)
    supervisor.update()
    assert calls==[new,old]
    assert read_json(rt/'current.json')==old
    assert read_json(rt/'update.json')['status']=='rolled_back'


@pytest.mark.parametrize('failure',['prepare','validate'])
def test_failed_preparation_keeps_old_service(store,monkeypatch,failure):
    rt=runtime(store);old={'python':'old','project':'old','revision':'old'}
    atomic_json(rt/'config.json',{'source':str(Path.cwd())});atomic_json(rt/'current.json',old)
    supervisor=Supervisor(store.root);terminated=[]
    monkeypatch.setattr(supervisor,'prepare',lambda:{**old,'revision':'new'})
    monkeypatch.setattr(supervisor,'validate',lambda c:None)
    def fail(*args):raise RuntimeError('network/install/validation failure')
    monkeypatch.setattr(supervisor,failure,fail)
    monkeypatch.setattr(supervisor,'terminate',lambda **kw:terminated.append(True))
    monkeypatch.setattr(supervisor,'health',lambda:None)
    supervisor.update()
    assert not terminated
    assert read_json(rt/'current.json')==old
    assert read_json(rt/'update.json')['status']=='failed'


def test_command_install_idempotent_and_preserves_conflicts(tmp_path):
    bin_dir=tmp_path/'.local/bin';bin_dir.mkdir(parents=True)
    (bin_dir/'end').write_text('existing end command')
    assert install(Path.cwd(),tmp_path)==['end']
    assert install(Path.cwd(),tmp_path)==['end']
    assert (bin_dir/'end').read_text()=='existing end command'
    assert (tmp_path/'.zshrc').read_text().count('# IHT:')==1
    result=subprocess.run([str(bin_dir/'tr'),'a-z','A-Z'],input='hello',text=True,capture_output=True)
    assert result.stdout=='HELLO'


def test_noninteractive_menu_prints_help(store):
    result=subprocess.run([sys.executable,'-m','transcript_local.cli','--data-dir',str(store.root),'menu'],stdin=subprocess.DEVNULL,capture_output=True,text=True)
    assert result.returncode==0
    assert 'I Hate Transcription' in result.stdout
    assert '互動式終端機' in result.stdout


def test_actual_candidate_validation_and_live_failure_rolls_back(store,tmp_path,monkeypatch):
    import shlex
    rt=runtime(store)
    previous={'project':str(Path.cwd()),'python':sys.executable,'revision':'old'}
    atomic_json(rt/'config.json',{'source':str(Path.cwd())});atomic_json(rt/'current.json',previous)
    wrapper=tmp_path/'candidate-python'
    wrapper.write_text('#!/bin/sh\ncase "$TRANSCRIPT_LOCAL_DATA" in *validation-*) exec '+shlex.quote(sys.executable)+' "$@";; *) exit 9;; esac\n')
    wrapper.chmod(0o755)
    candidate={'project':str(Path.cwd()),'python':str(wrapper),'revision':'candidate'}
    supervisor=Supervisor(store.root)
    assert supervisor.launch(previous)
    monkeypatch.setattr(supervisor,'prepare',lambda:candidate)
    monkeypatch.setattr(supervisor,'open_page',lambda:None)
    try:
        supervisor.update()
        assert read_json(rt/'update.json')['status']=='rolled_back'
        assert healthy(store)
        assert read_json(rt/'current.json')==previous
    finally:supervisor.terminate(force=True)


def test_current_version_update_does_not_restart_service(store,monkeypatch):
    rt=runtime(store);current={'revision':'same','python':'python','project':'project'}
    atomic_json(rt/'config.json',{'source':str(Path.cwd())});atomic_json(rt/'current.json',current)
    supervisor=Supervisor(store.root);actions=[]
    monkeypatch.setattr(supervisor,'prepare',lambda:current)
    monkeypatch.setattr(supervisor,'terminate',lambda **kw:actions.append('stop'))
    monkeypatch.setattr(supervisor,'unlock',lambda:actions.append('unlock'))
    supervisor.update()
    assert actions==['unlock']
    assert read_json(rt/'update.json')['status']=='complete'


def test_menu_accepts_terminal_arrow_and_number(store):
    import fcntl,pty,select,struct,termios
    master,slave=pty.openpty()
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,100,0,0))
    env=dict(os.environ,TERM='xterm-256color')
    proc=subprocess.Popen([sys.executable,'-m','transcript_local.cli','--data-dir',str(store.root),'menu'],stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
    os.close(slave)
    try:
        output=b'';deadline=time.monotonic()+5
        while '開啟網頁工作台'.encode() not in output and time.monotonic()<deadline:
            if select.select([master],[],[],.2)[0]:output+=os.read(master,10000)
        assert 'I HATE TRANSCRIPTION' in output.decode(errors='replace')
        os.write(master,b'\x1b[B');time.sleep(.15)
        assert proc.poll() is None
        os.write(master,b'5');deadline=time.monotonic()+5
        while proc.poll() is None and time.monotonic()<deadline:
            if select.select([master],[],[],.1)[0]:
                try:output+=os.read(master,10000)
                except OSError:break
        proc.wait(timeout=2)
        assert proc.returncode==0
    finally:
        if proc.poll() is None:proc.kill();proc.wait(timeout=5)
        os.close(master)


def test_partial_health_response_is_unhealthy(store,monkeypatch):
    from http.client import IncompleteRead
    import transcript_local.service as client
    import transcript_local.supervisor as daemon
    rt=runtime(store)
    atomic_json(rt/'service.json',{'pid':1234,'port':1234,'token':'test','instance_id':'test'})
    atomic_json(rt/'config.json',{'source':str(Path.cwd())})
    def partial(*args,**kwargs):raise IncompleteRead(b'',100)
    monkeypatch.setattr(client,'request',partial)
    assert client.healthy(store) is None
    supervisor=daemon.Supervisor(store.root)
    supervisor.child=SimpleNamespace(pid=1234,poll=lambda:None)
    monkeypatch.setattr(daemon,'api',partial)
    assert supervisor.health() is None
