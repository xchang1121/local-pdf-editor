/* PDF Studio: no build step, no CDN, no third-party client-side dependencies.
 * Recipes are immutable; previews and exports are rendered by the same backend.
 */
'use strict';
const $ = id => document.getElementById(id);
const clone = value => structuredClone(value);
const uid = () => crypto.randomUUID();
const icons = {
  undo:'M9 5 4 10l5 5M4 10h9a6 6 0 0 1 6 6v2',
  redo:'m15 5 5 5-5 5m5-5h-9a6 6 0 0 0-6 6v2',
  folder:'M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z',
  download:'M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5',
  upload:'M12 16V4m-5 5 5-5 5 5M4 16v4h16v-4',
  plus:'M12 5v14M5 12h14',
  append:'M8 3H4v17h13v-4M8 3v5h5M8 3l5 5v4m4-9v8m-4-4h8',
  sort:'M7 3v18m-4-4 4 4 4-4M14 5h7m-7 6h5m-5 6h3',
  pages:'M8 3h9l4 4v12H8V3Zm9 0v5h4M4 7H2v15h15v-2',
  up:'m6 14 6-6 6 6', down:'m6 10 6 6 6-6',
  copy:'M8 8h12v13H8zM16 8V3H3v13h5',
  trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  cursor:'m5 3 14 9-7 1-4 7-3-17Z',
  text:'M4 5V3h16v2M12 3v18M8 21h8',
  crop:'M6 2v16h16M2 6h16v16',
  square:'M4 4h16v16H4z',
  shield:'m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6l8-3Zm-3 9 2 2 4-4',
  rotate:'M20 10a8 8 0 1 0-2 8M20 4v6h-6',
  reset:'M4 9a8 8 0 1 1 1 8M4 3v6h6',
  lock:'M7 11V7a5 5 0 0 1 10 0v4M5 11h14v10H5zM12 15v2',
  close:'m6 6 12 12M6 18 18 6',
  image:'M3 3h18v18H3zM3 16l5-5 4 4 3-3 6 6M8 7h.01',
  history:'M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2',
};
function mountIcons(root=document) {
  root.querySelectorAll('[data-icon]').forEach(el => {
    const path = icons[el.dataset.icon];
    if (path) el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
  });
}
mountIcons();

