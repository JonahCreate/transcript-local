'use strict';
const $ = id => document.getElementById(id);
const state = {files: [], jobs: [], current: null, doc: null, dirty: false, status: null, busy: false, preview: null, authenticated: false, jobsKey: '', changed:new Set(), recoveredDraftIds:new Set(), updating:false, stopped:false, saving:false, opening:false, checking:false};
const jobStatus = new Map();
let jobsInitialized=false, jobRequest=0, appliedJobRequest=0, openRequest=0, openingAutomatic=false, pendingCompletions=[];
const windowId=sessionStorage.getItem('iht-window')||crypto.randomUUID();sessionStorage.setItem('iht-window',windowId);
let draftTimer, draftQueue=Promise.resolve(), positionTimer;
const statusLabels = {queued:'排隊中',running:'處理中',cancelling:'停止中',cancelled:'已停止',interrupted:'可續跑',failed:'處理失敗',complete:'可校稿'};
const modelInfo={medium:{title:'Whisper Medium',description:'一般中文錄音的均衡選擇；不知道選哪個時可先使用。速度與資源需求居中。'},small:{title:'Whisper Small',description:'模型較小，適合短錄音或希望較快取得初稿；可能需要較多校稿。'},qwen3:{title:'Qwen3-ASR 1.7B',description:'適合想嘗試另一種中文辨識結果，或 Whisper 辨識不理想的錄音。需額外下載時間對齊模型；處理時間與資源需求視電腦和錄音而定。'}};
function el(tag, text, cls) {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;}
function notify(message) {$('toast').textContent = message; $('toast').hidden = false; clearTimeout(notify.timer); notify.timer = setTimeout(() => $('toast').hidden = true, 6500);}
function bytes(n) {return n >= 1024**3 ? (n/1024**3).toFixed(1)+' GB' : (n/1024**2).toFixed(0)+' MB';}
function time(n) {return `${Math.floor(n/60).toString().padStart(2,'0')}:${Math.floor(n%60).toString().padStart(2,'0')}`;}
async function api(path, options = {}) {
  const headers = {'X-Local-Request':'1',...options.headers};
  if (options.body && !(options.body instanceof FormData)) {headers['Content-Type']='application/json'; options.body=JSON.stringify(options.body);}
  const response = await fetch('/api'+path, {...options,headers});
  if (!response.ok) {let message=await response.text(); try {const parsed=JSON.parse(message); message=typeof parsed.detail==='string'?parsed.detail:JSON.stringify(parsed.detail);} catch {} throw new Error(message || `操作失敗 (${response.status})`);}
  if (response.status === 204) return null;
  return response.json();
}
function protect(fn) {return async (...args) => {try {await fn(...args);} catch (e) {notify(e.message);}};}
function changeView(name) {document.querySelectorAll('.view').forEach(n => n.hidden=n.id!==name); document.querySelectorAll('.nav').forEach(n => n.classList.toggle('active',n.dataset.view===name));}
document.querySelectorAll('[data-view]').forEach(n => n.addEventListener('click',()=>changeView(n.dataset.view)));
$('setup-shortcut').onclick=$('get-started').onclick=()=>changeView('setup');
function setFiles(files) {state.files=Array.from(files); $('selected-files').replaceChildren(...state.files.map(f=>el('li',`${f.name} · ${bytes(f.size)}`))); updateSubmit();}
$('files').onchange=e=>setFiles(e.target.files);
$('drop-zone').ondragover=e=>{e.preventDefault();$('drop-zone').classList.add('dragover');};
$('drop-zone').ondragleave=()=>$('drop-zone').classList.remove('dragover');
$('drop-zone').ondrop=e=>{e.preventDefault();$('drop-zone').classList.remove('dragover');setFiles(e.dataTransfer.files);};
$('model').onchange=updateSubmit;
function updateSubmit() {const selected=state.status?.models.find(m=>m.name===$('model').value);const active=state.status?.download.name===selected?.name&&state.status?.download.status==='downloading';$('model-explanation').textContent=modelInfo[$('model').value].description+'　'+(selected?.ready?'已下載，可離線使用。':active?'下載中。':selected?'尚未就緒：缺少'+selected.missing.join('、')+'，請至「環境與模型」下載。':''); $('submit').disabled=!selected?.ready||!state.status?.ffmpeg||!state.files.length||state.busy||state.updating||state.stopped;}
$('submit').onclick=protect(async()=>{
  state.busy=true;updateSubmit();let count=0;
  try {
    for(const file of state.files) {
      $('submit').textContent=`傳入本機服務 ${count+1}/${state.files.length}…`;
      const form=new FormData();form.append('file',file);form.append('model',$('model').value);form.append('language',$('language').value);form.append('enhance',$('enhance').checked);form.append('force',$('force').checked);
      try {const job=await api('/jobs',{method:'POST',body:form});if(!jobStatus.has(job.id))jobStatus.set(job.id,job.status==='complete'?'queued':job.status);count++;} catch(e) {notify(`${file.name}：${e.message}`);}
    }
    if(count){notify($('force').checked ? `已建立 ${count} 個新版本。` : `已加入 ${count} 個檔案；相同內容與設定的任務會沿用。`);setFiles([]);$('files').value='';}
  } finally {state.busy=false;$('submit').textContent='加入轉錄佇列 →';updateSubmit();await refreshJobs();}
});
async function refreshStatus() {
  state.status=await api('/status');const s=state.status;
  $('setup-notice').hidden=s.models.some(m=>m.ready)&&s.ffmpeg;
  $('data-path').textContent=s.data_path;$('used-space').textContent=bytes(s.used_bytes);$('free-space').textContent=bytes(s.free_bytes);$('trash-space').textContent=bytes(s.trash_bytes);$('ffmpeg-status').textContent=s.ffmpeg?'已安裝':'缺少：brew install ffmpeg';
  $('models').replaceChildren(...s.models.map(m=>{
    const card=el('section',undefined,'panel model-card');card.append(el('h3',modelInfo[m.name].title),el('span',m.ready?'已準備完成 · 可離線使用':`完整模型約 ${bytes(m.estimated_bytes)}，已下載部分不重複下載`,'model-size'),el('p',modelInfo[m.name].description));
    const active=s.download.name===m.name;card.append(el('p',m.ready?(m.source==='cache'?'已下載 · 使用既有 Hugging Face 快取':'已下載 · 儲存在本工具模型資料夾'):active&&s.download.status==='downloading'?'下載中，請保持程式開啟…':active&&s.download.status==='failed'?`下載失敗：${s.download.error}`:`尚未就緒：缺少${m.missing.join('、')}`,'model-status'));
    const b=el('button',m.ready?'已準備完成':active&&s.download.status==='failed'?'重試下載':'下載模型','button '+(m.ready?'subtle':'primary'));b.disabled=m.ready||s.download.status==='downloading';
    b.onclick=protect(async()=>{if(!confirm(`準備 ${modelInfo[m.name].title}（完整模型約 ${bytes(m.estimated_bytes)}，已下載部分不重複下載）\n辨識模型：${m.path}${m.aligner_path?'\n時間對齊模型：'+m.aligner_path:''}\n\n模型由 Hugging Face 取得。錄音不會隨下載上傳。`))return;await api('/models/download',{method:'POST',body:{name:m.name}});await refreshStatus();});card.append(b);return card;
  }));updateSubmit();
}
async function refreshJobs() {
  const request=++jobRequest;const jobs=await api('/jobs');if(request<appliedJobRequest)return;appliedJobRequest=request;
  const completed=jobs.filter(j=>j.status==='complete'&&['queued','running'].includes(jobStatus.get(j.id))).sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id));
  for(const j of jobs)jobStatus.set(j.id,j.status);
  if(jobsInitialized&&completed.length){pendingCompletions.push(...completed.map(j=>j.id));if(state.current)notify(completed.length===1?`「${completed[0].name}」已完成，可從任務記錄點擊查看。`:`${completed.length} 份逐字稿已完成，可從任務記錄點擊查看。`);}
  if(!state.stopped)$('service-notice').hidden=true;state.jobs=jobs;$('job-count').textContent=jobs.length; const key=JSON.stringify([state.current,jobs]); if(key===state.jobsKey){scheduleCompleted();return;} state.jobsKey=key;
  if (!jobs.length){$('jobs').replaceChildren(el('p','第一份逐字稿，從一段錄音開始。','empty-small'));return;}
  $('jobs').replaceChildren(...jobs.map(j=>{
    const item=el('article',undefined,'job'+(j.id===state.current?' active':''));const title=el('button',j.name,'job-title');title.onclick=protect(()=>openJob(j));const label=j.status==='complete'?(j.id===state.current?'檢視中':'已完成・點擊查看'):(statusLabels[j.status]||j.status);item.append(title,el('div',`${label} · ${j.model}`,'job-meta'));
    if(j.progress?.message&&j.status!=='complete')item.append(el('p',j.progress.message,'job-meta'));
    if(j.status==='running'&&j.progress?.total){const p=el('progress');p.max=j.progress.total;p.value=j.progress.completed||0;p.setAttribute('aria-label','辨識片段進度');item.append(p);}
    if(j.error){item.append(el('div',j.error_hint||j.error,'job-error'));if(j.error_hint){const details=el('details',undefined,'original');details.append(el('summary','查看錯誤詳情'),el('pre',j.error));item.append(details);}}
    const buttons=el('div',undefined,'job-buttons');
    if(['queued','running'].includes(j.status)){const b=el('button','停止','button subtle');b.onclick=protect(async()=>{await api(`/jobs/${j.id}/cancel`,{method:'POST'});await refreshJobs();});buttons.append(b);}
    if(['failed','cancelled','interrupted'].includes(j.status)){const b=el('button','續跑','button subtle');b.onclick=protect(async()=>{await api(`/jobs/${j.id}/resume`,{method:'POST'});await refreshJobs();});buttons.append(b);}
    if(!['queued','running','cancelling'].includes(j.status)){const b=el('button','清除','button subtle');b.onclick=protect(async()=>{if(!confirm(`將「${j.name}」的副本、進度與校稿移至本機 trash？\n原始錄音不會刪除。`))return;if(j.id===state.current&&state.dirty&&!confirm('尚有未儲存修改，仍要清除嗎？'))return;await api(`/jobs/${j.id}`,{method:'DELETE'});if(j.id===state.current){state.current=null;state.doc=null;state.dirty=false;$('editor').hidden=true;$('editor-empty').hidden=false;}await refreshJobs();await refreshStatus();});buttons.append(b);}
    item.append(buttons);return item;
  }));scheduleCompleted();
}
function scheduleCompleted(){
  if(!jobsInitialized||!pendingCompletions.length||document.hidden)return;
  if(state.current){pendingCompletions=[];return;}
  if(state.opening||state.saving||state.dirty)return;
  const job=state.jobs.find(j=>j.id===pendingCompletions[0]&&j.status==='complete');
  if(!job){pendingCompletions.shift();scheduleCompleted();return;}
  pendingCompletions=[];
  protect(()=>openJob(job,0,0,true))();
}
function markDirty(id){if(id!==undefined)state.changed.add(id);clearTimeout(draftTimer);draftTimer=setTimeout(()=>protect(flushDraft)(),1000);state.dirty=true;$('save').disabled=false;$('save-state').textContent='尚未儲存';$('export-version').textContent='有未儲存修改；儲存後才能下載最新版。';}
async function openJob(j,position=0,segmentPosition=0,automatic=false){
  if(state.saving||(state.opening&&(automatic||!openingAutomatic))){if(!automatic)notify('正在儲存或載入，請稍候。');return;}
  if(automatic&&(state.current||state.dirty))return;
  if(j.status!=='complete'){notify(j.error||j.progress?.message||'任務完成後即可校稿。');return;}
  const request=++openRequest;state.opening=true;openingAutomatic=automatic;
  try {
  if(state.dirty){await flushDraft();if(!confirm('修改已備份為草稿，仍要切換稿件嗎？'))return;}
  const doc=await api(`/jobs/${j.id}/transcript`);if(request!==openRequest||(automatic&&(state.current||state.dirty)))return;
  state.current=j.id;state.doc=doc;state.dirty=false;state.changed.clear();state.recoveredDraftIds.clear();
  await api('/preferences',{method:'PUT',body:{job_id:j.id,position,segment_position:segmentPosition}});
  if(request!==openRequest)return;
  $('recording-name').textContent=j.name;$('save-state').textContent=`已載入第 ${doc.revision} 版`;$('export-version').textContent=`下載第 ${doc.revision} 版；檔名會包含錄音名稱與版本。`;$('save').disabled=true;$('audio').src=`/api/jobs/${j.id}/audio`;
  renderSegments();renderExports();await loadDrafts();await refreshJobs();$('editor-empty').hidden=true;$('editor').hidden=false;window.scrollTo(0,position);$('segments').scrollTop=segmentPosition;
  }finally{if(request===openRequest){state.opening=false;openingAutomatic=false;scheduleCompleted();}}
}
function renderSegments(){
  $('segments').replaceChildren(...state.doc.segments.map(r=>{
    const row=el('article',undefined,'segment');row.dataset.pending=String(!!r.review_reasons.length&&!r.reviewed);
    const meta=el('div',undefined,'segment-meta');const play=el('button',`${time(r.start)} ↗`,'time');play.setAttribute('aria-label',`回聽 ${time(r.start)} 到 ${time(r.end)}`);
    play.onclick=protect(async()=>{const audio=$('audio');audio.currentTime=r.start;audio.dataset.end=r.end;await audio.play();});meta.append(play);
    const label=el('label',undefined,'check');const check=el('input');check.type='checkbox';check.checked=r.reviewed;check.onchange=()=>{const current=state.doc.segments.find(x=>x.id===r.id);current.reviewed=check.checked;markDirty(r.id);row.dataset.pending=String(!!current.review_reasons.length&&!current.reviewed);filterSegments();};label.append(check,el('span','已確認'));meta.append(label);
    const body=el('div');const input=el('textarea');input.value=r.edited_text??r.traditional_text;input.setAttribute('aria-label',`${time(r.start)} 的逐字稿`);input.oninput=()=>{state.doc.segments.find(x=>x.id===r.id).edited_text=input.value;markDirty(r.id);};body.append(input);
    if(r.review_reasons.length)body.append(el('div',r.review_reasons.join('；'),'review-reason'));
    const details=el('details',undefined,'original');details.append(el('summary','查看原始辨識'),el('div',r.raw_text));body.append(details);row.append(meta,body);return row;
  }));filterSegments();
}
function filterSegments(){document.querySelectorAll('.segment').forEach(n=>n.hidden=$('only-pending').checked&&n.dataset.pending!=='true');}
$('only-pending').onchange=filterSegments;
$('audio').ontimeupdate=()=>{const audio=$('audio');if(audio.dataset.end&&audio.currentTime>=Number(audio.dataset.end)){audio.pause();delete audio.dataset.end;}};
$('audio').onerror=()=>notify('回聽音訊無法載入，請確認任務資料仍完整。');
async function save(){
  if(!state.current||!state.dirty)return;
  state.saving=true;
  const fields=[...$('segments').querySelectorAll('textarea, input')];fields.forEach(n=>n.disabled=true);$('save').disabled=true;
  try {
    await flushDraft();
    const doc=await api(`/jobs/${state.current}/transcript`,{method:'PUT',body:{revision:state.doc.revision,segments:state.doc.segments.map(r=>({id:r.id,text:r.edited_text??r.traditional_text,reviewed:r.reviewed}))}});
    state.doc=doc;state.dirty=false;state.changed.clear();
    const position=window.scrollY,segmentPosition=$('segments').scrollTop;renderSegments();renderExports();window.scrollTo(0,position);$('segments').scrollTop=segmentPosition;
    $('save-state').textContent=`已儲存第 ${doc.revision} 版`;$('export-version').textContent=`下載第 ${doc.revision} 版；檔名包含錄音名稱與版本。`;
    notify(`第 ${doc.revision} 版已儲存在本機。`);
    for(const id of new Set([windowId,...state.recoveredDraftIds])) {
      try {await api(`/jobs/${state.current}/drafts/${id}`,{method:'DELETE'});} catch {notify('正式稿已儲存；舊草稿稍後可手動清除。');}
    }
    state.recoveredDraftIds.clear();await loadDrafts();
  } finally {fields.forEach(n=>n.disabled=false);$('save').disabled=!state.dirty;state.saving=false;}
}
$('save').onclick=protect(save);
$('save-glossary').onclick=protect(async()=>{
  const prompts=$('prompts').value.split('\n').map(s=>s.trim()).filter(Boolean);const rules=[];
  for(const line of $('rules').value.split('\n').filter(s=>s.trim())){const pair=line.split('→');if(pair.length!==2||!pair[0].trim())throw new Error('替換規則請使用「誤辨詞 → 正確詞」，每行一筆。');rules.push({from:pair[0].trim(),to:pair[1].trim()});}
  await api('/glossary',{method:'PUT',body:{prompts,rules}});notify('詞彙表已儲存。提示詞會用於之後建立的任務。');
});
$('preview').onclick=protect(async()=>{
  if(state.dirty){notify('請先儲存修改，再預覽替換。');return;}
  const p=await api(`/jobs/${state.current}/preview`,{method:'POST'});state.preview=p;
  $('preview-list').replaceChildren(...p.changes.map(c=>{const row=el('div',undefined,'preview-row');const label=el('label',undefined,'check');const check=el('input');check.type='checkbox';check.checked=true;check.dataset.id=c.id;label.append(check,el('span',time(c.start)));row.append(label,el('div',c.before,'preview-before'),el('div',c.after,'preview-after'));return row;}));
  if(!p.changes.length)$('preview-list').append(el('p','目前沒有符合替換規則的文字。你可以到「我的詞彙」新增規則。'));
  $('apply-preview').disabled=!p.changes.length;$('preview-dialog').showModal();
});
$('close-preview').onclick=()=>$('preview-dialog').close();
$('apply-preview').onclick=protect(async()=>{
  const ids=Array.from($('preview-list').querySelectorAll('input:checked')).map(n=>Number(n.dataset.id));
  if(!ids.length){notify('請至少勾選一個片段。');return;}
  state.doc=await api(`/jobs/${state.current}/apply`,{method:'POST',body:{revision:state.preview.revision,glossary_hash:state.preview.glossary_hash,ids}});$('preview-dialog').close();renderSegments();renderExports();$('save-state').textContent=`已儲存第 ${state.doc.revision} 版`;$('export-version').textContent=`下載第 ${state.doc.revision} 版；檔名會包含錄音名稱與版本。`;notify(`已修改 ${ids.length} 個片段，原始辨識保留。`);
});
window.addEventListener('beforeunload',e=>{if(state.dirty){const payload=draftPayload();if(payload)fetch(`/api/jobs/${state.current}/draft`,{method:'PUT',headers:{'Content-Type':'application/json','X-Local-Request':'1'},body:JSON.stringify(payload),keepalive:true}).catch(()=>{});e.preventDefault();e.returnValue='';}});
async function init(){
  const token=new URLSearchParams(location.hash.slice(1)).get('session');
  if(token){await api('/session',{method:'POST',headers:{Authorization:'Bearer '+token}});history.replaceState(null,'',location.pathname);}
  await refreshStatus();state.authenticated=true; if(!state.status.models.find(m=>m.name===$('model').value)?.ready){const ready=state.status.models.find(m=>m.ready); if(ready){$('model').value=ready.name; updateSubmit();}}
  const g=await api('/glossary');$('prompts').value=g.prompts.join('\n');$('rules').value=g.rules.map(r=>r.from+' → '+r.to).join('\n');await refreshJobs();
  if(!state.status.models.some(m=>m.ready))changeView('setup');
  const prefs=await api('/preferences');const last=state.jobs.find(j=>j.id===prefs.job_id&&j.status==='complete');if(last)await openJob(last,prefs.position||0,prefs.segment_position||0);
  jobsInitialized=true;await refreshJobs();
  state.checking=true;api('/update/check',{method:'POST'}).then(result=>{state.checking=false;renderUpdate(result);}).catch(()=>{state.checking=false;});
}
protect(init)();
let polling=false;
async function pollJobs(){if(!state.authenticated||!jobsInitialized||polling||document.hidden||state.stopped)return;polling=true;try{await refreshJobs();if(state.status?.download.status==='downloading')await refreshStatus();}catch(e){$('service-notice').hidden=false;$('service-message').textContent=state.updating?'正在切換版本，請稍候新網頁。':'服務已停止或連線中斷。請輸入 tr，選擇開啟工作台。';}finally{polling=false;}}
setInterval(pollJobs,2500);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollJobs();});
setInterval(()=>{if(state.authenticated&&!state.stopped&&!document.hidden)protect(refreshStatus)();},20000);

