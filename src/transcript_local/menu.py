from http.client import HTTPException as HTTPReadError
import curses
import sys
import time
from .service import ensure, healthy, request, stop, runtime, busy_lease
from .store import read_json, ACTIVE

LOGO = [r'  ___ _   _ _____', r' |_ _| |_| |_   _|', r'  | ||  _  | | |', r' |___|_| |_| |_|', '  I HATE TRANSCRIPTION.', '  Less typing. More living.']
OPTIONS = ['開啟網頁工作台', '查看轉錄進度', '更新程式', '停止背景服務', '離開選單']


def choice(key, selected):
    if key == curses.KEY_UP: return (selected - 1) % len(OPTIONS), False
    if key == curses.KEY_DOWN: return (selected + 1) % len(OPTIONS), False
    if ord('1') <= key <= ord('5'): return key - ord('1'), True
    return selected, key in (10, 13, curses.KEY_ENTER)


def read_key(screen):
    key=screen.getch()
    if key!=27:return key
    # Accept both normal and application-cursor escape sequences.
    screen.timeout(50)
    first=screen.getch()
    if first in (ord('['),ord('O')):
        final=screen.getch()
        key={ord('A'):curses.KEY_UP,ord('B'):curses.KEY_DOWN}.get(final,27)
    screen.timeout(1000)
    return key


def menu(store):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('I Hate Transcription — Less typing. More living.\n請在互動式終端機輸入 tr 使用選單。\n直接操作：transcript-local open / stop [--force] / doctor')
        return 0
    try: return curses.wrapper(_screen, store)
    except KeyboardInterrupt: return 0
    except curses.error:
        print("目前終端機不支援互動選單；可使用 iht open 或 iht stop。",file=sys.stderr)
        return 1


def _screen(screen, store):
    curses.curs_set(0)
    if curses.has_colors():
        curses.start_color(); curses.use_default_colors(); curses.init_pair(1, curses.COLOR_RED, -1)
    screen.keypad(True); screen.timeout(1000)
    selected = 0; message = ''; last = 0; info = None; jobs = []
    def put(y, text, attr=0):
        height, width = screen.getmaxyx()
        if y < height - 1:
            try: screen.addnstr(y, 2, text, max(1, width - 4), attr)
            except curses.error: pass
    def confirm(text):
        put(15, text + ' [y/N]'); screen.refresh(); screen.timeout(-1)
        key = screen.getch(); screen.timeout(1000)
        return key in (ord('y'), ord('Y'))
    while True:
        if time.monotonic() - last > 2:
            info = healthy(store)
            try: jobs = request(info, '/api/jobs') if info else []
            except (OSError, ValueError, HTTPReadError): jobs = []
            last = time.monotonic()
        screen.erase()
        for i, line in enumerate(LOGO): put(i, line, curses.color_pair(1) if curses.has_colors() else curses.A_BOLD)
        active = sum(j['status'] in ACTIVE for j in jobs)
        state = '背景服務執行中' if info else '服務尚未啟動'
        put(7, f'● {state} · {active} 個任務處理中')
        update = read_json(runtime(store) / 'update.json', {})
        if update.get('status') in {'requested','preparing','validating','switching'}: state = '更新進行中';put(7,'● 更新進行中 · '+update.get('message',''))
        for i, option in enumerate(OPTIONS):
            text = '等待任務完成後更新' if i == 2 and active else option
            if i==0 and not info and busy_lease(store):text='服務失去回應 · 檢查修復說明'
            put(9 + i, f'{"❯" if i == selected else " "} {i + 1}  {text}', curses.A_REVERSE if i == selected else 0)
        put(16, message[:300]); put(18, '↑↓ 選擇 · Enter 確認 · 數字快捷 · Esc 離開')
        screen.refresh(); key = read_key(screen)
        if key in (27, ord('q')): return 0
        selected, activate = choice(key, selected)
        if not activate: continue
        if selected == 4: return 0
        try:
            if selected == 0:
                put(16, '正在準備工作台…'); screen.refresh(); ensure(store); message = '已開啟工作台。關閉此選單不會停止背景轉錄。'
            elif selected == 1:
                progress(screen,store)
            elif selected == 2:
                if active: message = '請先等待任務完成或停止任務。'; continue
                if confirm('確認所有網頁的修改都已儲存，開始更新？'):
                    info = ensure(store, False); request(info, '/api/update', {}); message = '更新已開始；可到網頁查看進度。'
            elif selected == 3:
                if confirm(f'停止背景服務？目前有 {active} 個處理中任務，已完成片段會保留。'):
                    try: message = stop(store)
                    except RuntimeError as exc:
                        if confirm(str(exc) + ' 確定強制停止？'): message = stop(store, True)
                        else: message = str(exc)
            last = 0
        except (RuntimeError, OSError, ValueError, HTTPReadError) as exc:
            message = str(exc)


def progress(screen,store):
    offset=0
    labels={'queued':'排隊中','running':'處理中','complete':'可校稿','failed':'失敗','cancelled':'已停止','interrupted':'可續跑','cancelling':'停止中'}
    while True:
        info=healthy(store)
        try:jobs=request(info,'/api/jobs') if info else []
        except (OSError,ValueError,HTTPReadError):jobs=[]
        height,width=screen.getmaxyx();screen.erase()
        lines=['IHT · 轉錄任務進度','Esc 回選單 · ↑↓ 捲動 · 每秒更新','']
        if not info:lines.append('服務尚未啟動；回選單開啟工作台。')
        elif not jobs:lines.append('目前沒有任務。')
        for job in jobs:
            p=job.get('progress',{})
            count=f" · {p.get('completed',0)}/{p['total']} 片段" if p.get('total') else ''
            lines.extend([job['name'],f"  {labels.get(job['status'],job['status'])}{count}", '  '+str(job.get('error') or p.get('message') or '')[:200],''])
        for i,line in enumerate(lines[offset:offset+height-1]):
            try:screen.addnstr(i,1,line,max(1,width-2))
            except curses.error:pass
        screen.refresh();key=read_key(screen)
        if key in (27,ord('q')):return
        if key==curses.KEY_DOWN:offset=min(max(0,len(lines)-height+1),offset+1)
        if key==curses.KEY_UP:offset=max(0,offset-1)
