from copy import deepcopy
from io import BytesIO
import json
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from app import engine, search, projects
from app.main import app
from app.schemas import SearchRequest, SearchReplaceRequest
from test_images import png, source_pdf, image_op


def text_source():
    with fitz.open() as doc:
        for i in range(2):
            page = doc.new_page(width=400, height=300)
            page.insert_text((35,55), 'Alpha 2025; alpha 2025. KEEP', fontsize=12)
            page.insert_text((35,110), 'Literal A+B and AAB', fontsize=12)
        return engine.import_pdf(doc.tobytes())['data']


def search_payload(**kwargs):
    return SearchReplaceRequest(pages=[{'id':f'p{i}','source':'s','index':i} for i in range(2)],
                                query='2025', replacement='2026', **kwargs).model_dump()


def test_literal_search_scope_case_and_geometry():
    source = text_source()
    payload = search_payload()
    found = search.find(payload, {'s':source}, {})
    assert len(found['matches']) == 4 and not found['limited']
    assert len({item['id'] for item in found['matches']}) == 4
    assert [item['page_number'] for item in found['matches']] == [1,1,2,2]
    for item in found['matches']:
        assert item['rect'][0] >= 35 and item['rect'][1] < 55
        assert '_op' not in item
    payload.update(query='Alpha',case_sensitive=True,page_ids=['p1'])
    assert len(search.find(payload,{'s':source},{})['matches']) == 1
    payload.update(case_sensitive=False)
    assert len(search.find(payload,{'s':source},{})['matches']) == 2
    payload.update(query='A+B')
    assert len(search.find(payload,{'s':source},{})['matches']) == 1


def test_search_only_current_visible_recipe_and_no_scan_ocr():
    payload = search_payload()
    payload['pages'][0]['ops'] = [{'kind':'crop','rect':[0,80,400,300]}]
    assert len(search.find(payload,{'s':text_source()},{})['matches']) == 2
    payload['pages'][1]['ops'] = [{'kind':'rotate','angle':90}]
    assert not search.find(payload,{'s':text_source()},{})['matches']
    blank = SearchRequest(pages=[{'id':'empty'}],query='test').model_dump()
    assert search.find(blank,{}, {})['no_text'] == [1]


def test_batch_replace_preserves_neighbors_and_can_replace_one():
    source, payload = text_source(), search_payload()
    found = search.find(payload,{'s':source},{})
    selected = deepcopy(payload);selected['ids'] = [found['matches'][1]['id']]
    one = search.replace(selected,{'s':source},{})
    assert one['count'] == 1 and one['changed_pages'] == 1
    result = search.replace(payload,{'s':source},{})
    assert result['count'] == 4 and result['changed_pages'] == 2
    assert all(len(p['ops']) == 1 and p['ops'][0]['kind'] == 'replace_batch' for p in result['pages'])
    output = engine.export_pdf(result['pages'],{'s':source},'vector',120)
    with fitz.open(stream=output,filetype='pdf') as doc:
        for page in doc:
            text = page.get_text()
            assert '2025' not in text and text.count('2026') == 2
            assert 'Alpha' in text and 'alpha' in text and 'KEEP' in text
            assert 'Literal A+B and AAB' in text


def test_failed_batch_stale_results_and_noop_leave_inputs_unchanged():
    source, payload = text_source(), search_payload()
    original = deepcopy(payload)
    payload['replacement'] = 'too much replacement text ' * 20
    payload['fit'] = False
    before = deepcopy(payload)
    with pytest.raises(engine.EditError,match='整批未提交'):
        search.replace(payload,{'s':source},{})
    assert payload == before
    result = search.find(original,{'s':source},{})
    original['ids'] = [result['matches'][0]['id']]
    original['pages'][0]['ops'].append({'kind':'cover','rect':[300,200,350,250]})
    with pytest.raises(engine.EditError,match='已改变'):
        search.replace(original,{'s':source},{})
    noop = search_payload();noop['replacement'] = '2025'
    assert search.replace(noop,{'s':source},{})['count'] == 0


@pytest.mark.parametrize('original,query,replacement,font',[
    ('合同金额2025元，日期2025年，备注保留。','2025','2026','china-s'),
    ('北京分公司与北京总部；上海保持不变。','北京','南京','china-s'),
    ('prefix20252025suffix','2025','2030','helv'),
    ('prefix20252025suffix','2025','','helv'),
])
def test_substring_replacement_keeps_chinese_and_adjacent_glyphs(original,query,replacement,font):
    with fitz.open() as doc:
        doc.new_page(width=500,height=180).insert_text((25,60),original,fontname=font,fontsize=13)
        source = engine.import_pdf(doc.tobytes())['data']
    payload = SearchReplaceRequest(pages=[{'id':'p','source':'s'}],query=query,replacement=replacement).model_dump()
    result = search.replace(payload,{'s':source},{})
    assert result['count'] == original.count(query)
    exported = engine.export_pdf(result['pages'],{'s':source},'vector',120)
    with fitz.open(stream=exported,filetype='pdf') as doc:
        # PDF content stream order changes on replacement; spatial order must
        # still describe the same line, without deleting neighboring glyphs.
        chars = [c for b in doc[0].get_text('rawdict')['blocks'] for line in b.get('lines',[])
                 for span in line['spans'] for c in span['chars']]
        text = ''.join(c['c'] for c in sorted(chars,key=lambda c:c['bbox'][0]))
        assert text == original.replace(query,replacement)