const S = {
  token: '', pages: [], active: null, selected: new Set(), tool: 'select',
  draft: null, draftBaseline: null, data: null, displayPage: null, zoom: 'fit', name: '未命名文档.pdf',
  timeline: [], historyCursor: 0, sidebar: 'pages', dirty: false, busy: false, drawing: null,
};
let previewSerial = 0, uploadMode = 'replace', toastTimer, busyCounter = 0;
let thumbnailObserver, thumbWorking = false, thumbQueue = [], dragId = null;
const previews = new Map(), thumbnails = new Map();
const CURRENT_KEY = 'pdf-studio-project-v1', TOKEN_KEY = 'pdf-studio-token-v1';
const HISTORY_LIMIT = 40;
let storageWarning = false;
const activePage = () => S.pages.find(p => p.id === S.active);
const signature = page => JSON.stringify(page);
function cachePut(cache, key, value, limit) {
  cache.delete(key); cache.set(key, value);
  while (cache.size > limit) cache.delete(cache.keys().next().value);
}
function toast(message, type='info', duration=4500) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').className = `toast ${type}`;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, duration);
}
function setStatus(text) { $('status-message').textContent = text; }
function busyStart(message) {
  busyCounter++;
  $('busy-message').textContent = message;
  $('busy-indicator').hidden = false;
}
function busyEnd() {
  busyCounter = Math.max(0, busyCounter - 1);
  if (!busyCounter) $('busy-indicator').hidden = true;
}
async function mutate(message, fn) {
  if (S.busy) return;
  S.busy = true;
  document.body.classList.add('working');
  busyStart(message); updateControls();
  try { await fn(); }
  catch (error) { toast(error.message || '操作失败。', 'error', 7000); }
  finally {
    S.busy = false; busyEnd(); document.body.classList.remove('working'); updateControls();
  }
}
async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  if (S.token) headers.set('X-Session-Token', S.token);
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const result = await response.json();
      message = typeof result.detail === 'string' ? result.detail : '参数无效，请检查文字、坐标或页面范围。';
    } catch (_) { /* Keep the HTTP error rather than hiding it. */ }
    throw new Error(message);
  }
  return response;
}
function post(path, body) {
  return api(path, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
}
function snapshot() { return clone({ pages:S.pages, active:S.active, name:S.name }); }
function resetHistory(label='开始编辑') {
  S.timeline=[{label,time:new Date().toISOString(),state:snapshot()}];S.historyCursor=0;
}
function packHistory() {
  // Reuse unchanged page recipes in storage instead of serializing forty full
  // copies of a long document. Image bytes live in the server session separately.
  const pages=[],ids=new Map();
  const entries=S.timeline.map(entry=>({...entry,state:{...entry.state,pages:entry.state.pages.map(page=>{
    const key=signature(page);if(!ids.has(key)){ids.set(key,pages.length);pages.push(page);}return ids.get(key);
  })}}));
  return {pages,entries,cursor:S.historyCursor};
}
function unpackHistory(saved,validPage) {
  const packed=saved.history;
  if(!packed||!Array.isArray(packed.pages)||!Array.isArray(packed.entries)||!packed.entries.length||packed.entries.length>HISTORY_LIMIT+1)return false;
  if(!Number.isInteger(packed.cursor)||packed.cursor<0||packed.cursor>=packed.entries.length)return false;
  if(!packed.pages.every(validPage))return false;
  try {
    const entries=packed.entries.map(entry=>{
      if(typeof entry.label!=='string'||!entry.state||!Array.isArray(entry.state.pages))throw new Error('invalid history');
      return {...entry,state:{...entry.state,pages:entry.state.pages.map(index=>{
        if(!Number.isInteger(index)||!packed.pages[index])throw new Error('invalid page');return clone(packed.pages[index]);
      })}};
    });
    if(signature(entries[packed.cursor].state.pages)!==signature(S.pages))return false;
    S.timeline=entries;S.historyCursor=packed.cursor;return true;
  } catch(_){return false;}
}
function persist() {
  try {
    sessionStorage.setItem(TOKEN_KEY, S.token);
    sessionStorage.setItem(CURRENT_KEY, JSON.stringify({ ...snapshot(), dirty:S.dirty, history:packHistory() }));
  } catch (_) {
    let currentSaved=false;
    try {sessionStorage.setItem(CURRENT_KEY,JSON.stringify({...snapshot(),dirty:S.dirty}));currentSaved=true;}catch(_){}
    if (!storageWarning) {
      storageWarning = true;
      toast(currentSaved?'历史记录超出浏览器暂存空间；当前文档已暂存，刷新后只能恢复当前状态。':'浏览器暂存空间不足；请及时导出，刷新将无法完整恢复。', 'warning', 7000);
    }
  }
}
function commit(pages, active, label, name=S.name) {
  if(!S.timeline.length)resetHistory();
  S.timeline=S.timeline.slice(0,S.historyCursor+1);
  S.pages = pages; S.active = active; S.name = name; S.dirty = true;
  S.timeline.push({label,time:new Date().toISOString(),state:snapshot()});
  if(S.timeline.length>HISTORY_LIMIT+1)S.timeline.shift();
  S.historyCursor=S.timeline.length-1;
  S.selected = new Set([...S.selected].filter(id => pages.some(p => p.id === id)));
  clearDraft(); persist(); refresh(); setStatus(label);
}
function restore(snapshotValue) {
  S.pages = snapshotValue.pages; S.active = snapshotValue.active; S.name = snapshotValue.name;
  S.selected.clear(); S.dirty = true; clearDraft(); persist(); refresh();
}
function historyStep(redo=false) {
  jumpHistory(S.historyCursor+(redo?1:-1));
}
function jumpHistory(index) {
  if(S.busy||index===S.historyCursor||index<0||index>=S.timeline.length||!discardDraft())return;
  S.historyCursor=index;restore(clone(S.timeline[index].state));
  setStatus(`已回到：${S.timeline[index].label}`);
}
function showSidebar(panel) {
  S.sidebar=panel;
  for(const name of ['pages','history']) {
    $(`${name}-panel`).hidden=name!==panel;
    $(`tab-${name}`).setAttribute('aria-selected',String(name===panel));
  }
  if(panel==='history')renderHistory();else renderThumbnails();
}
function renderHistory() {
  $('history-count').textContent=Math.max(0,S.timeline.length-1);
  const list=$('history-list');list.replaceChildren();
  S.timeline.forEach((entry,index)=>{
    const row=document.createElement('li'),button=document.createElement('button');
    button.className=`history-entry${index===S.historyCursor?' current':''}${index>S.historyCursor?' future':''}`;
    button.dataset.index=index;button.disabled=S.busy;button.setAttribute('aria-current',index===S.historyCursor?'step':'false');
    const number=document.createElement('span');number.className='history-number';number.textContent=index?String(index).padStart(2,'0'):'•';
    const body=document.createElement('span'),title=document.createElement('strong'),meta=document.createElement('small');
    title.textContent=entry.label;
    const time=new Date(entry.time);meta.textContent=`${index===S.historyCursor?'当前状态':index>S.historyCursor?'可重做':'已完成'} · ${Number.isNaN(time.getTime())?'':time.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`;
    body.append(title,meta);button.append(number,body);button.onclick=()=>jumpHistory(index);row.append(button);list.append(row);
  });
}
function discardDraft() {
  if (!S.draft) return true;
  if (hasPendingDraftChanges() && !confirm('当前选区有尚未应用的修改。丢弃这些修改并继续？')) return false;
  clearDraft(); return true;
}
function draftFingerprint() {
  if(S.draft.kind==='image_replace')return JSON.stringify([S.draft.asset,$('image-fit').value]);
  // Read displayed values without validating or mutating geometry. The initial
  // form already contains rounded coordinates; comparing with raw PDF floats
  // would incorrectly mark a newly selected region as modified.
  const ids=['rect-x','rect-y','rect-w','rect-h'];
  if(['text','replace'].includes(S.draft.kind)) {
    ids.push('text-content','font-size','font-family','font-bold','font-italic',
      'text-align','text-color','line-height','text-fit','text-background');
    if($('text-background').checked)ids.push('background-color');
  } else if(S.draft.kind==='crop')ids.push('crop-all');
  else ids.push('area-color');
  return JSON.stringify(ids.map(id=>{
    const el=$(id);
    if(el.type==='checkbox')return [id,el.checked];
    const value=el.value;
    return [id,el.type==='number' && value!=='' && Number.isFinite(Number(value)) ? Number(value) : value];
  }));
}
function hasPendingDraftChanges() {
  if(!S.draft)return false;
  if(S.draft.kind==='image_replace')return !!S.draft.asset;
  if(S.draft.kind==='text')return !!$('text-content').value.trim();
  if(S.draft.kind==='replace')return draftFingerprint()!==S.draftBaseline;
  // Drawing a crop, cover or redaction is itself an actionable change.
  return true;
}
function updateDraftState() {
  $('draft-badge').hidden=!hasPendingDraftChanges();
  $('apply-edit').disabled=S.busy || (S.draft?.kind==='image_replace' && !S.draft.asset);
}
function updateControls() {
  const page = activePage(), has = S.pages.length > 0, disabled = S.busy;
  $('undo').disabled = S.historyCursor<=0 || disabled;
  $('redo').disabled = S.historyCursor>=S.timeline.length-1 || disabled;
  for (const id of ['btn-export','btn-reorder','page-duplicate','page-delete','rotate']) $(id).disabled = !has || disabled;
  $('page-up').disabled = !page || S.pages[0]?.id === page.id || disabled;
  $('page-down').disabled = !page || S.pages.at(-1)?.id === page.id || disabled;
  $('reset-page').disabled = !page?.ops.length || disabled;
  for (const id of ['btn-open','btn-append','btn-blank','btn-start-blank','btn-demo','btn-clear','apply-edit','cancel-edit','remove-text','choose-image-file']) $(id).disabled = disabled;
  if(S.draft?.kind==='image_replace' && !S.draft.asset)$('apply-edit').disabled=true;
  document.querySelectorAll('.history-entry').forEach(el=>{el.disabled=disabled;});
  document.querySelectorAll('[data-tool]').forEach(el => { el.disabled = disabled; });
  $('select-all').disabled = !has || disabled;
  $('select-all').checked = has && S.selected.size === S.pages.length;
  $('select-all').indeterminate = S.selected.size > 0 && S.selected.size < S.pages.length;
  $('selected-count').textContent = S.selected.size ? `已选 ${S.selected.size} 页` : '未勾选';
  $('document-title').textContent = has ? S.name : '未打开文档';
  $('document-subtitle').textContent = has ? `${S.pages.length} 页 · ${S.dirty ? '有未导出的修改' : '已导出 / 就绪'}` : '让文档修改，更直接一点。';
  $('page-count').textContent = S.pages.length;
  $('status-pages').textContent = has ? `第 ${S.pages.findIndex(p=>p.id===S.active)+1} / ${S.pages.length} 页 · 原文件不变` : '源文件不会被覆盖';
  $('detail-ops').textContent = page ? `${page.ops.length} 项` : '—';
}
function dimensions(page) {
  let w = page.width, h = page.height;
  for (const op of page.ops) {
    if (op.kind === 'crop') { w = op.rect[2]-op.rect[0]; h = op.rect[3]-op.rect[1]; }
    if (op.kind === 'rotate' && op.angle % 180) [w,h] = [h,w];
  }
  return [w,h];
}
function refresh() {
  updateControls(); renderThumbnails(); renderHistory(); updateTool();
  $('empty-state').hidden = S.pages.length > 0;
  $('page-container').hidden = !S.pages.length;
  if (S.pages.length) void showPage();
  else {
    ++previewSerial; S.data = null; S.displayPage = null;
    $('page-size').textContent = ''; $('detail-size').textContent = '—';
    $('text-status').textContent = '扫描件没有可直接编辑的文字层。本版不包含 OCR。';
    $('page-image').removeAttribute('src'); $('text-hits').replaceChildren();$('image-hits').replaceChildren();renderImageHits();
  }
}
async function getPreview(page, scale=1.5, withText=true) {
  const key = signature(page);
  if (withText && previews.has(key)) return previews.get(key);
  const response = await post('/preview', { page, scale, text:withText });
  const data = await response.json();
  if (withText) cachePut(previews, key, data, 10);
  cachePut(thumbnails, key, `data:image/png;base64,${data.image}`, 60);
  return data;
}
async function showPage() {
  const page = activePage(); if (!page) return;
  const serial = ++previewSerial;
  S.displayPage = null; $('text-hits').replaceChildren();$('image-hits').replaceChildren();
  // Never allow editing against the previous page while a new page is loading.
  $('overlay').style.pointerEvents = 'none';
  busyStart('正在渲染页面…');
  try {
    const data = await getPreview(page);
    if (serial !== previewSerial || page.id !== S.active) return;
    S.data = data; S.displayPage = page.id;
    const src = `data:image/png;base64,${data.image}`;
    $('page-image').src = src;
    $('overlay').style.pointerEvents = '';
    $('current-page-label').textContent = `PAGE ${String(S.pages.findIndex(p=>p.id===page.id)+1).padStart(2,'0')}`;
    $('page-source-label').textContent = page.label;
    const size = `${Math.round(data.width)} × ${Math.round(data.height)} pt`;
    $('page-size').textContent = size; $('detail-size').textContent = size;
    if (!data.has_text) $('text-status').textContent = '本页未检测到文字层，可能是扫描件或纯图片。可以添加文字、裁剪或移除区域；本版不自动 OCR。';
    else if (data.skipped) $('text-status').textContent = `有 ${data.skipped} 个旋转 / 竖排文本块不支持直接编辑。旋转页面至文字水平后再试。`;
    else $('text-status').textContent = `检测到 ${data.blocks.length} 个文本块。点击选中，或切换为“单行文字”。`;
    resizePage(); renderTextHits(); renderImageHits(); updateActiveThumb(src);
    if (data.warnings.length) toast(data.warnings.join('\n'), 'warning');
  } catch (error) {
    if (serial === previewSerial) {
      S.data = null; $('page-image').removeAttribute('src');
      toast(error.message, 'error', 7000);
    }
  } finally { busyEnd(); }
}
function resizePage() {
  if (!S.data) return;
  const scroll = $('canvas-scroll');
  const style = getComputedStyle(scroll);
  const available = Math.max(100, scroll.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight));
  const scale = S.zoom === 'fit' ? Math.min(1.6, available/S.data.width) : Number(S.zoom);
  $('page-stage').style.width = `${S.data.width*scale}px`;
  $('page-stage').style.height = `${S.data.height*scale}px`;
  $('page-container').style.width = `${S.data.width*scale}px`;
  positionSelection();
}
new ResizeObserver(resizePage).observe($('canvas-scroll'));
function updateActiveThumb(src) {
  const card = [...document.querySelectorAll('.page-card')].find(el=>el.dataset.id===S.active);
  if (!card) return;
  const image = document.createElement('img'); image.src = src; image.alt = '页面缩略图';
  card.querySelector('.thumb-frame').replaceChildren(image);
}
function renderThumbnails() {
  thumbnailObserver?.disconnect(); thumbQueue = [];
  const list = $('page-list'), oldScroll = list.scrollTop;
  list.replaceChildren();
  if (!S.pages.length) {
    const empty = document.createElement('div'); empty.className='sidebar-empty';
    empty.innerHTML = '<span data-icon="pages"></span><p>文档的每一页<br>都会出现在这里</p>';
    list.append(empty); mountIcons(empty); return;
  }
  thumbnailObserver = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      const card = entry.target; thumbnailObserver.unobserve(card);
      const page = S.pages.find(p=>p.id===card.dataset.id);
      if (page && !thumbnails.has(signature(page))) thumbQueue.push({page:clone(page),card});
    }
    void runThumbnailQueue();
  }, { root:list, rootMargin:'250px' });
  S.pages.forEach((page, index) => {
    const card=document.createElement('div'); card.className=`page-card${page.id===S.active?' active':''}`;
    card.dataset.id=page.id; card.draggable=true; card.tabIndex=0; card.setAttribute('role','button');
    card.setAttribute('aria-label',`第 ${index+1} 页，点击编辑，可拖动排序`);
    const checkbox=document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=S.selected.has(page.id);
    checkbox.setAttribute('aria-label',`勾选第 ${index+1} 页`);
    checkbox.addEventListener('click', event=>event.stopPropagation());
    checkbox.addEventListener('change',()=>{
      if (checkbox.checked) S.selected.add(page.id); else S.selected.delete(page.id); updateControls();
    });
    const frame=document.createElement('div'); frame.className='thumb-frame';
    const [w,h]=dimensions(page); frame.style.aspectRatio=`${w}/${h}`;
    const cached=thumbnails.get(signature(page));
    if (cached) { const img=document.createElement('img'); img.src=cached; img.alt=`第 ${index+1} 页缩略图`; frame.append(img); }
    else { const place=document.createElement('span'); place.className='thumb-placeholder'; place.textContent=String(index+1).padStart(2,'0'); frame.append(place); }
    const info=document.createElement('div'); info.className='page-card-info';
    const number=document.createElement('span'); number.textContent=String(index+1).padStart(2,'0');
    if (page.ops.length) { const dot=document.createElement('i');dot.className='modified';number.prepend(dot); }
    const label=document.createElement('small'); label.textContent=page.source?`原第 ${page.index+1} 页`:'空白页';
    info.append(number,label); card.append(checkbox,frame,info); list.append(card);
    card.addEventListener('click',()=>selectPage(page.id));
    card.addEventListener('keydown',event=>{ if (event.target===card && ['Enter',' '].includes(event.key)) { event.preventDefault();selectPage(page.id); } });
    card.addEventListener('dragstart',event=>{
      if (S.busy || hasPendingDraftChanges()) { event.preventDefault(); if(hasPendingDraftChanges()) toast('请先应用或取消当前修改。','warning'); return; }
      if(S.draft)clearDraft();
      dragId=page.id; event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',page.id);card.classList.add('dragging');
    });
    card.addEventListener('dragover',event=>{ if(dragId){event.preventDefault();card.classList.add('drag-over');} });
    card.addEventListener('dragleave',()=>card.classList.remove('drag-over'));
    card.addEventListener('drop',event=>{
      if (!dragId) return;
      event.preventDefault();event.stopPropagation();card.classList.remove('drag-over');
      const from=S.pages.findIndex(p=>p.id===dragId),to=S.pages.findIndex(p=>p.id===page.id);
      if(from<0 || to<0 || from===to)return;
      const pages=clone(S.pages),[moved]=pages.splice(from,1);pages.splice(to,0,moved);
      dragId=null;commit(pages,S.active,'页序已更新');
    });
    card.addEventListener('dragend',()=>{dragId=null;card.classList.remove('dragging');document.querySelectorAll('.drag-over').forEach(e=>e.classList.remove('drag-over'));});
    if (!cached) thumbnailObserver.observe(card);
  });
  list.scrollTop=oldScroll;
}
async function runThumbnailQueue() {
  if (thumbWorking) return; thumbWorking=true;
  try {
    while(thumbQueue.length) {
      const {page,card}=thumbQueue.shift();
      if(!card.isConnected)continue;
      try {
        const data=await getPreview(page, Math.min(.35,170/dimensions(page)[0]),false);
        if(!card.isConnected || signature(S.pages.find(p=>p.id===page.id))!==signature(page))continue;
        const img=document.createElement('img');img.src=`data:image/png;base64,${data.image}`;img.alt='页面缩略图';
        card.querySelector('.thumb-frame').replaceChildren(img);
      } catch (_) { /* Active-page errors are shown explicitly; thumbnail errors are nonfatal. */ }
    }
  } finally { thumbWorking=false; }
}
function selectPage(id) {
  if(S.busy || id===S.active || !discardDraft())return;
  S.active=id;persist();refresh();
}
function updateTool() {
  const hints={select:'点击文字块修改；右侧可切换为单行选择。',text:'在页面上拖出一个文本框，再在右侧输入文字。',image:'点击图片，上传替换图片，再应用。仅替换选中的这一处。',crop:'拖出需要保留的区域；可调整坐标或批量应用。',cover:'拖出遮盖区域。注意：底层内容不会被删除。',redact:'拖出要移除内容的区域；应用后请检查相邻文字和图形。'};
  $('tool-hint').textContent=S.pages.length?hints[S.tool]:'打开一个 PDF，或从空白页开始。';
  document.querySelectorAll('[data-tool]').forEach(el=>el.classList.toggle('active',el.dataset.tool===S.tool));
  $('overlay').classList.toggle('draw-mode',!['select','image'].includes(S.tool));
  $('image-guide').hidden=S.tool!=='image';
  $('guide-panel').querySelector('.guide-card').hidden=S.tool==='image';
  $('text-granularity').closest('.field').hidden=S.tool==='image';
  $('text-status').hidden=S.tool==='image';
  renderTextHits();renderImageHits();
}
function setTool(tool) {
  if(S.busy || tool===S.tool || !discardDraft())return;
  S.tool=tool;updateTool();
}
function applyPercentBox(el, rect) {
  if(!S.data)return;
  el.style.left=`${rect[0]/S.data.width*100}%`;el.style.top=`${rect[1]/S.data.height*100}%`;
  el.style.width=`${(rect[2]-rect[0])/S.data.width*100}%`;el.style.height=`${(rect[3]-rect[1])/S.data.height*100}%`;
}
function renderTextHits() {
  const root=$('text-hits');root.replaceChildren();
  if(S.tool!=='select' || !S.data || S.displayPage!==S.active)return;
  const regions=S.data[$('text-granularity').value] || [];
  for(const region of regions) {
    const hit=document.createElement('div');hit.className='text-hit';hit.tabIndex=0;hit.setAttribute('role','button');
    hit.title=region.text.slice(0,180);hit.setAttribute('aria-label',`编辑：${region.text.slice(0,80)}`);
    applyPercentBox(hit,region.rect);
    hit.addEventListener('click',event=>{event.stopPropagation();chooseText(region);});
    hit.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();chooseText(region);}});
    root.append(hit);
  }
}
function renderImageHits() {
  const root=$('image-hits'),list=$('image-list');root.replaceChildren();list.replaceChildren();
  const regions=S.data && S.displayPage===S.active ? S.data.images||[] : [];
  $('image-count').textContent=regions.length;
  if(S.tool!=='image')return;
  if(!regions.length) {
    const empty=document.createElement('p');empty.className='note muted';
    empty.textContent='本页没有可识别的图片。矢量图形不属于图片对象。';list.append(empty);
  }
  for(const region of [...regions].sort((a,b)=>(b.rect[2]-b.rect[0])*(b.rect[3]-b.rect[1])-(a.rect[2]-a.rect[0])*(a.rect[3]-a.rect[1]))) {
    const hit=document.createElement('button');hit.className='image-hit';
    hit.setAttribute('aria-label',`选择图片 ${region.index+1}`);hit.title=`图片 ${region.index+1} · ${region.width} × ${region.height}`;
    if(!region.editable)hit.classList.add('unsupported');
    applyPercentBox(hit,region.rect);hit.onclick=event=>{event.stopPropagation();chooseImage(region);};root.append(hit);
  }
  for(const region of regions) {
    const button=document.createElement('button');button.className='image-list-item';
    const icon=document.createElement('span');icon.dataset.icon='image';
    const body=document.createElement('span'),title=document.createElement('strong'),meta=document.createElement('small');
    title.textContent=`图片 ${region.index+1}${region.full_page?' · 整页图像':''}`;
    meta.textContent=region.editable?`${region.width} × ${region.height} 像素`:region.reason;
    body.append(title,meta);button.append(icon,body);button.onclick=()=>chooseImage(region);list.append(button);
  }
  mountIcons(list);
}
function chooseImage(region) {
  if(S.busy)return;
  if(!region.editable){toast(region.reason||'暂不支持替换这张图片。','warning');return;}
  if(S.draft?.kind==='image_replace' && S.draft.image_index===region.index && S.draft.digest===region.digest)return;
  if(!discardDraft())return;
  setDraft({kind:'image_replace',rect:[...region.rect],bbox:[...region.bbox],image_index:region.index,digest:region.digest,asset:null,fit:'contain'});
  $('edit-note').textContent=`已选中图片 ${region.index+1}（${region.width} × ${region.height} 像素）。${region.full_page?'这是一张整页图像，替换会改变整页扫描内容。':'选择新图片后，点击“应用修改”查看结果。'}`;
}
async function uploadReplacement(file) {
  const draft=S.draft;
  if(!file || draft?.kind!=='image_replace' || S.busy)return;
  if(file.size>20*1024*1024){toast('图片不能超过 20 MB。','error');return;}
  await mutate('正在读取替换图片…',async()=>{
    const form=new FormData();form.append('file',file);
    const response=await api('/images',{method:'POST',body:form}),data=await response.json();
    if(S.draft!==draft)return;
    draft.asset=data.asset;
    $('replacement-preview').src=`data:image/png;base64,${data.preview}`;$('replacement-preview').hidden=false;
    $('replacement-placeholder').hidden=true;
    $('replacement-file-label').textContent=`${file.name} · ${data.width} × ${data.height} 像素`;
    updateDraftState();
  });
}
function defaultStyle() {return {size:14,color:'#172033',family:'sans-serif',bold:false,italic:false,align:'left',line_height:1.25,fit:true,background:null};}
function chooseText(region) {
  // Clicking the same selected source must not reset a draft being edited.
  if(S.draft?.kind==='replace' && signature(S.draft.erase)===signature(region.erase))return;
  if(S.busy || !discardDraft())return;
  const rect=[...region.rect];
  // The engine measures glyphs separately from HTML leading. Keep the exact
  // destination stable across edits and preserve the detected paragraph spacing.
  setDraft({kind:'replace',rect,text:region.text,font:region.font,erase:clone(region.erase),style:{...defaultStyle(),size:Math.max(4,Math.min(200,region.size)),line_height:region.line_height||1.25,color:region.color,bold:region.bold,italic:region.italic,family:'original'}});
  $('edit-note').textContent=`原字体：${region.font}。${region.mixed?'此区域有混合样式，修改后将统一样式。':''}优先复用原字体；缺少字形或字体不可用时会提示回退。替换会移除所选原文字。`;
}
function setDraft(draft) {
  S.draft=draft;
  $('draft-actions').hidden=false;
  $('guide-panel').hidden=true;$('draft-panel').hidden=false;$('draft-badge').hidden=false;
  const text=['text','replace'].includes(draft.kind);
  $('properties-title').textContent={text:'添加文字',replace:'修改文字',image_replace:'替换图片',crop:'裁剪页面',cover:'视觉遮盖',redact:'真正删除内容'}[draft.kind];
  $('text-fields').hidden=!text;$('area-color-field').hidden=!['cover','redact'].includes(draft.kind);
  $('image-fields').hidden=draft.kind!=='image_replace';$('geometry-fields').hidden=draft.kind==='image_replace';
  $('crop-options').hidden=draft.kind!=='crop';$('remove-text').hidden=draft.kind!=='replace';
  $('crop-all').checked=false;
  $('edit-note').textContent={text:'输入新文字，调整文本框位置和样式后应用。',replace:'移除原文字后重新排版。新文字会使用所选替代字体。',crop:'选框内部是保留区域，框外区域会被隐藏。',cover:'仅绘制覆盖层，不删除底层文字或图片。不能用作隐私脱敏。',redact:'移除选区内的文字和图像像素，并移除与选区接触的矢量路径。较大的跨区图形可能一并消失。'}[draft.kind];
  $('edit-note').className=`note${['redact','cover'].includes(draft.kind)?' warning':''}`;
  if(text) {
    const style=draft.style;
    $('font-family').querySelector('[value="original"]').disabled=draft.kind!=='replace';
    $('text-content').value=draft.text;$('font-size').value=style.size;$('line-height').value=style.line_height;
    $('font-family').value=style.family;$('font-bold').checked=style.bold;$('font-italic').checked=style.italic;
    $('text-align').value=style.align;$('text-color').value=style.color;$('text-fit').checked=style.fit;
    $('text-background').checked=!!style.background;$('background-color').value=style.background||'#ffffff';
  } else if(draft.kind==='image_replace') {
    $('image-fit').value=draft.fit;
    $('replacement-preview').removeAttribute('src');$('replacement-preview').hidden=true;$('replacement-placeholder').hidden=false;
    $('replacement-file-label').textContent='PNG、JPEG、WebP · 最大 20 MB';
  } else $('area-color').value=draft.color||'#ffffff';
  updateRectInputs();positionSelection();
  S.draftBaseline=draftFingerprint();
  updateDraftState();
}
function clearDraft() {
  S.draft=null;S.draftBaseline=null;S.drawing=null;
  $('draft-actions').hidden=true;
  $('draft-panel').hidden=true;$('guide-panel').hidden=false;$('draft-badge').hidden=true;
  $('properties-title').textContent='编辑面板';$('selection-box').hidden=true;
}
function round(n){return Math.round(n*100)/100;}
function updateRectInputs() {
  if(!S.draft)return;
  const [x,y,x1,y1]=S.draft.rect;
  $('rect-x').value=round(x);$('rect-y').value=round(y);$('rect-w').value=round(x1-x);$('rect-h').value=round(y1-y);
  updateDraftState();
}
function positionSelection() {
  const box=$('selection-box');
  if(!S.draft || !S.data){box.hidden=true;return;}
  box.hidden=false;box.className=`selection-box ${S.draft.kind}`;applyPercentBox(box,S.draft.rect);
  $('selection-caption').textContent={replace:'替换文字 · 拖动移动',text:'新文本框',image_replace:'已选中图片 · 右侧上传替换',crop:'保留此区域',cover:'视觉遮盖',redact:'删除此区域内容'}[S.draft.kind];
}
function readRectInputs() {
  if(!S.draft || !S.data)return;
  const nums=['rect-x','rect-y','rect-w','rect-h'].map(id=>Number($(id).value));
  if(!nums.every(Number.isFinite))return;
  let [x,y,w,h]=nums;
  x=Math.max(0,Math.min(x,S.data.width-1));y=Math.max(0,Math.min(y,S.data.height-1));
  w=Math.max(1,Math.min(w,S.data.width-x));h=Math.max(1,Math.min(h,S.data.height-y));
  S.draft.rect=[x,y,x+w,y+h];positionSelection();
}
function formDraft() {
  if(S.draft.kind==='image_replace') {
    const {kind,image_index,bbox,digest,asset}=S.draft;
    if(!asset)throw new Error('请先选择替换图片。');
    return {kind,image_index,bbox,digest,asset,fit:$('image-fit').value};
  }
  readRectInputs();
  const op=clone(S.draft);
  if(['text','replace'].includes(op.kind)) {
    op.text=$('text-content').value;
    const size=Number($('font-size').value),height=Number($('line-height').value);
    if(!Number.isFinite(size)||size<4||size>200)throw new Error('字号需要在 4–200 pt 之间。');
    if(!Number.isFinite(height)||height<.85||height>3)throw new Error('行距倍数需要在 0.85–3 之间。');
    op.style={size,color:$('text-color').value,family:$('font-family').value,bold:$('font-bold').checked,italic:$('font-italic').checked,align:$('text-align').value,line_height:height,fit:$('text-fit').checked,background:$('text-background').checked?$('background-color').value:null};
  } else if(op.kind!=='crop')op.color=$('area-color').value;
  return op;
}
function point(event) {
  const r=$('overlay').getBoundingClientRect();
  return [Math.max(0,Math.min(S.data.width,(event.clientX-r.left)/r.width*S.data.width)),Math.max(0,Math.min(S.data.height,(event.clientY-r.top)/r.height*S.data.height))];
}
$('overlay').addEventListener('pointerdown',event=>{
  if(event.button!==0 || S.busy || !S.data || S.displayPage!==S.active)return;
  const hit=event.target.closest('.text-hit,.image-hit'), selection=event.target.closest('#selection-box');
  if(hit && !selection)return;
  const p=point(event);
  if(selection && S.draft) {
    if(S.draft.kind==='image_replace')return;
    S.drawing={mode:event.target.dataset.resize?'resize':'move',start:p,rect:[...S.draft.rect]};
  } else if(!['select','image'].includes(S.tool)) {
    if(S.draft && !discardDraft())return;
    const kind=S.tool;
    const draft={kind,rect:[p[0],p[1],p[0]+.01,p[1]+.01]};
    if(kind==='text')Object.assign(draft,{text:'',erase:[],style:defaultStyle()});
    else draft.color=kind==='redact'?'#000000':'#ffffff';
    setDraft(draft);S.drawing={mode:'draw',start:p};
  } else {if(S.draft)discardDraft();return;}
  event.preventDefault();$('overlay').setPointerCapture(event.pointerId);
});
$('overlay').addEventListener('pointermove',event=>{
  if(!S.drawing || !S.draft)return;
  const p=point(event),drag=S.drawing,[sx,sy]=drag.start;
  if(drag.mode==='draw')S.draft.rect=[Math.min(sx,p[0]),Math.min(sy,p[1]),Math.max(sx,p[0]),Math.max(sy,p[1])];
  else if(drag.mode==='move') {
    const [x,y,x1,y1]=drag.rect,w=x1-x,h=y1-y;
    const nx=Math.max(0,Math.min(S.data.width-w,x+p[0]-sx)),ny=Math.max(0,Math.min(S.data.height-h,y+p[1]-sy));
    S.draft.rect=[nx,ny,nx+w,ny+h];
  } else {
    const [x,y,x1,y1]=drag.rect;
    S.draft.rect=[x,y,Math.max(x+1,Math.min(S.data.width,x1+p[0]-sx)),Math.max(y+1,Math.min(S.data.height,y1+p[1]-sy))];
  }
  updateRectInputs();positionSelection();
});
function endPointer(event) {
  if(!S.drawing)return;
  const mode=S.drawing.mode;S.drawing=null;
  if($('overlay').hasPointerCapture(event.pointerId))$('overlay').releasePointerCapture(event.pointerId);
  if(!S.draft)return;
  const [x,y,x1,y1]=S.draft.rect;
  if(mode==='draw' && (x1-x<4 || y1-y<4)) {
    if(S.draft.kind==='text') {
      const nx=Math.min(x,S.data.width-60),ny=Math.min(y,S.data.height-40);
      S.draft.rect=[Math.max(0,nx),Math.max(0,ny),Math.min(S.data.width,nx+220),Math.min(S.data.height,ny+80)];
    } else {clearDraft();toast('请拖出一个更大的区域。');return;}
  }
  updateRectInputs();positionSelection();
  if(mode==='draw' && S.draft.kind==='text')$('text-content').focus();
}
$('overlay').addEventListener('pointerup',endPointer);
$('overlay').addEventListener('pointercancel',endPointer);

