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
    atomic_json(store.root/'export-folder.json', {'path': str(tmp_path)})
    page.reload()
    expect(page.locator('#export-version')).to_have_text(str(tmp_path))
    for fmt in ('md','txt','srt','json'):
        if fmt=='md':
            page.locator('#export-md').click()
        else:
            page.locator('#export-formats').press('Enter')
            page.locator(f'#export-format-options [data-format="{fmt}"]').click()
        expect(page.locator('#export-dialog-status')).to_have_text('已匯出 1 份。')
        path, = tmp_path.glob(f'*.{fmt}')
        assert '第4版' in path.name
        assert '第4版最新內容' in path.read_text()
        assert '第3版最新內容' not in path.read_text()
        page.locator('#export-dialog-close').click()
    page.reload()
    expect(page.locator('#segments textarea').first).to_have_value('第4版最新內容')
    if os.environ.get('IHT_SCREENSHOT'):
        page.screenshot(path=os.environ['IHT_SCREENSHOT'],full_page=True)

def test_browser_model_choices_explain_tradeoffs(review_page):
    page=review_page
    page.locator('#new-recording').click()
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
    # Review mode scrolls the page itself; the transcript has no inner scroll box.
    page.evaluate('()=>window.scrollTo(0,1500)')
    wait_for(lambda:read_json(store.root/'preferences.json',{}).get('position',0)>1400)
    page.reload()
    expect(page.locator('#segments textarea')).to_have_count(45)
    wait_for(lambda:page.evaluate('()=>window.scrollY')>1400)


def test_browser_current_version_only_checks(review_page):
    import json
    page=review_page;updates=[]
    page.on('request',lambda r:updates.append(r.url) if r.method=='POST' and r.url.endswith('/api/update') else None)
    page.route('**/api/update/check',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'status':'idle','available':True,'can_update':False,'check_status':'up_to_date','check_message':'已是最新版本。'})))
    page.locator('#update-button').click()
    expect(page.locator('#update-button')).to_have_text('✓ 已是最新')
    assert not updates
    expect(page.locator('#save')).to_be_disabled()

def test_browser_offers_repair_for_unknown_installed_revision(review_page):
    import json
    page=review_page;updates=[]
    page.on('request',lambda r:updates.append(r.url) if r.method=='POST' and r.url.endswith('/api/update') else None)
    page.route('**/api/update/check',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'status':'idle','available':True,'can_update':True,'check_status':'repair_available','check_message':'無法確認目前版本，可安裝官方最新版本修復。'})))
    page.route('**/api/update',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'status':'requested','message':'準備修復'})))
    page.locator('#update-button').click()
    expect(page.locator('#update-button')).to_have_text('更新中…')
    assert len(updates)==1


def test_browser_review_marker_filters_plays_and_saves(review_page):
    page = review_page
    expect(page.locator('#pending-count')).to_contain_text('1 段')
    page.locator('#only-pending').check()
    expect(page.locator('.segment:visible')).to_have_count(1)
    page.locator('.review-reason').click()
    page.wait_for_function("() => document.querySelector('#audio').currentTime > 0")
    page.locator('.segment:visible textarea').fill('已回聽修正')
    page.locator('.segment:visible input[type=checkbox]').check()
    expect(page.locator('#pending-count')).to_contain_text('0 段')
    page.locator('#save').click()
    expect(page.locator('#save-state')).to_have_text('已儲存第 2 版')
    page.locator('#only-pending').uncheck()
    expect(page.locator('.segment textarea').first).to_have_value('已回聽修正')


