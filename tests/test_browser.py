"""Real Chromium regression tests for the user-facing review flow."""
import os
from pathlib import Path
import signal
import pytest
from playwright.sync_api import sync_playwright, expect
from transcript_local.manager import Manager
from transcript_local.service import healthy
from transcript_local.store import atomic_json, read_json
from test_service import supervised, wait_for


@pytest.fixture
def browser_job(store,wav,document):
    job=Manager(store).import_file(wav,model='small');job['status']='complete';store.save(job)
    atomic_json(store.job_dir(job['id'])/'transcript.json',document)
    (store.job_dir(job['id'])/'audio.wav').write_bytes(wav.read_bytes())
    return job


@pytest.fixture
def review_page(store,browser_job,supervised):
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        context=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1000})
        page=context.new_page();page.on('dialog',lambda d:d.accept())
        info=healthy(store)
        page.goto(f'http://127.0.0.1:{info["port"]}/#session={info["token"]}')
        page.get_by_role('button',name=browser_job['name'],exact=True).click()
        expect(page.locator('#editor')).to_be_visible()
        yield page
        browser.close()


def test_browser_three_edits_exports_and_reload(review_page,store,browser_job,tmp_path):
    page=review_page
    for revision in range(2,5):
        page.locator('#segments textarea').first.fill(f'第{revision}版最新內容')
        page.locator('#save').click()
        expect(page.locator('#save-state')).to_have_text(f'已儲存第 {revision} 版')
    for fmt in ('md','txt','srt','json'):
        with page.expect_download() as pending:
            page.locator('#exports').get_by_text(fmt.upper(),exact=True).click()
        download=pending.value
        assert '第4版' in download.suggested_filename
        path=tmp_path/download.suggested_filename;download.save_as(path)
        text=path.read_text()
        assert '第4版最新內容' in text
        assert '第3版最新內容' not in text
    page.reload()
    expect(page.locator('#segments textarea').first).to_have_value('第4版最新內容')
    if os.environ.get('IHT_SCREENSHOT'):
        page.screenshot(path=os.environ['IHT_SCREENSHOT'],full_page=True)

def test_browser_model_choices_explain_tradeoffs(review_page):
    page=review_page
    for value,description in [('medium','均衡選擇'),('small','模型較小'),('qwen3','時間對齊模型')]:
        page.locator('#model').select_option(value)
        expect(page.locator('#model-explanation')).to_contain_text(description)
    expect(page.locator('#model-explanation')).to_contain_text('尚未就緒')
    page.get_by_role('button',name='環境與模型').click()
    expect(page.locator('#models')).to_contain_text('Qwen3-ASR 1.7B')

@pytest.fixture
def empty_page(store,supervised):
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('dialog',lambda d:d.accept())
        info=healthy(store)
        page.goto(f'http://127.0.0.1:{info["port"]}/#session={info["token"]}')
        expect(page.locator('#jobs')).to_contain_text('第一份逐字稿')
        yield page
        browser.close()

def make_browser_job(store,wav,document,name,created):
    job={'id':store.new_id(),'name':name,'status':'running','model':'small','created_at':created,'suffix':'.wav'}
    path=store.job_dir(job['id']);path.mkdir();store.save(job)
    atomic_json(path/'transcript.json',document)
    (path/'audio.wav').write_bytes(wav.read_bytes())
    return job

def finish_browser_job(store,job):
    job['status']='complete';store.save(job)

def test_browser_auto_opens_first_completion_and_preserves_edits(empty_page,store,wav,document):
    page=empty_page
    first=make_browser_job(store,wav,document,'第一份.wav','2026-01-01T00:00:01')
    second=make_browser_job(store,wav,document,'第二份.wav','2026-01-01T00:00:02')
    expect(page.get_by_role('button',name='第一份.wav')).to_be_visible(timeout=10000)
    expect(page.get_by_role('button',name='第二份.wav')).to_be_visible()
    finish_browser_job(store,first)
    expect(page.locator('#recording-name')).to_have_text('第一份.wav',timeout=10000)
    expect(page.locator('.job.active')).to_contain_text('檢視中')
    page.locator('#segments textarea').first.fill('還沒儲存的內容')
    finish_browser_job(store,second)
    expect(page.locator('#jobs')).to_contain_text('已完成・點擊查看',timeout=10000)
    expect(page.locator('#recording-name')).to_have_text('第一份.wav')
    expect(page.locator('#segments textarea').first).to_have_value('還沒儲存的內容')
    page.get_by_role('button',name='第二份.wav').click()
    expect(page.locator('#recording-name')).to_have_text('第二份.wav')
    assert list((store.job_dir(first['id'])/'drafts').glob('*.json'))