async function applyEdit(deleteOnly=false) {
  if(!S.draft || S.busy)return;
  let op;
  try {op=formDraft();if(deleteOnly)op.text='';} catch(error){toast(error.message,'error');return;}
  if(op.kind==='text' && !op.text.trim()){toast('请先输入要添加的文字。','warning');return;}
  if(op.kind==='redact' && !confirm('此操作会真正移除选区内内容，且可能影响跨越选区的矢量图形。是否应用？（导出前仍可撤销。）'))return;
  const allCrop=op.kind==='crop'&&$('crop-all').checked;
  await mutate(allCrop?'正在校验批量裁剪…':'正在应用并检查排版…',async()=>{
    const pages=clone(S.pages),current=pages.find(p=>p.id===S.active);
    if(allCrop) {
      const ratios=op.rect.map((n,i)=>n/(i%2?S.data.height:S.data.width));
      for(const page of pages) {
        const [w,h]=dimensions(page),rect=ratios.map((n,i)=>n*(i%2?h:w));
        if(Math.min(rect[2]-rect[0],rect[3]-rect[1])<8)throw new Error('部分页面裁剪后小于 8 pt，请扩大保留区域。');
        page.ops.push({...op,rect});
      }
    } else current.ops.push(op);
    // Validate before committing, including text overflow. On failure, keep the
    // draft and the original document unchanged, rather than committing a no-op.
    await getPreview(current);
    const action={text:'添加文字',replace:deleteOnly?'删除原文字':'修改文字',image_replace:'替换图片',crop:'裁剪页面',cover:'添加遮盖',redact:'真正删除内容'}[op.kind];
    commit(pages,S.active,allCrop?'裁剪所有页面':`第 ${pages.indexOf(current)+1} 页 · ${action}`);
  });
}
async function pageOperation(op,label) {
  if(!activePage() || S.busy || !discardDraft())return;
  await mutate(label,async()=>{
    const pages=clone(S.pages),page=pages.find(p=>p.id===S.active);page.ops.push(op);
    await getPreview(page);commit(pages,S.active,label);
  });
}
function movePage(delta) {
  if(S.busy || !discardDraft())return;
  const from=S.pages.findIndex(p=>p.id===S.active),to=from+delta;
  if(from<0||to<0||to>=S.pages.length)return;
  const pages=clone(S.pages),[page]=pages.splice(from,1);pages.splice(to,0,page);commit(pages,S.active,'当前页面已移动');
}
function deletePages() {
  if(!S.pages.length||S.busy||!discardDraft())return;
  const ids=S.selected.size?S.selected:new Set([S.active]);
  if(!confirm(`删除 ${ids.size} 页？导出前可以撤销。`))return;
  const old=S.pages.findIndex(p=>p.id===S.active),pages=S.pages.filter(p=>!ids.has(p.id));
  const active=pages.some(p=>p.id===S.active)?S.active:pages[Math.min(old,pages.length-1)]?.id||null;
  commit(clone(pages),active,`已删除 ${ids.size} 页`);
}
function newBlank() {
  if(S.busy||!discardDraft())return;
  if(S.pages.length>=300){toast('最多支持 300 页。','warning');return;}
  const page={id:uid(),source:null,index:0,width:595,height:842,label:'A4 空白页',ops:[]};
  const pages=clone(S.pages),index=S.pages.findIndex(p=>p.id===S.active)+1;pages.splice(index,0,page);
  commit(pages,page.id,'已插入空白页');
}
function parseRange(text,count) {
  if(!text.trim())return Array.from({length:count},(_,i)=>i);
  const result=[];
  for(const part of text.replaceAll('，',',').replace(/[–—]/g,'-').split(',')) {
    const match=part.trim().match(/^(\d+)(?:\s*-\s*(\d+))?$/);
    if(!match)throw new Error('页码格式错误，请使用 1-3,5 这样的格式。');
    const start=Number(match[1]),end=Number(match[2]||match[1]);
    if(start<1||end<1||start>count||end>count)throw new Error(`页码必须在 1–${count} 之间。`);
    const step=end>=start?1:-1;
    for(let p=start;step>0?p<=end:p>=end;p+=step){result.push(p-1);if(result.length>300)throw new Error('一次最多导出 300 页。');}
  }
  return result;
}
function reorderPages() {
  if(S.busy||!S.pages.length||!discardDraft())return;
  const input=prompt(`输入新的完整页序，例如 3,1-2。当前共 ${S.pages.length} 页，每页必须恰好出现一次。`,S.pages.map((_,i)=>i+1).join(','));
  if(input===null)return;
  try {
    const indexes=parseRange(input,S.pages.length);
    if(indexes.length!==S.pages.length||new Set(indexes).size!==S.pages.length)throw new Error('重排必须包含全部页面，且每页只能出现一次。删除页面请使用删除按钮。');
    commit(indexes.map(i=>clone(S.pages[i])),S.active,'页序已更新');
  } catch(error){toast(error.message,'error');}
}
function openPicker(mode) {
  if(S.busy)return;uploadMode=mode;$('file-input').value='';$('file-input').click();
}
async function importFiles(files,mode='append') {
  if(!files.length||S.busy||!discardDraft())return;
  if(mode==='replace'&&S.pages.length&&!confirm('打开新文档将替换当前页面列表。可通过撤销返回；建议先导出当前文档。'))return;
  await mutate('正在导入 PDF…',async()=>{
    const imported=[];let name=S.name,notice='';const errors=[];
    for(let i=0;i<files.length;i++) {
      const file=files[i];$('busy-message').textContent=`正在导入 ${i+1} / ${files.length}…`;
      if(file.size>50*1024*1024){errors.push(`${file.name}：超过 50 MB。`);continue;}
      try {
        const form=new FormData();form.append('file',file);
        const response=await api('/import',{method:'POST',body:form});const data=await response.json();
        if(imported.length===0&&(mode==='replace'||!S.pages.length))name=data.filename;
        const pages=data.pages.map((p,index)=>({id:uid(),source:data.source,index,width:p.width,height:p.height,label:`${data.filename} · 原第 ${index+1} 页`,ops:[]}));
        const count=(mode==='replace'?0:S.pages.length)+imported.length+pages.length;
        if(count>300){errors.push(`${file.name}：加入后超过 300 页，未加入页面列表。`);continue;}
        imported.push(...pages);notice=data.notice;
      } catch(error){errors.push(`${file.name}：${error.message}`);}
    }
    if(imported.length) {
      const pages=mode==='replace'?imported:[...clone(S.pages),...imported];
      commit(pages,imported[0].id,`已导入 ${imported.length} 页`,name);
      toast(notice,'info',6500);
    }
    if(errors.length)toast(errors.join('\n'),'error',10000);
  });
}
async function loadDemo() {
  if(S.busy)return;
  try{const response=await fetch('/demo.pdf');if(!response.ok)throw new Error('示例文件不存在。');const blob=await response.blob();await importFiles([new File([blob],'示例项目简报.pdf',{type:'application/pdf'})],S.pages.length?'append':'replace');}
  catch(error){toast(error.message,'error');}
}
function openExport() {
  if(!S.pages.length||S.busy)return;
  if(hasPendingDraftChanges()){toast('当前有尚未应用的修改，请先应用或取消，再导出。','warning');return;}
  if(S.draft)clearDraft();
  $('export-name').value=S.name.replace(/\.pdf$/i,'')+'-edited.pdf';$('export-range').value='';
  $('export-dialog').showModal();
}
async function exportDocument() {
  if(S.busy)return;
  let indexes;
  try{indexes=parseRange($('export-range').value,S.pages.length);}catch(error){toast(error.message,'error');return;}
  const mode=document.querySelector('input[name="export-mode"]:checked').value;
  const filename=$('export-name').value.trim()||'edited.pdf';
  $('confirm-export').disabled=true;
  await mutate(mode==='raster'?'正在生成图像化 PDF…':'正在生成 PDF…',async()=>{
    const response=await post('/export',{pages:indexes.map(i=>S.pages[i]),mode,dpi:Number($('export-dpi').value),filename});
    const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=filename.toLowerCase().endsWith('.pdf')?filename:filename+'.pdf';document.body.append(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),60000);
    $('export-dialog').close();
    const full=indexes.length===S.pages.length&&indexes.every((value,i)=>value===i);
    if(full){S.dirty=false;persist();}
    setStatus(`已导出 ${indexes.length} 页 · ${(blob.size/1024/1024).toFixed(2)} MB`);
    toast(mode==='raster'?'图像化 PDF 已生成。请逐页检查可见内容；会话中的源文件尚未清除。':'PDF 已生成。普通遮盖和裁剪不等于移除隐藏内容。','info',6500);
  });
  $('confirm-export').disabled=false;
}
async function clearSession() {
  if(S.busy)return;
  if(!confirm('清空当前页面、源文件和全部撤销记录？请先导出需要保留的内容。'))return;
  await mutate('正在结束会话…',async()=>{
    await api('/session',{method:'DELETE'});
    S.token='';S.pages=[];S.active=null;S.selected.clear();S.dirty=false;S.name='未命名文档.pdf';resetHistory();
    previews.clear();thumbnails.clear();clearDraft();sessionStorage.removeItem(CURRENT_KEY);sessionStorage.removeItem(TOKEN_KEY);
    const response=await api('/session');const data=await response.json();S.token=data.token;persist();refresh();setStatus('会话已清空');
  });
}

