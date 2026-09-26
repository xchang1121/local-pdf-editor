from __future__ import annotations
import base64
from pathlib import Path

import pymupdf as fitz
import pytest
from app import engine
from app.schemas import PageSpec


def raw_pdf(texts=('ALPHA secret', 'BRAVO', 'CHARLIE'), rotation=0, cropped=False):
    doc=fitz.open()
    for text in texts:
        p=doc.new_page(width=400,height=500)
        p.insert_text((55,90),text,fontsize=16)
        p.insert_text((55,240),'KEEP ME',fontsize=12)
        if cropped:p.set_cropbox(fitz.Rect(20,30,380,480))
        if rotation:p.set_rotation(rotation)
    result=doc.tobytes();doc.close();return result


def spec(index=0,ops=None,source='src',width=400,height=500):
    return PageSpec(id=f'p{index}',source=source,index=index,width=width,height=height,ops=ops or []).model_dump()


def text_of(data):
    with fitz.open(stream=data,filetype='pdf') as doc:return '\n'.join(p.get_text() for p in doc)


@pytest.fixture
def source():return engine.import_pdf(raw_pdf())['data']


def test_import_pages_and_preview(source):
    result=engine.preview_page(spec(),source,1,True)
    assert result['width']==400 and result['height']==500
    assert base64.b64decode(result['image']).startswith(b'\x89PNG')
    assert any('ALPHA' in b['text'] for b in result['blocks'])


def test_delete_reorder_duplicate_and_merge(source):
    second=engine.import_pdf(raw_pdf(('DELTA',)))['data']
    pages=[spec(2),spec(0),spec(2),spec(0,source='second')]
    out=engine.export_pdf(pages,{'src':source,'second':second},'vector',180)
    with fitz.open(stream=out,filetype='pdf') as doc:
        assert len(doc)==4
        assert [p.get_text().splitlines()[0] for p in doc]==['CHARLIE','ALPHA secret','CHARLIE','DELTA']
        assert 'BRAVO' not in text_of(out)


def test_replace_moves_and_removes_old_text(source):
    data=engine.preview_page(spec(),source,1,True)
    region=next(b for b in data['blocks'] if 'ALPHA' in b['text'])
    op={'kind':'replace','rect':[110,130,370,200],'erase':region['erase'],
        'text':'中文替换 2026','style':{'size':17,'bold':True,'align':'center'}}
    out=engine.export_pdf([spec(ops=[op])],{'src':source},'vector',180)
    txt=text_of(out)
    assert 'ALPHA' not in txt and 'secret' not in txt
    assert '中文替换' in txt and '2026' in txt and 'KEEP ME' in txt
    with fitz.open(stream=out,filetype='pdf') as doc:
        rect=doc[0].search_for('2026')[0]
        assert 110<=rect.x0<370 and 130<=rect.y0<200


def test_text_fit_rejected_instead_of_silently_losing_content(source):
    op={'kind':'text','rect':[10,10,35,21],'text':'word '*30,'style':{'size':20,'fit':False}}
    with pytest.raises(engine.EditError,match='文字放不下'):engine.preview_page(spec(ops=[op]),source,1,True)
    assert 'ALPHA' in text_of(source)


def test_empty_replacement_deletes_text(source):
    region=engine.preview_page(spec(),source,1,True)['blocks'][0]
    op={'kind':'replace','rect':region['rect'],'erase':region['erase'],'text':''}
    out=engine.export_pdf([spec(ops=[op])],{'src':source},'vector',180)
    assert 'ALPHA' not in text_of(out) and 'KEEP ME' in text_of(out)


def test_cover_is_not_redaction(source):
    rect=[45,65,270,105]
    covered=engine.export_pdf([spec(ops=[{'kind':'cover','rect':rect}])],{'src':source},'vector',180)
    redacted=engine.export_pdf([spec(ops=[{'kind':'redact','rect':rect,'color':'#000000'}])],{'src':source},'vector',180)
    assert 'ALPHA' in text_of(covered)
    assert 'ALPHA' not in text_of(redacted)
    assert 'KEEP ME' in text_of(redacted)


def test_redaction_splits_visible_image_pixels():
    image_doc=fitz.open();p=image_doc.new_page(width=200,height=200)
    p.draw_rect(p.rect,fill=(1,0,0),color=None)
    image=p.get_pixmap().tobytes('png')
    doc=fitz.open();p=doc.new_page(width=200,height=200);p.insert_image(p.rect,stream=image)
    raw=doc.tobytes();doc.close();image_doc.close()
    source=engine.import_pdf(raw)['data']
    op={'kind':'redact','rect':[50,50,150,150],'color':'#ffffff'}
    out=engine.export_pdf([spec(width=200,height=200,ops=[op])],{'src':source},'vector',180)
    with fitz.open(stream=out,filetype='pdf') as final:
        pix=final[0].get_pixmap()
        assert pix.pixel(100,100)==(255,255,255)
        assert pix.pixel(10,10)==(255,0,0)
        # The embedded image itself has blanked pixels, not merely a cover.
        xref=final[0].get_images()[0][0];embedded=fitz.Pixmap(final,xref)
        assert embedded.pixel(100,100)==(255,255,255)