$('update-button').onclick=protect(async()=>{
  if(state.checking)return;state.checking=true;
  $('update-button').disabled=true;$('update-button').textContent='檢查中…';
  try {
    const current=await api('/update/check',{method:'POST'});state.checking=false;renderUpdate(current);
    if(!current.can_update){notify(current.check_message);return;}
    if(!current.available){notify('請從 tr 選單開啟背景服務，才能安全更新。');return;}
    const repair=current.check_status==='repair_available';
    if(state.dirty){await flushDraft();notify('請先儲存修改，再更新或修復。');return;}
    if(!confirm(repair?`${current.check_message} 請確認所有視窗的修改都已儲存。開始安裝嗎？`:'有新版本可以更新。請確認所有視窗的修改都已儲存。開始下載安裝嗎？'))return;
    const result=await api('/update',{method:'POST'});
    state.updating=['requested','preparing','validating','switching'].includes(result.status);
    renderUpdate(result);
  } finally {state.checking=false;if(!state.updating)$('update-button').disabled=false;}
});
function renderUpdate(update){
  const active=['requested','preparing','validating','switching'].includes(update.status);
  $('update-button').disabled=active||state.checking;
  $('update-button').textContent=state.checking?'檢查中…':active?'更新中…':update.check_status==='repair_available'?'↻ 修復版本':update.can_update?'↑ 有新版':update.check_status==='up_to_date'?'✓ 已是最新':'↻ 檢查更新';
  $('update-button').title=update.check_message||'檢查是否有新版本';
  $('update-message').textContent=active?update.message:update.check_message||update.message||'';
}
async function refreshUpdate(){
  const update=await api('/update');
  const active=['requested','preparing','validating','switching'].includes(update.status);
  const wasUpdating=state.updating;state.updating=active;updateSubmit();$('save').disabled=active||state.saving||!state.dirty;renderUpdate(update);
  if(active&&state.dirty)await flushDraft();
  if(wasUpdating&&!active&&['complete','rolled_back'].includes(update.status)){if(state.dirty){await flushDraft();state.dirty=false;}location.reload();}
}
setInterval(()=>{if(state.authenticated&&!state.stopped)protect(refreshUpdate)();},2000);
$('stop-service').onclick=protect(async()=>{
  if(state.dirty){await flushDraft();notify('請先儲存修改，再停止服務。');return;}
  if(!confirm('停止背景服務？完成片段會保留，未完成的片段下次重跑。'))return;
  await api('/service/stop',{method:'POST'});state.stopped=true;
  $('service-notice').hidden=false;$('service-message').textContent='服務已停止，可輸入 tr 重新開啟。';
});
function draftPayload(){
  if(!state.current||!state.dirty)return null;
  return {window_id:windowId,base_revision:state.doc.revision,position:window.scrollY,segment_position:$('segments').scrollTop,
    segments:state.doc.segments.filter(r=>state.changed.has(r.id)).map(r=>({id:r.id,text:r.edited_text??r.traditional_text,reviewed:r.reviewed}))};
}
async function flushDraft(){
  clearTimeout(draftTimer);const payload=draftPayload();if(!payload)return;
  const job=state.current;
  draftQueue=draftQueue.catch(()=>{}).then(()=>api(`/jobs/${job}/draft`,{method:'PUT',body:payload}));
  await draftQueue;
  if(state.current===job&&state.dirty)$('save-state').textContent='草稿已備份 · 尚未正式儲存';
}
async function loadDrafts(){
  const drafts=await api(`/jobs/${state.current}/drafts`);$('draft-list').replaceChildren();
  $('draft-notice').hidden=!drafts.some(d=>d.segments.length);
  for(const draft of drafts.filter(d=>d.segments.length)){
    const row=el('div',undefined,'draft-row');row.append(el('span',`草稿 · ${new Date(draft.saved_at).toLocaleString()} · 基於第 ${draft.base_revision} 版`));
    const restore=el('button','預覽恢復','button subtle');restore.onclick=()=>previewDraft(draft);row.append(restore);
    const remove=el('button','清除草稿','button subtle');remove.onclick=protect(async()=>{if(confirm('清除此草稿？正式逐字稿會保留。')){await api(`/jobs/${state.current}/drafts/${draft.window_id}`,{method:'DELETE'});await loadDrafts();}});row.append(remove);$('draft-list').append(row);
  }
}
function previewDraft(draft){
  $('draft-preview').replaceChildren();
  $('draft-warning').textContent=draft.base_revision===state.doc.revision?'勾選要帶回編輯區的修改；恢復後仍需按儲存修改。':'正式稿已有其他版本。請逐項比對，避免覆蓋其他視窗的修改。';
  for(const item of draft.segments){
    const current=state.doc.segments.find(r=>r.id===item.id);if(!current)continue;
    const row=el('div',undefined,'preview-row');const label=el('label',undefined,'check');const check=el('input');check.type='checkbox';check.checked=true;check.dataset.id=item.id;
    label.append(check,el('span',time(current.start)));row.append(label,el('div',current.edited_text??current.traditional_text,'preview-before'),el('div',item.text,'preview-after'));$('draft-preview').append(row);
  }
  $('restore-draft').onclick=()=>{
    const ids=new Set([...$('draft-preview').querySelectorAll('input:checked')].map(n=>Number(n.dataset.id)));
    if(!ids.size){notify('請選擇要恢復的片段。');return;}
    for(const item of draft.segments.filter(x=>ids.has(x.id))){const row=state.doc.segments.find(r=>r.id===item.id);row.edited_text=item.text;row.reviewed=item.reviewed;state.changed.add(item.id);}
    state.recoveredDraftIds.add(draft.window_id);renderSegments();markDirty();$('draft-dialog').close();window.scrollTo(0,draft.position||0);$('segments').scrollTop=draft.segment_position||0;notify('草稿已恢復至編輯區，請確認後儲存。');
  };
  $('draft-dialog').showModal();
}
$('close-draft').onclick=()=>$('draft-dialog').close();
function renderExports(){
  $('exports').replaceChildren(...['md','txt','srt','json'].map(fmt=>{
    const link=el('a',fmt.toUpperCase());link.href=`/api/jobs/${state.current}/export/${fmt}?revision=${state.doc.revision}`;
    link.onclick=protect(async event=>{
      event.preventDefault();if(state.dirty){notify('請先儲存修改，再匯出。');return;}
      const response=await fetch(link.href,{cache:'no-store'});
      if(!response.ok)throw new Error('匯出失敗或版本已變更，請保留草稿並重新載入。');
      const url=URL.createObjectURL(await response.blob());const download=el('a');download.href=url;
      download.download=decodeURIComponent((response.headers.get('Content-Disposition')||'').split("UTF-8''")[1]||`逐字稿.${fmt}`);
      document.body.append(download);download.click();download.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
    });return link;
  }));
}
function persistPosition(){
  clearTimeout(positionTimer);if(!state.current||state.stopped||state.updating)return;
  positionTimer=setTimeout(()=>protect(()=>api('/preferences',{method:'PUT',body:{job_id:state.current,position:window.scrollY,segment_position:$('segments').scrollTop}}))(),800);
}
window.addEventListener('scroll',persistPosition,{passive:true});
$('segments').addEventListener('scroll',persistPosition,{passive:true});