// Event wiring. User strings are assigned through textContent, never HTML.
$('btn-open').onclick=()=>openPicker('replace');$('btn-append').onclick=()=>openPicker('append');
$('file-input').onchange=event=>importFiles([...event.target.files],uploadMode);
$('drop-zone').onclick=()=>openPicker('replace');$('drop-zone').onkeydown=event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();openPicker('replace');}};
$('btn-demo').onclick=loadDemo;$('btn-start-blank').onclick=newBlank;$('btn-blank').onclick=newBlank;
$('undo').onclick=()=>historyStep(false);$('redo').onclick=()=>historyStep(true);
$('btn-history').onclick=()=>showSidebar(S.sidebar==='history'?'pages':'history');
$('tab-pages').onclick=()=>showSidebar('pages');$('tab-history').onclick=()=>showSidebar('history');
$('choose-image-file').onclick=()=>{if(!S.busy)$('image-file-input').click();};
$('image-file-input').onchange=event=>{const file=event.target.files[0];event.target.value='';void uploadReplacement(file);};
$('page-up').onclick=()=>movePage(-1);$('page-down').onclick=()=>movePage(1);$('page-delete').onclick=deletePages;
$('page-duplicate').onclick=()=>{
  if(S.busy||!activePage()||!discardDraft())return;
  if(S.pages.length>=300){toast('最多支持 300 页。','warning');return;}
  const page=clone(activePage());page.id=uid();page.label+=' · 副本';page.label=page.label.slice(0,250);
  const pages=clone(S.pages),index=pages.findIndex(p=>p.id===S.active);pages.splice(index+1,0,page);commit(pages,page.id,'页面已复制');
};
$('btn-reorder').onclick=reorderPages;$('rotate').onclick=()=>pageOperation({kind:'rotate',angle:90},'页面已顺时针旋转');
$('reset-page').onclick=()=>{
  if(S.busy||!activePage()?.ops.length||!discardDraft())return;
  if(!confirm('重置当前页的全部文字、裁剪、旋转和内容移除操作？可以撤销此重置。'))return;
  const pages=clone(S.pages);pages.find(p=>p.id===S.active).ops=[];commit(pages,S.active,'当前页编辑已重置');
};
$('select-all').onchange=event=>{S.selected=event.target.checked?new Set(S.pages.map(p=>p.id)):new Set();renderThumbnails();updateControls();};
$('text-granularity').onchange=()=>{if(discardDraft())renderTextHits();};
$('zoom').onchange=event=>{S.zoom=event.target.value;resizePage();};
$('apply-edit').onclick=()=>applyEdit();$('cancel-edit').onclick=clearDraft;$('remove-text').onclick=()=>applyEdit(true);
for(const id of ['rect-x','rect-y','rect-w','rect-h'])$(id).addEventListener('input',readRectInputs);
$('draft-panel').addEventListener('input',updateDraftState);
$('draft-panel').addEventListener('change',updateDraftState);
for(const el of document.querySelectorAll('[data-tool]'))el.onclick=()=>setTool(el.dataset.tool);
$('btn-help').onclick=()=>$('help-dialog').showModal();$('btn-export').onclick=openExport;$('confirm-export').onclick=exportDocument;
$('btn-clear').onclick=clearSession;
for(const el of document.querySelectorAll('[data-close-dialog]'))el.onclick=()=>$(el.dataset.closeDialog).close();
for(const el of document.querySelectorAll('input[name="export-mode"]'))el.onchange=()=>{$('dpi-field').hidden=document.querySelector('input[name="export-mode"]:checked').value!=='raster';};
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});