def test_browser_background_completion_opens_oldest(empty_page,store,wav,document):
    page=empty_page
    first=make_browser_job(store,wav,document,'先加入.wav','2026-01-01T00:00:01')
    second=make_browser_job(store,wav,document,'後加入.wav','2026-01-01T00:00:02')
    expect(page.get_by_role('button',name='後加入.wav')).to_be_visible(timeout=10000)
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true})")
    finish_browser_job(store,second);finish_browser_job(store,first)
    page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator('#recording-name')).to_have_text('先加入.wav',timeout=10000)
    expect(page.locator('#jobs')).to_contain_text('已完成・點擊查看')

def test_browser_existing_completion_does_not_auto_open_on_reload(empty_page,store,wav,document):
    job=make_browser_job(store,wav,document,'舊稿.wav','2026-01-01T00:00:01');finish_browser_job(store,job)
    empty_page.reload()
    expect(empty_page.get_by_role('button',name='舊稿.wav')).to_be_visible()
    expect(empty_page.locator('#editor-empty')).to_be_visible()

def test_browser_manual_choice_wins_over_slow_auto_open(empty_page,store,wav,document):
    page=empty_page
    old=make_browser_job(store,wav,document,'手動選擇.wav','2026-01-01T00:00:01');finish_browser_job(store,old)
    page.reload();expect(page.get_by_role('button',name='手動選擇.wav')).to_be_visible()
    new=make_browser_job(store,wav,document,'新完成.wav','2026-01-01T00:00:02')
    expect(page.get_by_role('button',name='新完成.wav')).to_be_visible(timeout=10000)
    page.evaluate('''id=>{const original=window.fetch;window.autoFetchStarted=false;
      window.autoFetchGate=new Promise(resolve=>window.releaseAutoFetch=resolve);
      window.fetch=async (...args)=>{if(String(args[0])===`/api/jobs/${id}/transcript`){window.autoFetchStarted=true;await window.autoFetchGate;}return original(...args);};}''',new['id'])
    finish_browser_job(store,new)
    page.wait_for_function('() => window.autoFetchStarted',timeout=10000)
    page.get_by_role('button',name='手動選擇.wav').click()
    expect(page.locator('#recording-name')).to_have_text('手動選擇.wav')
    page.evaluate('window.releaseAutoFetch()')
    expect(page.locator('#recording-name')).to_have_text('手動選擇.wav')

def test_browser_failed_or_cancelled_jobs_do_not_auto_open(empty_page,store,wav,document):
    page=empty_page
    failed=make_browser_job(store,wav,document,'失敗.wav','2026-01-01T00:00:01')
    cancelled=make_browser_job(store,wav,document,'取消.wav','2026-01-01T00:00:02')
    expect(page.get_by_role('button',name='取消.wav')).to_be_visible(timeout=10000)
    failed['status']='failed';store.save(failed)
    cancelled['status']='cancelled';store.save(cancelled)
    expect(page.locator('#jobs')).to_contain_text('處理失敗',timeout=10000)
    expect(page.locator('#jobs')).to_contain_text('已停止')
    expect(page.locator('#editor-empty')).to_be_visible()


def test_browser_save_failure_preserves_text_and_retries(review_page):
    page=review_page
    def fail(route):route.fulfill(status=500,body='test save failure')
    page.route('**/transcript',lambda route:fail(route) if route.request.method=='PUT' else route.continue_())
    page.locator('#segments textarea').first.fill('不能遺失的修改')
    page.locator('#save').click()
    expect(page.locator('#toast')).to_contain_text('test save failure')
    expect(page.locator('#segments textarea').first).to_have_value('不能遺失的修改')
    expect(page.locator('#save')).to_be_enabled()
    page.unroute('**/transcript')
    page.locator('#save').click()
    expect(page.locator('#save-state')).to_have_text('已儲存第 2 版')