def test_browser_batch_select_and_export(review_page, store, browser_job, wav, document, tmp_path):
    atomic_json(store.root/'export-folder.json', {'path': str(tmp_path)})
    page = review_page
    page.reload()
    expect(page.locator('#export-version')).to_have_text(str(tmp_path))
    original_name = browser_job['name']
    for name, created in [(original_name, '2000-01-03'), ('中間.wav', '2000-01-02'), ('最後.wav', '2000-01-01')]:
        job = make_browser_job(store, wav, document, name, created)
        finish_browser_job(store, job)
    pending = make_browser_job(store, wav, document, '新完成.wav', '2000-01-04')
    expect(page.get_by_role('button', name='新完成.wav')).to_be_visible(timeout=10000)
    page.get_by_role('button', name='選擇', exact=True).click()
    boxes = page.locator('#jobs input[type=checkbox]')
    expect(boxes).to_have_count(4)
    expect(page.get_by_role('button', name='新完成.wav')).to_be_disabled()
    boxes.nth(0).click()
    boxes.nth(2).click(modifiers=['Shift'])
    expect(page.locator('#selection-count')).to_have_text('已選 3 份')
    boxes.nth(2).click(modifiers=['Meta'])
    boxes.nth(3).click(modifiers=['Meta'])
    expect(page.locator('#selection-count')).to_have_text('已選 3 份')
    finish_browser_job(store, pending)
    expect(boxes).to_have_count(5, timeout=10000)
    expect(page.get_by_role('checkbox', name='選擇 新完成.wav')).not_to_be_checked()
    expect(page.locator('#selection-count')).to_have_text('已選 3 份')
    expect(page.locator('#recording-name')).to_have_text(original_name)

    page.locator('#segments textarea').first.fill('批次匯出最新校稿')
    expect(page.locator('#batch-md')).to_have_text('儲存並匯出 Markdown')
    page.locator('#batch-md').click()
    page.locator('#export-dialog-run').click()
    expect(page.locator('#export-dialog-status')).to_have_text('已匯出 3 份。')
    page.locator('#export-dialog-close').click()
    expect(page.locator('#save-state')).to_have_text('已儲存第 2 版')

    def check_files(suffix):
        entries = list(tmp_path.glob(f'*.{suffix}'))
        assert len(entries) == 3
        assert sum(original_name.rsplit('.', 1)[0] in path.name for path in entries) == 2
        assert any('批次匯出最新校稿' in path.read_text() for path in entries)

    check_files('md')
    page.locator('#batch-formats').hover()
    expect(page.get_by_role('button', name='TXT', exact=True)).to_be_visible()
    page.locator('#selection-count').hover()
    expect(page.locator('#batch-format-options')).to_be_hidden()
    page.locator('#batch-formats').press('Enter')
    expect(page.get_by_role('button', name='MD', exact=True)).to_be_focused()
    page.locator('#batch-format-options [data-format=srt]').click()
    expect(page.locator('#export-dialog-status')).to_have_text('已匯出 3 份。')
    check_files('srt')
    assert not list(tmp_path.glob('*.zip'))
    page.locator('#export-dialog-close').click()
    page.keyboard.press('Escape')
    expect(boxes).to_have_count(0)


def test_export_bar_stays_visible_at_half_width(review_page, store, browser_job):
    page = review_page
    page.set_viewport_size({'width': 700, 'height': 700})
    path = store.job_dir(browser_job['id'])/'transcript.json'
    transcript = read_json(path)
    transcript['segments'] = [{**transcript['segments'][0], 'id': index} for index in range(30)]
    atomic_json(path, transcript)
    page.reload()
    last = page.locator('#segments textarea').last
    expect(last).to_be_visible()
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    bar = page.locator('.export-bar').bounding_box()
    assert bar['y'] + bar['height'] == pytest.approx(700, abs=1)
    area = last.bounding_box()
    assert area['y'] >= 0 and area['y'] + area['height'] <= bar['y']


def test_export_cancel_and_save_conflict_preserve_edits(review_page, store, tmp_path):
    page = review_page
    page.route('**/api/export-folder/choose', lambda route: route.fulfill(body='null', content_type='application/json'))
    with page.expect_response('**/api/export-folder/choose'):
        page.locator('#export-md').click()
    expect(page.locator('#export-dialog')).to_be_hidden()
    page.unroute('**/api/export-folder/choose')
    atomic_json(store.root/'export-folder.json', {'path': str(tmp_path)})
    page.reload()
    expect(page.locator('#export-version')).to_have_text(str(tmp_path))
    page.locator('#segments textarea').first.fill('需要保留的校稿')
    page.route('**/api/jobs/*/transcript', lambda route: (
        route.fulfill(status=409, body='稿件已由其他視窗更新')
        if route.request.method=='PUT' else route.continue_()
    ))
    page.locator('#export-md').click()
    page.locator('#export-dialog-run').click()
    expect(page.locator('#export-dialog-status')).to_have_text('稿件已由其他視窗更新')
    assert not list(tmp_path.glob('*.md'))
    page.locator('#export-dialog-close').click()
    expect(page.locator('#segments textarea').first).to_have_value('需要保留的校稿')