$('page-list').addEventListener('dragover',event=>{
  if(!dragId)return;
  event.preventDefault();
  const list=$('page-list'),rect=list.getBoundingClientRect();
  if(event.clientY<rect.top+40)list.scrollTop-=18;
  else if(event.clientY>rect.bottom-40)list.scrollTop+=18;
});
let dragDepth=0;
document.addEventListener('dragenter',event=>{if([...event.dataTransfer.types].includes('Files')){event.preventDefault();dragDepth++;document.body.classList.add('drag-upload');}});
document.addEventListener('dragleave',()=>{dragDepth=Math.max(0,dragDepth-1);if(!dragDepth)document.body.classList.remove('drag-upload');});
document.addEventListener('dragover',event=>{if([...event.dataTransfer.types].includes('Files'))event.preventDefault();});
document.addEventListener('drop',event=>{
  document.body.classList.remove('drag-upload');dragDepth=0;
  if(event.dataTransfer.files.length){event.preventDefault();void importFiles([...event.dataTransfer.files],S.pages.length?'append':'replace');}
});
document.addEventListener('keydown',event=>{
  if(document.querySelector('dialog[open]'))return;
  const typing=event.target.matches('input,textarea,select,[contenteditable="true"]');
  if(event.key==='Escape'){clearDraft();return;}
  if(typing||S.busy)return;
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();historyStep(event.shiftKey);}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='y'){event.preventDefault();historyStep(true);}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();openExport();}
  if(event.key==='Delete'||event.key==='Backspace'){event.preventDefault();if(!S.draft)deletePages();}
  if(event.key==='PageDown'||event.key==='PageUp'){
    const index=S.pages.findIndex(p=>p.id===S.active)+(event.key==='PageDown'?1:-1);
    if(S.pages[index]){event.preventDefault();selectPage(S.pages[index].id);}
  }
});
window.addEventListener('beforeunload',event=>{if(S.dirty||hasPendingDraftChanges()){event.preventDefault();event.returnValue='';}});

