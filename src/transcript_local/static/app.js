'use strict';
const $ = id => document.getElementById(id);
const state = {files: [], jobs: [], current: null, doc: null, dirty: false, status: null, busy: false, preview: null, authenticated: false, jobsKey: '', changed:new Set(), recoveredDraftIds:new Set(), updating:false, stopped:false, saving:false, opening:false, checking:false};
const jobStatus = new Map();
let jobsInitialized=false, jobRequest=0, appliedJobRequest=0, openRequest=0, openingAutomatic=false, pendingCompletions=[];
const windowId=sessionStorage.getItem('iht-window')||crypto.randomUUID();sessionStorage.setItem('iht-window',windowId);
let draftTimer, draftQueue=Promise.resolve(), positionTimer;
const modelInfo={medium:{title:'Whisper Medium',description:'一般中文錄音的均衡選擇；不知道選哪個時可先使用。速度與資源需求居中。'},small:{title:'Whisper Small',description:'模型較小，適合短錄音或希望較快取得初稿；可能需要較多校稿。'},qwen3:{title:'Qwen3-ASR 1.7B',description:'適合想嘗試另一種中文辨識結果，或 Whisper 辨識不理想的錄音。需額外下載時間對齊模型；處理時間與資源需求視電腦和錄音而定。'}};
function el(tag, text, cls) {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;}
function notify(message) {$('toast').textContent = message; $('toast').hidden = false; clearTimeout(notify.timer); notify.timer = setTimeout(() => $('toast').hidden = true, 6500);}
function bytes(n) {return n >= 1024**3 ? (n/1024**3).toFixed(1)+' GB' : (n/1024**2).toFixed(0)+' MB';}
function time(n) {return `${Math.floor(n/60).toString().padStart(2,'0')}:${Math.floor(n%60).toString().padStart(2,'0')}`;}
async function apiResponse(path, options = {}) {
  const headers = {'X-Local-Request':'1',...options.headers};
  if (options.body && !(options.body instanceof FormData)) {headers['Content-Type']='application/json'; options.body=JSON.stringify(options.body);}
  const response = await fetch('/api'+path, {...options,headers});
  if (!response.ok) {let message=await response.text(); try {const parsed=JSON.parse(message); message=typeof parsed.detail==='string'?parsed.detail:JSON.stringify(parsed.detail);} catch {} throw new Error(message || `操作失敗 (${response.status})`);}
  return response;
}
async function api(path, options) {
  const response = await apiResponse(path, options);
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
  if(!state.stopped)$('service-notice').hidden=true;state.jobs=jobs;$('job-count').textContent=jobs.length;
  renderJobs();
  scheduleCompleted();
}
function renderJobs(){
  const jobs=state.jobs;
  const key=JSON.stringify([state.current,selectionMode,jobs]);
  if(key===state.jobsKey)return;
  state.jobsKey=key;
  const eligible = new Set(jobs.filter(job=>job.status==='complete').map(job=>job.id));
  for(const id of selectedJobs)if(!eligible.has(id))selectedJobs.delete(id);
  updateSelection();
  if (!jobs.length){$('jobs').replaceChildren(el('p','第一份逐字稿，從一段錄音開始。','empty-small'));return;}
  $('jobs').replaceChildren(...jobs.map(j=>{
    const item=el('article',undefined,'job'+(j.id===state.current?' active':''));const title=el('button',j.name,'job-title');title.onclick=protect(()=>openJob(j));const label=j.status==='complete'?(j.id===state.current?'檢視中':'已完成・點擊查看'):j.label;item.append(title,el('div',`${label} · ${j.model}`,'job-meta'));
    if(selectionMode){
      title.disabled=j.status!=='complete';
      if(j.status==='complete'){
        const checkbox=el('input');
        checkbox.type='checkbox';
        checkbox.dataset.jobId=j.id;
        checkbox.setAttribute('aria-label','選擇 '+j.name);
        item.classList.add('selectable-job');
        item.prepend(checkbox);
        title.onclick=null;
        item.onclick=event=>selectJob(j.id,event.shiftKey);
      }
      return item;
    }
    if(j.progress?.message&&j.status!=='complete')item.append(el('p',j.progress.message,'job-meta'));
    if(j.status==='running'&&j.progress?.total){const p=el('progress');p.max=j.progress.total;p.value=j.progress.completed||0;p.setAttribute('aria-label','辨識片段進度');item.append(p);}
    if(j.error){item.append(el('div',j.error_hint||j.error,'job-error'));if(j.error_hint){const details=el('details',undefined,'original');details.append(el('summary','查看錯誤詳情'),el('pre',j.error));item.append(details);}}
    const buttons=el('div',undefined,'job-buttons');
    if(j.actions.includes('cancel')){const b=el('button','停止','button subtle');b.onclick=protect(async()=>{await api(`/jobs/${j.id}/cancel`,{method:'POST'});await refreshJobs();});buttons.append(b);}
    if(j.error_code==='model_missing'){
      const settings=el('button','前往模型設定','button subtle');
      settings.onclick=()=>changeView('setup');buttons.append(settings);
    }
    if(j.actions.includes('resume')){const next=(j.progress?.completed||0)+1;const label=j.progress?.total>=next?'從第 '+next+' 段繼續':'續跑';const b=el('button',label,'button subtle');b.onclick=protect(async()=>{await api(`/jobs/${j.id}/resume`,{method:'POST'});await refreshJobs();});buttons.append(b);}
    if(j.actions.includes('delete')){const b=el('button','清除','button subtle');b.onclick=protect(async()=>{if(!confirm(`將「${j.name}」的副本、進度與校稿移至本機 trash？\n原始錄音不會刪除。`))return;if(j.id===state.current&&state.dirty&&!confirm('尚有未儲存修改，仍要清除嗎？'))return;await api(`/jobs/${j.id}`,{method:'DELETE'});if(j.id===state.current){state.current=null;state.doc=null;state.dirty=false;$('editor').hidden=true;$('editor-empty').hidden=false;}await refreshJobs();await refreshStatus();});buttons.append(b);}
    item.append(buttons);return item;
  }));
  updateSelection();
}
let selectionMode=false;
let selectionAnchor=null;
let exportingBatch=false;
const selectedJobs=new Set();