def test_browser_conflict_keeps_draft_and_requires_preview(review_page,store,browser_job):
    page=review_page
    second=page.context.new_page();second.on('dialog',lambda d:d.accept())
    second.goto(page.url)
    expect(second.locator('#segments textarea').first).to_be_visible()
    page.locator('#segments textarea').first.fill('第一個視窗的草稿')
    second.locator('#segments textarea').first.fill('第二個視窗已儲存')
    second.locator('#save').click();expect(second.locator('#save-state')).to_have_text('已儲存第 2 版')
    page.locator('#save').click();expect(page.locator('#toast')).to_contain_text('另一個視窗')
    expect(page.locator('#segments textarea').first).to_have_value('第一個視窗的草稿')
    page.reload()
    expect(page.locator('#draft-notice')).to_be_visible()
    page.get_by_role('button',name='預覽恢復').first.click()
    expect(page.locator('#draft-warning')).to_contain_text('其他版本')
    page.locator('#restore-draft').click()
    expect(page.locator('#segments textarea').first).to_have_value('第一個視窗的草稿')
    page.locator('#save').click();expect(page.locator('#save-state')).to_have_text('已儲存第 3 版')
    assert read_json(store.job_dir(browser_job['id'])/'transcript.json')['segments'][0]['edited_text']=='第一個視窗的草稿'
    second.close()


def test_browser_crash_restart_recovers_draft(review_page,store):
    page=review_page
    page.locator('#segments textarea').first.fill('重啟前的草稿')
    expect(page.locator('#save-state')).to_contain_text('草稿已備份')
    first=healthy(store);os.kill(first['pid'],signal.SIGKILL)
    info=wait_for(lambda:(value if (value:=healthy(store)) and value['instance_id']!=first['instance_id'] else None))
    page.goto(f'http://127.0.0.1:{info["port"]}/#session={info["token"]}')
    page.reload()  # The supervisor preserves the URL across a recovery.
    expect(page.locator('#draft-notice')).to_be_visible()
    page.get_by_role('button',name='預覽恢復').first.click();page.locator('#restore-draft').click()
    expect(page.locator('#segments textarea').first).to_have_value('重啟前的草稿')
    page.locator('#save').click();expect(page.locator('#save-state')).to_have_text('已儲存第 2 版')


def test_browser_restores_long_review_scroll(review_page,store,browser_job):
    page=review_page;path=store.job_dir(browser_job['id'])/'transcript.json'
    doc=read_json(path);template=doc['segments'][0]
    doc['segments']=[{**template,'id':i,'start':i*2,'end':i*2+1,'traditional_text':f'測試片段 {i}'} for i in range(45)]
    atomic_json(path,doc);page.reload()
    expect(page.locator('#segments textarea')).to_have_count(45)
    expect(page.locator('#segments textarea').first).to_be_visible()
    page.locator('#segments').evaluate('(node)=>{node.scrollTop=550;node.dispatchEvent(new Event("scroll"));}')
    wait_for(lambda:read_json(store.root/'preferences.json',{}).get('segment_position',0)>500)
    page.reload()
    expect(page.locator('#segments textarea')).to_have_count(45)
    wait_for(lambda:page.locator('#segments').evaluate('(node)=>node.scrollTop')>500)


def test_browser_current_version_only_checks(review_page):
    import json
    page=review_page;updates=[]
    page.on('request',lambda r:updates.append(r.url) if r.method=='POST' and r.url.endswith('/api/update') else None)
    page.route('**/api/update/check',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'status':'idle','available':True,'can_update':False,'check_status':'up_to_date','check_message':'已是最新版本。'})))
    page.locator('#update-button').click()
    expect(page.locator('#update-button')).to_have_text('✓ 已是最新')
    assert not updates
    expect(page.locator('#save')).to_be_disabled()