async function init() {
  try {
    S.token=sessionStorage.getItem(TOKEN_KEY)||'';
    const response=await api('/session'),data=await response.json();S.token=data.token;
    let saved=null;try{saved=JSON.parse(sessionStorage.getItem(CURRENT_KEY)||'null');}catch(_){}
    const validPage=p=>p && Array.isArray(p.ops) && (!p.source||data.sources.includes(p.source)) && p.ops.every(op=>op.kind!=='image_replace'||(data.images||[]).includes(op.asset));
    if(Array.isArray(saved?.pages) && saved.pages.every(validPage)) {
      S.pages=saved.pages;S.active=saved.pages.some(p=>p.id===saved.active)?saved.active:saved.pages[0]?.id||null;S.name=saved.name||S.name;S.dirty=!!saved.dirty;
      const restored=unpackHistory(saved,validPage);
      if(!restored)resetHistory('已恢复文档');
      toast(restored?'已恢复当前标签页的文档和编辑历史。':'已恢复当前文档；之前的历史不可恢复，已从当前状态继续。');
    } else {
      resetHistory();
      if(saved?.pages?.length)toast('之前的文件会话已过期或服务已重启，请重新导入 PDF。','warning',6500);
    }
    persist();$('connection-dot').classList.add('connected');setStatus('已连接本地服务 · 就绪');refresh();
  } catch(error){toast(`连接失败：${error.message}。请确认 Python 服务正在运行。`,'error',12000);setStatus('未连接，请检查服务并刷新网页');}
}
void init();