def test_crop_visibility_and_raster_rebuild(source):
    ops=[{'kind':'crop','rect':[30,160,380,400]}]
    normal=engine.export_pdf([spec(ops=ops)],{'src':source},'vector',180)
    flat=engine.export_pdf([spec(ops=ops)],{'src':source},'raster',120)
    with fitz.open(stream=normal,filetype='pdf') as doc:
        assert tuple(doc[0].rect)==(0,0,350,240)
        # Restoring the original MediaBox makes hidden content accessible.
        doc[0].set_cropbox(doc[0].mediabox)
        assert 'ALPHA' in doc[0].get_text()
    with fitz.open(stream=flat,filetype='pdf') as doc:
        assert not doc[0].get_text().strip()
        assert tuple(doc[0].mediabox)==(0,0,350,240)
        assert len(doc[0].get_images())==1
        assert not doc.metadata.get('author')
        doc[0].set_cropbox(doc[0].mediabox)
        assert 'ALPHA' not in doc[0].get_text()


@pytest.mark.parametrize('angle',[0,90,180,270])
def test_import_normalizes_rotated_cropped_page_appearance(angle):
    raw=raw_pdf(rotation=angle,cropped=True)
    normalized=engine.import_pdf(raw)['data']
    with fitz.open(stream=raw,filetype='pdf') as old,fitz.open(stream=normalized,filetype='pdf') as new:
        assert new[0].rotation==0
        a=old[0].get_pixmap();b=new[0].get_pixmap()
        assert (a.width,a.height)==(b.width,b.height)
        # Normalization shouldn't change the appearance of the input page.
        assert bool(a.samples==b.samples)


@pytest.mark.parametrize('angle',[90,180,270])
def test_rotate_then_crop_then_edit_coordinates(source,angle):
    first={'kind':'rotate','angle':angle}
    data=engine.preview_page(spec(ops=[first]),source,1,True)
    w,h=data['width'],data['height']
    crop={'kind':'crop','rect':[10,20,w-20,h-30]}
    text={'kind':'text','rect':[20,20,220,65],'text':'COORDINATE-OK','style':{'size':14}}
    ops=[first,crop,text]
    out=engine.export_pdf([spec(ops=ops)],{'src':source},'vector',180)
    with fitz.open(stream=out,filetype='pdf') as doc:
        p=doc[0];r=p.search_for('COORDINATE-OK')[0]
        assert 19<=r.x0<=23 and 19<=r.y0<=40
        assert abs(p.rect.width-(w-30))<.01
        assert abs(p.rect.height-(h-50))<.01


def test_repeated_crop_translates_correctly(source):
    ops=[{'kind':'crop','rect':[20,20,380,480]}, {'kind':'crop','rect':[10,10,350,450]},
         {'kind':'text','rect':[5,5,180,45],'text':'SECOND-CROP'}]
    out=engine.export_pdf([spec(ops=ops)],{'src':source},'vector',180)
    with fitz.open(stream=out,filetype='pdf') as d:
        assert tuple(d[0].rect)==(0,0,340,440)
        assert abs(d[0].search_for('SECOND-CROP')[0].x0-5)<.05


def test_blank_page_and_html_escaping():
    op={'kind':'text','rect':[20,20,370,80],'text':'<script>not executed</script> & 中文','style':{'size':12}}
    out=engine.export_pdf([spec(source=None,ops=[op])],{},'vector',180)
    assert '<script>not executed</script>' in text_of(out)


def test_bad_files_and_password_are_rejected():
    with pytest.raises(engine.EditError):engine.import_pdf(b'Not a PDF')
    d=fitz.open();d.new_page()
    raw=d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,user_pw='secret',owner_pw='owner');d.close()
    with pytest.raises(engine.EditError,match='密码'):engine.import_pdf(raw)


def test_schema_and_bounds_reject_bad_ops(source):
    with pytest.raises(ValueError):spec(ops=[{'kind':'text','rect':[0,0,40,40],'style':{'size':-1}}])
    with pytest.raises(ValueError):spec(ops=[{'kind':'replace','rect':[0,0,40,40],'text':'no erase'}])
    with pytest.raises(ValueError):spec(ops=[{'kind':'crop','rect':[float('nan'),0,40,40]}])
    with pytest.raises(engine.EditError,match='超出'):engine.preview_page(spec(ops=[{'kind':'crop','rect':[-20,0,40,40]}]),source,1,True)