function updateSelection(){
  $('batch-actions').hidden=!selectionMode;
  $('batch-md').textContent=state.dirty&&selectedJobs.has(state.current)?'儲存並匯出 Markdown':'匯出 Markdown';
  $('selection-count').textContent=`已選 ${selectedJobs.size} 份`;
  const disabled=exportingBatch||!selectedJobs.size;
  $('batch-md').disabled=disabled;
  $('batch-formats').disabled=disabled;
  if(disabled)showBatchFormats(false);
  for(const checkbox of $('jobs').querySelectorAll('[data-job-id]')){
    checkbox.checked=selectedJobs.has(checkbox.dataset.jobId);
  }
}
function selectJob(id, range){
  const ids=state.jobs.filter(job=>job.status==='complete').map(job=>job.id);
  const anchor=ids.indexOf(selectionAnchor);
  if(range&&anchor>=0){
    const end=ids.indexOf(id);
    for(const selected of ids.slice(Math.min(anchor,end),Math.max(anchor,end)+1))selectedJobs.add(selected);
  }else{
    if(selectedJobs.has(id))selectedJobs.delete(id);
    else selectedJobs.add(id);
    selectionAnchor=id;
  }
  updateSelection();
}
function setSelectionMode(enabled){
  selectionMode=enabled;
  selectedJobs.clear();
  selectionAnchor=null;
  $('select-jobs').textContent=enabled?'完成':'選擇';
  $('select-jobs').setAttribute('aria-pressed',String(enabled));
  renderJobs();
  if(!enabled){
    $('select-jobs').focus();
    scheduleCompleted();
  }
}
function showBatchFormats(open){
  $('batch-format-options').hidden=!open;
  $('batch-formats').setAttribute('aria-expanded',String(open));
}
$('select-jobs').onclick=()=>setSelectionMode(!selectionMode);
$('batch-formats').onpointerenter=()=>{if(!$('batch-formats').disabled)showBatchFormats(true);};
$('batch-formats').onclick=event=>{
  const keyboard=event.detail===0;
  const open=$('batch-format-options').hidden||!keyboard;
  showBatchFormats(open);
  if(open&&keyboard)$('batch-format-options').querySelector('button').focus();
};
$('batch-download').onpointerleave=()=>{
  if(!$('batch-format-options').contains(document.activeElement))showBatchFormats(false);
};
$('batch-download').onfocusout=event=>{
  if(!$('batch-download').contains(event.relatedTarget))showBatchFormats(false);
};
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&selectionMode&&!event.defaultPrevented&&!document.querySelector('dialog[open]')){
    event.preventDefault();
    setSelectionMode(false);
  }
});
async function downloadBatch(format){
  const ids=state.jobs.filter(job=>selectedJobs.has(job.id)).map(job=>job.id);
  if(ids.length)await beginExport(ids,format);
}
$('batch-md').onclick=protect(()=>downloadBatch('md'));
for(const button of $('batch-format-options').querySelectorAll('button')){
  button.onclick=protect(async()=>{
    showBatchFormats(false);
    await downloadBatch(button.dataset.format);
    if(selectionMode)$('batch-formats').focus();
  });
}