def test_batch_changes_after_existing_annotation_preserve_comment():
    payload = search_payload()
    payload['pages'][0]['ops'] = annotation_ops()
    result = search.replace(payload,{'s':text_source()},{})
    preview = engine.preview_page(result['pages'][0],text_source(),.5)
    assert [a['comment'] for a in preview['annotations']] == ['核对年份 2025','请确认数据与来源。']


def annotation_ops():
    return [{'kind':'annotation','id':'h1','type':'highlight','rect':[32,40,220,60],
             'comment':'核对年份 2025','color':'#ffdd00','opacity':.4},
            {'kind':'annotation','id':'n1','type':'note','rect':[260,80,280,100],
             'comment':'请确认数据与来源。','color':'#4ba8ff','opacity':1}]


def test_annotations_export_as_native_comments_and_keep_selectable_text():
    spec = {'id':'p','source':'s','ops':annotation_ops()}
    preview = engine.preview_page(spec,text_source(),.5)
    assert [a['type'] for a in preview['annotations']] == ['highlight','note']
    raw = engine.export_pdf([spec],{'s':text_source()},'vector',120)
    with fitz.open(stream=raw,filetype='pdf') as doc:
        page = doc[0]
        annots = list(page.annots())
        assert [a.type[0] for a in annots] == [fitz.PDF_ANNOT_HIGHLIGHT,fitz.PDF_ANNOT_TEXT]
        assert [a.info['content'] for a in annots] == ['核对年份 2025','请确认数据与来源。']
        assert 'Alpha 2025' in doc[0].get_text()
    raster = engine.export_pdf([spec],{'s':text_source()},'raster',120)
    with fitz.open(stream=raster,filetype='pdf') as doc:
        assert not list(doc[0].annots() or []) and not doc[0].get_text().strip()


@pytest.mark.parametrize('angle',[90,180,270])
def test_annotation_update_and_delete_after_crop_rotation(angle):
    ops = annotation_ops()+[{'kind':'crop','rect':[10,10,390,290]}, {'kind':'rotate','angle':angle},
                           {'kind':'annotation_update','id':'h1','comment':'已审核','color':'#00cc55','opacity':.6},
                           {'kind':'annotation_delete','id':'n1'}]
    preview = engine.preview_page({'id':'p','source':'s','ops':ops},text_source(),.5)
    assert len(preview['annotations']) == 1
    item = preview['annotations'][0]
    assert item['id'] == 'h1' and item['comment'] == '已审核' and item['color'] == '#00cc55'
    assert 0 <= item['rect'][0] < item['rect'][2] <= preview['width']
    assert 0 <= item['rect'][1] < item['rect'][3] <= preview['height']


def test_area_highlight_on_scan_and_invalid_notes():
    op = {'kind':'annotation','id':'h','type':'highlight','rect':[20,20,100,70],'color':'#ffcc00'}
    preview = engine.preview_page({'id':'p','ops':[op]},None,.3)
    assert len(preview['annotations']) == 1
    with pytest.raises(engine.EditError,match='填写'):
        engine.build_page({'id':'p','ops':[{**op,'type':'note'}]},None)
    with pytest.raises(engine.EditError,match='找不到'):
        engine.build_page({'id':'p','ops':[{'kind':'annotation_delete','id':'missing'}]},None)
    with pytest.raises(engine.EditError,match='填写'):
        engine.build_page({'id':'p','ops':annotation_ops()+[{'kind':'annotation_update','id':'n1','comment':''}]},None)


@pytest.mark.parametrize('rotation',[0,90,270])
def test_highlight_follows_mixed_size_text_quads(rotation):
    with fitz.open() as doc:
        p = doc.new_page(width=300,height=300)
        p.insert_text((40,100),'aa',fontsize=12)
        p.insert_text((54,100),'BIG',fontsize=26)
        p.insert_text((100,100),'zz',fontsize=12)
        source = engine.import_pdf(doc.tobytes())['data']
    spec = {'id':'p','source':'s','ops':[{'kind':'rotate','angle':rotation}] if rotation else []}
    doc, _ = engine.build_page(spec,source)
    try:
        before = doc[0].get_text('dict')['blocks'][0]['bbox']
    finally:
        doc.close()
    spec['ops'].append({'kind':'annotation','id':'h','type':'highlight','rect':list(fitz.Rect(before)+(-1,-1,1,1))})
    doc, _ = engine.build_page(spec,source)
    try:
        page = doc[0]
        annot = next(page.annots())
        points = annot.vertices
        actual = fitz.Rect(min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points))
        assert all(abs(a-b)<.05 for a,b in zip(actual,before))
    finally:
        doc.close()


