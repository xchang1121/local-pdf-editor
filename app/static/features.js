/* Search, native PDF annotations, and portable project files.
 * Loaded before app.js; functions resolve the editor state when invoked.
 */
'use strict';
const F={search:null,scrollSearch:false};
function hasUnsavedChanges(){return S.projectMode?S.projectDirty:S.dirty;}
function updateFeatureControls(){
  $('btn-save-project').disabled=S.busy||!S.token;
  $('btn-open-project').disabled=S.busy||!S.token;
  $('run-search').disabled=S.busy||!S.pages.length;
  const results=F.search?.matches||[];
  $('replace-one').disabled=S.busy||!results.length;
  $('replace-all').disabled=S.busy||!results.length||F.search.limited;
  for(const button of document.querySelectorAll('.search-result,.annotation-list-item'))button.disabled=S.busy;
}
function openSearch(){
  showSidebar('search');$('search-query').focus();$('search-query').select();
}
function searchRequest(){
  return {pages:S.pages,query:$('search-query').value,case_sensitive:$('search-case').checked,
    page_ids:$('search-scope').value==='page'?[S.active]:null};
}
function invalidateSearch(){
  F.search=null;F.scrollSearch=false;renderSearch();renderSearchHits();
}
function renderSearch(){
  const list=$('search-results');list.replaceChildren();
  if(!F.search){$('search-summary').textContent='输入关键词后查找；修改文档后需重新查找。';updateFeatureControls();return;}
  const {matches,limited,no_text}=F.search;
  $('search-summary').textContent=limited?'超过 2000 处匹配，请缩小查找范围。':`找到 ${matches.length} 处${no_text.length?` · ${no_text.length} 页无文字层`:''}`;
  matches.forEach((match,index)=>{
    const row=document.createElement('li'),button=document.createElement('button');
    button.className=`search-result${index===F.search.index?' current':''}`;
    button.setAttribute('aria-current',index===F.search.index?'true':'false');
    const label=document.createElement('small');label.textContent=`第 ${match.page_number} 页 · ${index+1}${match.mixed?' · 混合样式':''}`;
    const context=document.createElement('span'),mark=document.createElement('mark');mark.textContent=match.text;
    context.append(document.createTextNode(match.before),mark,document.createTextNode(match.after));
    button.append(label,context);button.onclick=()=>selectSearchResult(index);row.append(button);list.append(row);
  });
  updateFeatureControls();
}
function renderSearchHits(){
  const root=$('search-hits');root.replaceChildren();
  if(!F.search||!S.data||S.displayPage!==S.active)return;
  F.search.matches.forEach((match,index)=>{
    if(match.page_id!==S.active)return;
    const hit=document.createElement('span');hit.className=`search-hit${index===F.search.index?' current':''}`;
    applyPercentBox(hit,match.rect);root.append(hit);
    if(index===F.search.index&&F.scrollSearch){requestAnimationFrame(()=>hit.scrollIntoView({block:'center',inline:'nearest'}));F.scrollSearch=false;}
  });
}
function selectSearchResult(index){
  if(S.busy||!F.search?.matches[index]||!discardDraft())return;
  F.search.index=index;F.scrollSearch=true;
  S.active=F.search.matches[index].page_id;persist();renderSearch();refresh();
}
async function performSearch(preferred=0){
  const payload=clone(searchRequest());
  if(!payload.query.trim()){invalidateSearch();toast('请先输入查找内容。','warning');return;}
  const data=await (await post('/search',payload)).json();
  if(signature(payload)!==signature(searchRequest()))return;
  F.search={...data,payload,signature:signature(S.pages),index:Math.min(preferred,Math.max(0,data.matches.length-1))};
  renderSearch();renderSearchHits();
}
async function runSearch(){
  if(S.busy||!S.pages.length)return;
  await mutate('正在查找文档文字…',async()=>{await performSearch();});
  if(F.search?.matches.length)selectSearchResult(0);
}
async function replaceFound(all=false){
  if(S.busy||!F.search?.matches.length)return;
  if(F.search.signature!==signature(S.pages)){invalidateSearch();toast('文档已改变，请重新查找。','warning');return;}
  if(!discardDraft())return;
  const selected=F.search.matches[F.search.index],index=F.search.index;
  const payload={...clone(F.search.payload),replacement:$('replace-content').value,fit:$('replace-fit').checked,
    ids:all?null:[selected.id]};
  await mutate(all?'正在校验并替换全部匹配…':'正在校验替换文字…',async()=>{
    const data=await (await post('/search/replace',payload)).json();
    if(!data.count){toast('替换内容与原文字相同，没有新增修改。');return;}
    commit(data.pages,S.active,`${all?'批量替换':'替换'} ${data.count} 处文字 · ${data.changed_pages} 页`);
    toast(`已替换 ${data.count} 处，可在历史中整步撤销。${data.warnings.length?'\n'+data.warnings.join('\n'):''}`,data.warnings.length?'warning':'info',7000);
    // Replacement is already committed. A subsequent search failure must not
    // disguise a successful edit or leave stale replace buttons enabled.
    try{await performSearch(index);}catch(error){invalidateSearch();toast(`替换已完成；重新查找失败：${error.message}`,'warning');}
  });
}