function scheduleCompleted(){
  if(!jobsInitialized||!pendingCompletions.length||document.hidden||selectionMode)return;
  if(state.current){pendingCompletions=[];return;}
  if(state.opening||state.saving||state.dirty)return;
  const job=state.jobs.find(j=>j.id===pendingCompletions[0]&&j.status==='complete');
  if(!job){pendingCompletions.shift();scheduleCompleted();return;}
  pendingCompletions=[];
  protect(()=>openJob(job,0,0,true))();
}
function markDirty(id){if(id!==undefined)state.changed.add(id);clearTimeout(draftTimer);draftTimer=setTimeout(()=>protect(flushDraft)(),1000);state.dirty=true;renderExports();$('save').disabled=false;$('save-state').textContent='尚未儲存';}
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
  $('recording-name').textContent=j.name;$('save-state').textContent=`已載入第 ${doc.revision} 版`;$('save').disabled=true;$('audio').src=`/api/jobs/${j.id}/audio`;
  renderSegments();renderExports();await loadDrafts();await refreshJobs();$('editor-empty').hidden=true;$('editor').hidden=false;$('now-playing').hidden=true;fitAll();window.scrollTo(0,position);revealActiveJob();$('segments').scrollTop=segmentPosition;
  }finally{if(request===openRequest){state.opening=false;openingAutomatic=false;scheduleCompleted();}}
}
function renderSegments(){
  $('segments').replaceChildren(...state.doc.segments.map(r=>{
    const row=el('article',undefined,'segment');row.dataset.id=r.id;row.dataset.pending=String(!!r.review_reasons.length&&!r.reviewed);
    const meta=el('div',undefined,'segment-meta');const play=el('button',`${time(r.start)} ↗`,'time');play.setAttribute('aria-label',`回聽 ${time(r.start)} 到 ${time(r.end)}`);
    play.onclick=protect(async()=>{const audio=$('audio');audio.currentTime=r.start;audio.dataset.end=r.end;await audio.play();});meta.append(play);
    const label=el('label',undefined,'check');const check=el('input');check.type='checkbox';check.checked=r.reviewed;check.onchange=()=>{const current=state.doc.segments.find(x=>x.id===r.id);current.reviewed=check.checked;markDirty(r.id);row.dataset.pending=String(!!current.review_reasons.length&&!current.reviewed);filterSegments();};label.append(check,el('span','已確認'));meta.append(label);
    const body=el('div');const input=el('textarea');input.value=r.edited_text??r.traditional_text;input.setAttribute('aria-label',`${time(r.start)} 的逐字稿`);input.oninput=()=>{state.doc.segments.find(x=>x.id===r.id).edited_text=input.value;markDirty(r.id);fit(input);};input.onclick=event=>{if(event.altKey)protect(()=>playFrom(r,input))();};
    const wrap=el('div',undefined,'text-wrap');const karaoke=el('div',undefined,'karaoke');karaoke.setAttribute('aria-hidden','true');wrap.append(karaoke,input);body.append(wrap);
    if(r.review_reasons.length){
      const reason=el('button',r.review_reasons.join('；')+' · 回聽','button subtle review-reason');
      reason.onclick=play.onclick;body.append(reason);
    }
    const details=el('details',undefined,'original');details.append(el('summary','查看原始辨識'),el('div',r.raw_text));body.append(details);row.append(meta,body);return row;
  }));filterSegments();
}
function filterSegments(){
  const pending=state.doc.segments.filter(segment=>segment.review_reasons.length&&!segment.reviewed).length;
  $('pending-count').textContent='只看待確認片段（'+pending+' 段）';
  const query=$('find').value.trim().toLowerCase();let shown=0;
  document.querySelectorAll('.segment').forEach(n=>{n.hidden=($('only-pending').checked&&n.dataset.pending!=='true')||(!!query&&!n.querySelector('textarea').value.toLowerCase().includes(query));if(!n.hidden)shown++;});
  $('find-count').textContent=query?`符合 ${shown} 段`:'';
}
$('find').oninput=filterSegments;
$('only-pending').onchange=filterSegments;
// Textareas grow with their text; CSS field-sizing does it where supported.
const nativeFit=CSS.supports('field-sizing','content');
function fit(input){if(nativeFit)return;input.style.height='auto';input.style.height=input.scrollHeight+2+'px';}
// Keep the open task visible inside the sticky task list without moving the page.
function revealActiveJob(){const box=document.querySelector('.intake'),active=box.querySelector('.job.active');if(!active||box.scrollHeight<=box.clientHeight)return;
  if(active.offsetTop<box.scrollTop||active.offsetTop+active.offsetHeight>box.scrollTop+box.clientHeight)box.scrollTop=active.offsetTop-box.clientHeight/3;}