def test_scan_no_editable_text():
    sample=Path(__file__).parents[1]/'examples/demo.pdf'
    source=engine.import_pdf(sample.read_bytes())['data']
    data=engine.preview_page(spec(index=2,width=595,height=842),source,.2,True)
    assert not data['has_text'] and data['blocks']==[]


def test_normalization_flattens_form_and_annotation():
    d=fitz.open();p=d.new_page(width=400,height=500)
    p.insert_text((40,50),'NORMAL TEXT')
    a=p.add_freetext_annot(fitz.Rect(40,100,250,140),'ANNOTATION',fontsize=15)
    a.update()
    w=fitz.Widget();w.field_name='field';w.field_type=fitz.PDF_WIDGET_TYPE_TEXT;w.rect=fitz.Rect(40,180,250,220);w.field_value='FORM VALUE'
    p.add_widget(w)
    d.embfile_add('hidden.txt',b'embedded confidential payload')
    raw=d.tobytes();d.close()
    clean=engine.import_pdf(raw)['data']
    with fitz.open(stream=clean,filetype='pdf') as doc:
        assert not list(doc[0].annots() or [])
        assert not list(doc[0].widgets() or [])
        assert doc.embfile_count()==0
        assert 'NORMAL TEXT' in doc[0].get_text()
        assert 'ANNOTATION' in doc[0].get_text()
        assert 'FORM VALUE' in doc[0].get_text()


@pytest.mark.parametrize('angle',[90,180,270])
@pytest.mark.parametrize('offset',[(10,20),(-10,-20)])
def test_offset_mediabox_rotation_normalization(angle,offset):
    x,y=offset
    d=fitz.open();p=d.new_page(width=400,height=500)
    p.insert_text((55,90),'OFFSET-CONTENT',fontsize=16)
    p.set_mediabox(fitz.Rect(x,y,400+x,500+y))
    p.set_cropbox(p.cropbox+(20,30,-20,-20));p.set_rotation(angle)
    before=p.get_pixmap();raw=d.tobytes();d.close()
    source=engine.import_pdf(raw)['data']
    with fitz.open(stream=source,filetype='pdf') as doc:
        after=doc[0].get_pixmap()
        assert (before.width,before.height)==(after.width,after.height)
        assert bool(before.samples==after.samples)


def test_crop_then_rotate_retains_appearance_and_dimensions(source):
    crop={'kind':'crop','rect':[20,30,380,480]}
    ops=[crop,{'kind':'rotate','angle':90}]
    doc,_=engine.build_page(spec(ops=[crop]),source)
    doc[0].set_rotation(90);expected=doc[0].get_pixmap()
    actual,_=engine.build_page(spec(ops=ops),source)
    got=actual[0].get_pixmap()
    assert tuple(actual[0].rect)==(0,0,450,360)
    assert bool(expected.samples==got.samples)
    actual.close();doc.close()


def test_tight_extracted_line_can_be_replaced_without_auto_shrink(source):
    data=engine.preview_page(spec(),source,1,True)
    region=next(r for r in data['lines'] if r['text']=='ALPHA secret')
    # Use the raw extracted glyph bbox on purpose: this used to fail because the
    # HTML layout engine needs leading and the fallback font is slightly wider.
    op={'kind':'replace','rect':region['rect'],'erase':region['erase'],
        'text':'OMEGA secret','style':{'size':region['size'],'line_height':1.25,'fit':False}}
    out=engine.export_pdf([spec(ops=[op])],{'src':source},'vector',180)
    txt=text_of(out)
    assert 'ALPHA secret' not in txt
    assert 'OMEGA secret' in txt


def test_tight_extracted_line_chinese_replacement_succeeds(source):
    data=engine.preview_page(spec(),source,1,True)
    region=next(r for r in data['lines'] if r['text']=='ALPHA secret')
    op={'kind':'replace','rect':region['rect'],'erase':region['erase'],
        'text':'中文修改 2026','style':{'size':region['size'],'line_height':1.25,'fit':True}}
    out=engine.export_pdf([spec(ops=[op])],{'src':source},'vector',180)
    txt=text_of(out)
    assert 'ALPHA secret' not in txt
    assert '中文修改' in txt and '2026' in txt


def test_genuinely_long_replacement_still_rejected_without_fit(source):
    data=engine.preview_page(spec(),source,1,True)
    region=next(r for r in data['lines'] if r['text']=='ALPHA secret')
    op={'kind':'replace','rect':region['rect'],'erase':region['erase'],
        'text':'this replacement is intentionally much too long for the original line box',
        'style':{'size':region['size'],'line_height':1.25,'fit':False}}
    with pytest.raises(engine.EditError,match='确实超出当前文本框'):
        engine.preview_page(spec(ops=[op]),source,1,True)