function annotationForm(){
  const d=S.draft;
  const op={kind:d.kind==='annotation_update'?'annotation_update':'annotation',id:d.id,
    comment:$('annotation-comment').value,color:$('annotation-color').value,opacity:Number($('annotation-opacity').value)};
  if((d.kind==='note'||d.type==='note')&&!op.comment.trim())throw new Error('请先填写便签内容。');
  if(op.kind==='annotation'){
    readRectInputs();op.type=d.kind;op.rect=[...d.rect];op.snap=$('annotation-snap').checked;
  }
  return op;
}
function renderAnnotations(){
  const list=$('annotation-list'),hits=$('annotation-hits');list.replaceChildren();hits.replaceChildren();
  const items=S.data&&S.displayPage===S.active?S.data.annotations||[]:[];
  $('annotation-count').textContent=items.length;
  $('annotation-guide').hidden=!items.length&&!['highlight','note'].includes(S.tool);
  if(!items.length){const p=document.createElement('p');p.className='note muted';p.textContent='本页还没有批注。用高亮工具拖选，或用便签工具点击页面。';list.append(p);}
  items.forEach((item,index)=>{
    const button=document.createElement('button');button.className='annotation-list-item';
    const icon=document.createElement('span');icon.dataset.icon=item.type;icon.style.color=item.color;
    const body=document.createElement('span'),title=document.createElement('strong'),text=document.createElement('small');
    title.textContent=`${item.type==='note'?'便签':'高亮'} ${index+1}`;text.textContent=item.comment||'无备注';
    body.append(title,text);button.append(icon,body);button.onclick=()=>chooseAnnotation(item);list.append(button);
    if(['highlight','note'].includes(S.tool)){
      const hit=document.createElement('button');hit.className='annotation-hit';hit.setAttribute('aria-label',`编辑${title.textContent}`);
      hit.title=item.comment||title.textContent;applyPercentBox(hit,item.rect);hit.onclick=event=>{event.stopPropagation();chooseAnnotation(item);};hits.append(hit);
    }
  });
  mountIcons(list);
}
function chooseAnnotation(item){
  if(S.busy||S.draft?.kind==='annotation_update'&&S.draft.id===item.id||!discardDraft())return;
  setDraft({kind:'annotation_update',...clone(item)});
}
async function deleteAnnotation(){
  if(S.busy||S.draft?.kind!=='annotation_update')return;
  const id=S.draft.id;
  await mutate('正在删除批注…',async()=>{
    const pages=clone(S.pages),page=pages.find(p=>p.id===S.active);
    page.ops.push({kind:'annotation_delete',id});await getPreview(page);commit(pages,S.active,'删除批注');
  });
}
function downloadBlob(blob,filename){
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename;
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
}
async function saveProject(){
  if(S.busy||!S.token)return;
  $('project-menu').open=false;
  if(hasPendingDraftChanges()){toast('请先应用或取消当前修改，再保存项目。','warning');return;}
  await mutate('正在打包源文件、图片和编辑历史…',async()=>{
    const payload={document:{...snapshot(),dirty:S.dirty},history:packHistory()};
    const response=await post('/projects/save',payload),blob=await response.blob();
    downloadBlob(blob,S.name.replace(/\.pdf$/i,'')+'.pdfstudio');
    S.projectMode=true;S.projectDirty=false;persist();
    setStatus(`项目已保存 · ${(blob.size/1024/1024).toFixed(2)} MB`);
    toast('项目文件已生成。保留下载的 .pdfstudio，重启服务后可重新打开继续编辑。','info',7000);
  });
}
async function openProject(file){
  if(S.busy||!file)return;
  if(file.size>220*1024*1024){toast('项目文件不能超过 220 MB。','error');return;}
  if((hasUnsavedChanges()||hasPendingDraftChanges())&&!confirm('打开项目会替换当前文档。未保存的编辑需要先保存为项目。继续打开？'))return;
  await mutate('正在检查项目文件与全部编辑历史…',async()=>{
    const body=new FormData();body.append('file',file);
    const data=await (await api('/projects/open',{method:'POST',body})).json();
    const {document:doc,history}=data.project;
    ++previewSerial;S.pages=doc.pages;S.active=doc.active;S.name=doc.name;S.dirty=doc.dirty;
    S.projectMode=true;S.projectDirty=false;S.selected.clear();clearDraft();invalidateSearch();
    previews.clear();thumbnails.clear();
    if(!unpackHistory({history},()=>true))throw new Error('项目历史无法恢复。');
    persist();showSidebar('pages');refresh();setStatus('项目已打开 · 可以继续编辑');
    toast(data.warnings.length?data.warnings.join('\n'):'已恢复源文件、图片、批注和编辑历史。',data.warnings.length?'warning':'info',6000);
  });
}
function initFeatureEvents(){
  $('btn-search').onclick=openSearch;$('tab-search').onclick=()=>showSidebar('search');
  $('search-form').onsubmit=event=>{event.preventDefault();void runSearch();};
  for(const id of ['search-query','search-case','search-scope'])$(id).addEventListener('input',invalidateSearch);
  $('replace-one').onclick=()=>replaceFound(false);$('replace-all').onclick=()=>replaceFound(true);
  $('remove-annotation').onclick=deleteAnnotation;
  $('btn-save-project').onclick=saveProject;
  $('btn-open-project').onclick=()=>{$('project-menu').open=false;if(!S.busy)$('project-file-input').click();};
  $('project-file-input').onchange=event=>{const file=event.target.files[0];event.target.value='';void openProject(file);};
  document.addEventListener('click',event=>{if(!event.target.closest('#project-menu'))$('project-menu').open=false;});
}