function fitAll(){document.querySelectorAll('#segments textarea').forEach(fit);}
window.addEventListener('resize',fitAll);
// Each word's time is stored, but edits change the text, so a word's place in the
// text is matched by proportion. ponytail: drifts a few characters after heavy edits;
// re-align on save if exact karaoke ever matters. Old transcripts without words spread evenly.
function wordSpans(r,text){
  let words=(r.words||[]).filter(w=>w.text.trim());
  if(!words.length)words=[...text].map((c,i,all)=>({text:c,start:r.start+(r.end-r.start)*i/all.length,end:r.start+(r.end-r.start)*(i+1)/all.length}));
  const total=words.reduce((n,w)=>n+w.text.trim().length,0);let seen=0;
  return words.map(w=>{const from=Math.round(seen/total*text.length);seen+=w.text.trim().length;return {from,to:Math.round(seen/total*text.length),start:w.start,end:w.end};});
}
async function playFrom(r,input){
  const audio=$('audio'),word=wordSpans(r,input.value).find(w=>input.selectionStart<w.to);
  delete audio.dataset.end;audio.currentTime=word?word.start:r.start;await audio.play();
}
let followed=null;
function showPlaying(){
  const audio=$('audio'),t=audio.currentTime,label=$('now-playing');
  const current=state.doc?.segments.find(x=>x.start<=t&&t<x.end);
  document.querySelectorAll('.segment.now').forEach(n=>{if(n.dataset.id!==String(current?.id)){n.classList.remove('now');n.querySelector('.karaoke')?.replaceChildren();}});
  if(!current){label.hidden=true;return;}
  const row=document.querySelector(`.segment[data-id="${current.id}"]`);row?.classList.add('now');
  const text=current.edited_text??current.traditional_text;
  const word=wordSpans(current,text).findLast(w=>w.start<=t);
  if(word&&row)row.querySelector('.karaoke').replaceChildren(text.slice(0,word.from),el('mark',text.slice(word.from,word.to)),text.slice(word.to));
  // Follow along while listening, but never pull the page away from a segment being typed in.
  if(followed!==current.id&&!audio.paused&&row&&!row.hidden){followed=current.id;const typing=document.activeElement?.closest?.('.segment');if(!typing||typing===row)row.scrollIntoView({block:'nearest',behavior:'smooth'});}
  label.textContent=`${audio.paused?'⏸ 停在':'▶ 正在聽'} ${time(current.start)} · ${text.length>24?text.slice(0,24)+'…':text}`;
  label.dataset.id=current.id;label.hidden=false;
}
$('audio').ontimeupdate=()=>{const audio=$('audio');if(audio.dataset.end&&audio.currentTime>=Number(audio.dataset.end)){audio.pause();delete audio.dataset.end;}showPlaying();};
function follow(){showPlaying();if(!$('audio').paused)requestAnimationFrame(follow);}
$('audio').onpause=showPlaying;$('audio').onplay=follow;
function setSpeed(value){const audio=$('audio');audio.defaultPlaybackRate=audio.playbackRate=Number(value)||1;$('speed').value=String(audio.playbackRate);try{localStorage.setItem('iht-speed',$('speed').value);}catch{}}
$('speed').onchange=()=>setSpeed($('speed').value);
try{setSpeed(localStorage.getItem('iht-speed')||1);}catch{setSpeed(1);}
// Review shortcuts. Option/Command combinations only, so plain typing and the Chinese input method are untouched.
document.addEventListener('keydown',event=>{
  if(event.isComposing||$('editor').hidden||document.querySelector('dialog[open]'))return;
  const audio=$('audio'),row=document.activeElement?.closest?.('.segment'),mod=event.metaKey||event.ctrlKey;
  const segment=row&&state.doc.segments.find(x=>String(x.id)===row.dataset.id);
  const rows=()=>[...document.querySelectorAll('.segment:not([hidden])')];
  let handled=true;
  if(event.key==='Escape'&&!event.defaultPrevented&&!selectionMode&&event.target!==$('find')){if(audio.paused)protect(()=>audio.play())();else audio.pause();}
  else if(mod&&event.key.toLowerCase()==='s')protect(save)();
  else if(mod&&event.key==='Enter'&&row){const check=row.querySelector('.check input');if(!check.checked){check.checked=true;check.onchange();}
    const all=rows(),next=all.slice(all.indexOf(row)+1).find(n=>n.dataset.pending==='true')||all[all.indexOf(row)+1];next?.querySelector('textarea').focus();}
  else if(event.altKey&&event.key==='Enter'&&segment)protect(()=>playFrom(segment,row.querySelector('textarea')))();
  else if(event.altKey&&(event.key==='ArrowDown'||event.key==='ArrowUp')&&row){const all=rows();all[all.indexOf(row)+(event.key==='ArrowDown'?1:-1)]?.querySelector('textarea').focus();}
  else if(event.altKey&&(event.code==='Comma'||event.code==='Period'))audio.currentTime=Math.max(0,audio.currentTime+(event.code==='Comma'?-3:3));
  else handled=false;
  if(handled)event.preventDefault();
});
$('now-playing').onclick=()=>document.querySelector(`.segment[data-id="${$('now-playing').dataset.id}"] textarea`)?.focus();
$('new-recording').onclick=()=>{const open=$('new-recording').closest('.intake').classList.toggle('intake-open');$('new-recording').setAttribute('aria-expanded',String(open));};
$('toggle-jobs').onclick=()=>{const open=$('toggle-jobs').closest('.intake').classList.toggle('jobs-open');$('toggle-jobs').setAttribute('aria-expanded',String(open));};
$('audio').onerror=()=>notify('回聽音訊無法載入，請確認任務資料仍完整。');
async function save(){
  if(!state.current||!state.dirty)return;
  state.saving=true;
  const fields=[...$('segments').querySelectorAll('textarea, input')];fields.forEach(n=>n.disabled=true);$('save').disabled=true;
  try {
    await flushDraft();
    const doc=await api(`/jobs/${state.current}/transcript`,{method:'PUT',body:{revision:state.doc.revision,segments:state.doc.segments.map(r=>({id:r.id,text:r.edited_text??r.traditional_text,reviewed:r.reviewed}))}});
    state.doc=doc;state.dirty=false;state.changed.clear();
    const position=window.scrollY,segmentPosition=$('segments').scrollTop;renderSegments();renderExports();fitAll();window.scrollTo(0,position);$('segments').scrollTop=segmentPosition;
    $('save-state').textContent=`已儲存第 ${doc.revision} 版`;
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
  state.doc=await api(`/jobs/${state.current}/apply`,{method:'POST',body:{revision:state.preview.revision,glossary_hash:state.preview.glossary_hash,ids}});$('preview-dialog').close();renderSegments();renderExports();$('save-state').textContent=`已儲存第 ${state.doc.revision} 版`;notify(`已修改 ${ids.length} 個片段，原始辨識保留。`);
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
  protect(loadExportFolder)();
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
    state.recoveredDraftIds.add(draft.window_id);renderSegments();markDirty();$('draft-dialog').close();fitAll();window.scrollTo(0,draft.position||0);$('segments').scrollTop=draft.segment_position||0;notify('草稿已恢復至編輯區，請確認後儲存。');
  };
  $('draft-dialog').showModal();
}
$('close-draft').onclick=()=>$('draft-dialog').close();
let exportFolder=null;
let defaultExportFolder=null;
let folderLoaded=false;
let pendingExport=null;
let exportBusy=false;
let canOpenExportFolder=false;
let replaceExportFolder=false;
let exportDialogUsedForFiles=false;
const exportDialog=$('export-dialog');