def test_review_mode_keeps_task_and_transcript_on_screen(review_page, store, wav, document, browser_job):
    page = review_page
    for index in range(8):  # newer tasks push the open one to the bottom of the list
        finish_browser_job(store, make_browser_job(store, wav, document, f'較新的{index}.wav', f'2099-01-01T00:00:0{index}'))
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.reload()
    expect(page.locator('#recording-name')).to_have_text(browser_job['name'])
    active = page.locator('.job.active').bounding_box()
    assert 0 <= active['y'] and active['y'] + active['height'] <= 900
    expect(page.locator('#workspace .page-title')).to_be_hidden()
    expect(page.locator('#drop-zone')).to_be_hidden()
    assert page.locator('#segments textarea').first.bounding_box()['height'] < 70
    page.locator('#new-recording').click()
    expect(page.locator('#drop-zone')).to_be_visible()
    expect(page.locator('#new-recording')).to_have_attribute('aria-expanded', 'true')


def test_review_mode_half_width_collapses_task_list(review_page, store, wav, document, browser_job):
    page = review_page
    other = make_browser_job(store, wav, document, '另一份.wav', '2026-01-01T00:00:09')
    finish_browser_job(store, other)
    page.set_viewport_size({'width': 720, 'height': 900})
    page.reload()
    expect(page.locator('#recording-name')).to_have_text(browser_job['name'])
    expect(page.get_by_role('button', name='另一份.wav')).to_be_hidden()
    assert page.locator('#segments textarea').first.bounding_box()['y'] < 900
    page.locator('#toggle-jobs').click()
    page.get_by_role('button', name='另一份.wav').click()
    expect(page.locator('#recording-name')).to_have_text('另一份.wav')


def test_playback_marks_current_segment(review_page):
    page = review_page
    page.locator('#segments .time').first.click()
    expect(page.locator('.segment.now')).to_have_count(1)
    expect(page.locator('.segment').first).to_have_class('segment now')
    expect(page.locator('#now-playing')).to_contain_text('00:00')


def test_review_word_playback_search_and_shortcuts(review_page):
    page = review_page
    first = page.locator('#segments textarea').first
    first.click(modifiers=['Alt'])  # Option-click plays from that word
    page.keyboard.press('Escape')   # Esc pauses
    assert page.evaluate("document.getElementById('audio').paused")
    expect(page.locator('.segment.now .karaoke mark')).to_have_count(1)
    page.locator('#find').fill('你好')
    expect(page.locator('.segment:not([hidden])')).to_have_count(1)
    expect(page.locator('#find-count')).to_have_text('符合 1 段')
    page.locator('#find').fill('')
    first.focus()
    page.keyboard.press('Control+Enter')  # confirm and move on
    expect(page.locator('.segment').first).to_have_attribute('data-pending', 'false')
    assert page.evaluate("document.activeElement.closest('.segment').dataset.id") == '1'
    page.keyboard.press('Control+s')
    expect(page.locator('#save-state')).to_have_text('已儲存第 2 版')


def test_palette_progress_and_learned_glossary(review_page, store, browser_job):
    page = review_page
    path = store.job_dir(browser_job['id'])/'transcript.json'
    transcript = read_json(path)
    transcript['segments'][1].update(raw_text='供钞', traditional_text='供鈔')  # the same mistake twice
    atomic_json(path, transcript)
    page.reload()
    expect(page.locator('#review-count')).to_have_text('已確認 0／2 段')
    assert page.evaluate('document.documentElement.dataset.theme') == 'paper'
    page.locator('#theme-button').click()  # one click steps to the next palette
    assert page.evaluate('document.documentElement.dataset.theme') == 'ink'
    page.locator('#theme-button').hover()
    page.get_by_role('menuitemradio', name='茶霧').click()
    page.reload()
    assert page.evaluate('document.documentElement.dataset.theme') == 'tea'
    expect(page.locator('#segments textarea')).to_have_count(2)  # wait for the reload before filling
    for area in page.locator('#segments textarea').all(): area.fill('共槽')
    page.locator('#save').click()
    expect(page.locator('#save-state')).to_have_text('已儲存第 2 版')
    page.locator('[data-view=glossary]').click()
    page.get_by_role('button', name='加入詞彙表').click()
    expect(page.locator('#rules')).to_have_value('供鈔 → 共槽')
    expect(page.locator('#prompts')).to_have_value('共槽')
    expect(page.locator('#learned')).to_contain_text('目前還沒有')