def project_fixture():
    source = source_pdf(shared=True)
    base = {'id':'p','source':'s','index':0,'ops':[]}
    edited = deepcopy(base)
    edited['ops'] = [image_op(source), *annotation_ops()]
    payload = {'document':{'pages':[base],'active':'p','name':'便携项目.pdf','dirty':True},
               'history':{'pages':[base,edited], 'cursor':0, 'entries':[
                   {'label':'原始文档','time':'2026-09-26T12:00:00Z','state':{'pages':[0],'active':'p','name':'便携项目.pdf'}},
                   {'label':'替换图片和批注','time':'2026-09-26T12:01:00Z','state':{'pages':[1],'active':'p','name':'便携项目.pdf'}}]}}
    return payload, {'s':source}, {'new':png('blue')}


def test_portable_project_includes_redo_assets_and_remaps_identifiers():
    payload, sources, images = project_fixture()
    archive = projects.save(payload,sources,images)
    opened = projects.open_project(archive)
    assert opened['project']['history']['cursor'] == 0
    assert set(opened['sources']) != {'s'} and set(opened['images']) != {'new'}
    later = opened['project']['history']['pages'][1]
    raw = engine.export_pdf([later],opened['sources'],'vector',120,opened['images'])
    with fitz.open(stream=raw,filetype='pdf') as doc:
        assert doc[0].get_pixmap().pixel(40,65) == (0,0,255)
        assert len(list(doc[0].annots())) == 2
    with ZipFile(BytesIO(archive)) as z:
        assert set(z.namelist()) == {'manifest.json','sources/0.pdf','images/0.png'}
        assert 'token' not in json.loads(z.read('manifest.json'))


@pytest.mark.parametrize('corruption',['future_version','path','checksum','missing','mismatch','duplicate','garbage'])
def test_invalid_projects_rejected(corruption):
    payload, sources, images = project_fixture()
    raw = projects.save(payload,sources,images)
    with ZipFile(BytesIO(raw)) as z:
        files = {name:z.read(name) for name in z.namelist()}
    manifest = json.loads(files['manifest.json'])
    if corruption == 'future_version':manifest['version'] = 999
    if corruption == 'path':manifest['assets']['sources']['s']['path'] = '../outside.pdf'
    if corruption == 'checksum':files['images/0.png'] = png('green')
    if corruption == 'missing':files.pop('sources/0.pdf')
    if corruption == 'mismatch':manifest['project']['history']['cursor'] = 1
    if corruption == 'garbage':files['sources/0.pdf'] = b'broken pdf'
    files['manifest.json'] = json.dumps(manifest).encode()
    out = BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as z:
        for name,blob in files.items():z.writestr(name,blob)
        if corruption == 'duplicate':
            with pytest.warns(UserWarning):z.writestr('manifest.json',files['manifest.json'])
    with pytest.raises(engine.EditError):projects.open_project(out.getvalue())


def test_project_api_restores_after_a_new_server_lifespan_and_failed_open_is_atomic():
    with TestClient(app,base_url='http://127.0.0.1') as client:
        token = client.get('/api/session').json()['token'];headers = {'X-Session-Token':token}
        imported = client.post('/api/import',headers=headers,files={'file':('source.pdf',text_source())}).json()
        page = {'id':'p','source':imported['source'],'ops':annotation_ops()}
        payload = {'document':{'pages':[page],'active':'p','name':'项目.pdf'},
                   'history':{'pages':[page],'cursor':0,'entries':[{'label':'已批注','time':'','state':{'pages':[0],'active':'p','name':'项目.pdf'}}]}}
        response = client.post('/api/projects/save',headers=headers,json=payload)
        assert response.status_code == 200,response.text
        saved = response.content
        other = {'X-Session-Token':client.get('/api/session').json()['token']}
        assert client.post('/api/projects/save',headers=other,json=payload).status_code == 404
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.post('/api/preview',headers=headers,json={'page':page}).status_code == 401
        headers = {'X-Session-Token':client.get('/api/session').json()['token']}
        response = client.post('/api/projects/open',headers=headers,files={'file':('项目.pdfstudio',saved)})
        assert response.status_code == 200,response.text
        restored = response.json()['project']['document']['pages'][0]
        assert restored['source'] != page['source']
        before = client.get('/api/session',headers=headers).json()
        assert client.post('/api/projects/open',headers=headers,files={'file':('bad.pdfstudio',b'bad')}).status_code == 422
        assert client.get('/api/session',headers=headers).json() == before
        response = client.post('/api/preview',headers=headers,json={'page':restored})
        assert response.status_code == 200 and len(response.json()['annotations']) == 2


def test_empty_document_project_preserves_undo_and_archive_limits(monkeypatch):
    payload, sources, images = project_fixture()
    payload['document']['pages'] = [];payload['document']['active'] = None
    payload['history']['entries'][0]['state'].update(pages=[],active=None)
    data = projects.save(payload,sources,images)
    loaded = projects.open_project(data)
    assert loaded['project']['document']['pages'] == []
    assert len(loaded['sources']) == len(loaded['images']) == 1
    monkeypatch.setattr(projects,'MAX_PROJECT_BYTES',10)
    with pytest.raises(engine.EditError,match='220 MB'):projects.open_project(data)