function renderExports(){
  $('export-md').textContent=state.dirty?'儲存並匯出 Markdown':'匯出 Markdown';
  const location=exportFolder?.path||'首次匯出時選擇位置';
  $('export-version').textContent=location;
  $('export-version').textContent=location;
  $('batch-destination').textContent=location;
  updateSelection();
}
function showExportDialog(){
  if(!exportDialog.open)exportDialog.showModal();
  renderExportDialog();
}
function renderExportDialog(){
  $('export-dialog-path').textContent=exportFolder?.path||'尚未選擇位置';
  $('remember-folder-label').hidden=!exportFolder||exportFolder.remembered;
  $('export-dialog-run').hidden=!pendingExport||!exportFolder||replaceExportFolder;
  $('export-dialog-run').textContent=pendingExport?.retry?'重試失敗項目':
    state.dirty&&pendingExport?.ids.includes(state.current)?'儲存並匯出':'匯出';
  $('export-open-finder').hidden=!canOpenExportFolder;
  for(const id of ['export-dialog-change','export-dialog-run','export-dialog-close','export-open-finder']){
    $(id).disabled=exportBusy;
  }
}
async function loadExportFolder(){
  if(!folderLoaded){
    defaultExportFolder=await api('/export-folder');
    exportFolder=defaultExportFolder;
    folderLoaded=true;
    renderExports();
  }
}
async function chooseFolder({first=false}={}){
  if(exportBusy)return;
  exportBusy=true;
  renderExportDialog();
  try{
    const chosen=await api('/export-folder/choose',{method:'POST'});
    if(chosen){
      exportFolder=chosen;
      replaceExportFolder=false;
      canOpenExportFolder=false;
      $('remember-folder').checked=false;
      $('export-dialog-status').textContent='';
      renderExports();
    }else if(first){
      pendingExport=null;
      exportDialog.close();
    }
  }catch(error){
    $('export-dialog-status').textContent=error.message;
  }finally{
    exportBusy=false;
    renderExportDialog();
  }
}
async function beginExport(ids,format){
  if(exportBusy||exportDialog.open)return;
  await loadExportFolder();
  exportDialogUsedForFiles=true;
  pendingExport={ids,format,retry:false};
  canOpenExportFolder=false;
  replaceExportFolder=false;
  $('export-failures').replaceChildren();
  $('export-dialog-status').textContent='';
  showExportDialog();
  if(exportFolder){
    if(!state.dirty||!ids.includes(state.current))await exportFiles();
  }else await chooseFolder({first:true});
}
async function exportFiles(){
  if(exportBusy||!pendingExport||!exportFolder)return;
  const {ids,format}=pendingExport;
  exportBusy=true;
  exportingBatch=true;
  updateSelection();
  renderExportDialog();
  $('export-dialog-status').textContent='正在匯出…';
  try{
    const revisions={};
    if(ids.includes(state.current)){
      if(state.saving)throw new Error('正在儲存，請稍後重試。');
      if(state.dirty)await save();
      revisions[state.current]=state.doc.revision;
    }
    if($('remember-folder').checked&&!exportFolder.remembered){
      exportFolder=await api('/export-folder',{method:'PUT',body:{destination:exportFolder.destination}});
      defaultExportFolder=exportFolder;
      $('remember-folder').checked=false;
    }
    const result=await api('/jobs/export-to-folder',{method:'POST',body:{
      ids,format,revisions,destination:exportFolder.destination
    }});
    canOpenExportFolder=canOpenExportFolder||result.saved.length>0;
    const failed=result.failed;
    $('export-failures').replaceChildren(...failed.map(item=>el('li',item.name+' — '+item.reason)));
    $('export-dialog-status').textContent=`已匯出 ${result.saved.length} 份${failed.length?'，'+failed.length+' 份失敗':''}。`;
    pendingExport=failed.length?{ids:failed.map(item=>item.id),format,retry:true}:null;
  }catch(error){
    $('export-dialog-status').textContent=error.message;
    replaceExportFolder=error.message.includes('匯出資料夾已無法使用');
  }finally{
    exportBusy=false;
    exportingBatch=false;
    renderExports();
    renderExportDialog();
  }
}
exportDialog.addEventListener('close',()=>{
  if(exportDialogUsedForFiles)exportFolder=defaultExportFolder;
  exportDialogUsedForFiles=false;
  pendingExport=null;
  $('remember-folder').checked=false;
  renderExports();
});
exportDialog.addEventListener('cancel',event=>{if(exportBusy)event.preventDefault();});
$('export-dialog-close').onclick=()=>exportDialog.close();
$('export-dialog-run').onclick=protect(exportFiles);
$('export-dialog-change').onclick=()=>chooseFolder();
$('export-open-finder').onclick=protect(()=>api('/export-folder/open',{
  method:'POST',body:{destination:exportFolder.destination}
}));
async function changeExportFolder(){
  if(exportBusy||exportDialog.open)return;
  await loadExportFolder();
  exportDialogUsedForFiles=false;
  pendingExport=null;
  canOpenExportFolder=false;
  replaceExportFolder=false;
  $('export-failures').replaceChildren();
  $('export-dialog-status').textContent='選擇下次匯出的位置。';
  showExportDialog();
  await chooseFolder({first:true});
  if(exportDialog.open&&exportFolder&&!exportFolder.remembered){
    $('remember-folder-label').hidden=false;
  }
}
$('remember-folder').onchange=protect(async()=>{
  if(pendingExport||!$('remember-folder').checked)return;
  exportFolder=await api('/export-folder',{method:'PUT',body:{destination:exportFolder.destination}});
  defaultExportFolder=exportFolder;
  $('remember-folder').checked=false;
  renderExports();
  renderExportDialog();
});
$('change-export-folder').onclick=protect(changeExportFolder);
$('batch-change-folder').onclick=protect(changeExportFolder);
$('export-md').onclick=protect(()=>beginExport([state.current],'md'));
const singleFormats=$('export-format-options');
function showSingleFormats(open){
  singleFormats.hidden=!open;
  $('export-formats').setAttribute('aria-expanded',String(open));
}
$('export-formats').onpointerenter=()=>showSingleFormats(true);
$('export-formats').onclick=event=>{
  showSingleFormats(true);
  if(!event.detail)singleFormats.querySelector('button').focus();
};
const singleMenu=$('export-formats').parentElement;
singleMenu.onpointerleave=()=>{
  if(!singleMenu.contains(document.activeElement))showSingleFormats(false);
};
singleMenu.onfocusout=event=>{
  if(!singleMenu.contains(event.relatedTarget))showSingleFormats(false);
};
for(const button of singleFormats.querySelectorAll('button')){
  button.onclick=protect(async()=>{
    showSingleFormats(false);
    await beginExport([state.current],button.dataset.format);
  });
}
singleMenu.onkeydown=event=>{
  if(event.key==='Escape'&&!singleFormats.hidden){
    event.stopPropagation();
    showSingleFormats(false);
    $('export-formats').focus();
  }
};
const exportBar=document.querySelector('.export-bar');
new ResizeObserver(()=>{
  document.documentElement.style.setProperty('--export-height',exportBar.offsetHeight+'px');
}).observe(exportBar);
function persistPosition(){
  clearTimeout(positionTimer);if(!state.current||state.stopped||state.updating)return;
  positionTimer=setTimeout(()=>protect(()=>api('/preferences',{method:'PUT',body:{job_id:state.current,position:window.scrollY,segment_position:$('segments').scrollTop}}))(),800);
}
window.addEventListener('scroll',persistPosition,{passive:true});
$('segments').addEventListener('scroll',persistPosition,{passive:true});
