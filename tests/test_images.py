from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
import pymupdf as fitz
import pytest

from app import engine, images
from app.main import app
from app.schemas import ImageReplaceOp


def png(color='red', size=(100, 60), mode='RGB'):
    out = BytesIO()
    Image.new(mode, size, color).save(out, format='PNG')
    return out.getvalue()


def source_pdf(shared=False, form=False):
    with fitz.open() as d:
        p = d.new_page(width=400, height=300)
        if form:
            with fitz.open() as inner:
                q = inner.new_page(width=120, height=100)
                q.insert_image((10, 10, 110, 70), stream=png())
                q.insert_text((12, 45), 'OVERLAY', fontsize=9)
                p.show_pdf_page(fitz.Rect(20, 20, 140, 120), inner)
                if shared:
                    p.show_pdf_page(fitz.Rect(200, 20, 320, 120), inner)
        else:
            xref = p.insert_image((30, 40, 130, 100), stream=png())
            if shared:
                p.insert_image((210, 40, 310, 100), xref=xref)
            p.insert_text((36, 75), 'OVERLAY', fontsize=9)
        p.insert_text((30, 180), 'KEEP TEXT', fontsize=15)
        return engine.import_pdf(d.tobytes())['data']


def image_op(source, ops=(), index=0, fit='contain', assets=None):
    spec = {'id': 'p', 'source': 's', 'ops': list(ops)}
    result = engine.preview_page(spec, source, .3, True, assets)
    item = result['images'][index]
    assert item['editable'], item
    return {'kind': 'image_replace', 'image_index': item['index'], 'bbox': item['bbox'],
            'digest': item['digest'], 'asset': 'new', 'fit': fit}


@pytest.mark.parametrize('form', [False, True])
def test_only_selected_occurrence_replaced_with_shared_images_and_forms(form):
    source = source_pdf(shared=True, form=form)
    op = image_op(source)
    output = engine.export_pdf([{'id':'p','source':'s','ops':[op]}], {'s':source}, 'vector', 120, {'new':png('blue')})
    with fitz.open(stream=output, filetype='pdf') as doc:
        p = doc[0]
        pix = p.get_pixmap()
        x, y = (40, 40) if form else (40, 50)
        assert pix.pixel(x, y) == (0, 0, 255)
        assert pix.pixel(x+180, y) == (255, 0, 0)
        assert p.get_text().count('OVERLAY') == (2 if form else 1)
        assert 'KEEP TEXT' in p.get_text()
        # Foreground text keeps its original paint order above the replacement.
        rect = p.search_for('OVERLAY')[0]
        crop = p.get_pixmap(clip=rect)
        assert any(crop.pixel(x,y)[2] < 100 for x in range(crop.width) for y in range(crop.height))


@pytest.mark.parametrize('fit', ['contain', 'cover', 'stretch'])
def test_replacement_aspect_modes(fit):
    source = source_pdf()
    op = image_op(source, fit=fit)
    doc, _ = engine.build_page({'id':'p','source':'s','ops':[op]}, source, {'new':png('blue',(40,100))})
    pix = doc[0].get_pixmap()
    assert pix.pixel(80,50) == (0,0,255)
    assert pix.pixel(35,50) == ((255,255,255) if fit == 'contain' else (0,0,255))
    assert pix.pixel(20,50) == (255,255,255)
    doc.close()


@pytest.mark.parametrize('angle', [90, 180, 270])
def test_image_replacement_after_crop_and_rotation(angle):
    source = source_pdf()
    ops = [{'kind':'crop','rect':[10,10,390,290]}, {'kind':'rotate','angle':angle}]
    op = image_op(source, ops, fit='stretch')
    doc, _ = engine.build_page({'id':'p','source':'s','ops':ops+[op]}, source, {'new':png('blue')})
    rect = fitz.Rect(op['bbox'])
    point = rect.tl + (rect.br-rect.tl) * .15
    pix = doc[0].get_pixmap()
    assert pix.pixel(int(point.x),int(point.y)) == (0,0,255)
    assert 'KEEP TEXT' in doc[0].get_text()
    doc.close()


def test_repeated_replacement_and_missing_or_stale_asset():
    source = source_pdf()
    op = image_op(source)
    asset = {'new':png('blue')}
    next_op = image_op(source, [op], assets=asset)
    next_op['asset'] = 'second'
    doc, _ = engine.build_page({'id':'p','source':'s','ops':[op,next_op]}, source, {**asset,'second':png('green')})
    assert doc[0].get_pixmap().pixel(40,50) == (0,128,0)
    doc.close()
    with pytest.raises(engine.EditError, match='不存在'):
        engine.build_page({'id':'p','source':'s','ops':[op]}, source)
    with pytest.raises(engine.EditError, match='已改变'):
        engine.build_page({'id':'p','source':'s','ops':[op,op]}, source, asset)


def test_old_image_is_not_kept_as_a_visible_or_unused_resource():
    source = source_pdf()
    op = image_op(source, fit='stretch')
    output = engine.export_pdf([{'id':'p','source':'s','ops':[op]}], {'s':source}, 'vector', 120, {'new':png('blue')})
    with fitz.open(stream=output,filetype='pdf') as doc:
        assert len(doc[0].get_image_info()) == 1
        for xref, *_ in doc[0].get_images(full=True):
            assert fitz.Pixmap(doc,xref).pixel(0,0) != (255,0,0)


def test_upload_validation_transparency_and_orientation():
    item = images.import_image(png((0,0,255,100), mode='RGBA'))
    assert (item['width'],item['height']) == (100,60)
    with Image.open(BytesIO(item['data'])) as picture:
        assert picture.mode == 'RGBA' and picture.getpixel((0,0))[3] == 100
    out = BytesIO()
    exif = Image.Exif();exif[274] = 6
    Image.new('RGB',(30,50),'blue').save(out,format='JPEG',exif=exif)
    item = images.import_image(out.getvalue())
    assert (item['width'],item['height']) == (50,30)
    with Image.open(BytesIO(item['data'])) as picture:
        assert not picture.getexif()
    with pytest.raises(engine.EditError):
        images.import_image(b'not an image')
    with pytest.raises(engine.EditError,match='20 MB'):
        images.import_image(b'x'*(images.MAX_IMAGE_BYTES+1))


def test_image_api_isolation_and_export():
    with TestClient(app,base_url='http://127.0.0.1') as client:
        headers = {'X-Session-Token':client.get('/api/session').json()['token']}
        other = {'X-Session-Token':client.get('/api/session').json()['token']}
        uploaded = client.post('/api/import',headers=headers,files={'file':('images.pdf',source_pdf(),'application/pdf')}).json()
        result = client.post('/api/images',headers=headers,files={'file':('replacement.png',png('blue'),'image/png')})
        assert result.status_code == 200, result.text
        asset = result.json()['asset']
        assert asset in client.get('/api/session',headers=headers).json()['images']
        assert client.post('/api/images',files={'file':('test.png',png(),'image/png')}).status_code == 401
        assert client.post('/api/images',headers=headers,files={'file':('bad.png',b'bad','image/png')}).status_code == 422
        spec = {'id':'p','source':uploaded['source']}
        region = client.post('/api/preview',headers=headers,json={'page':spec}).json()['images'][0]
        op = {'kind':'image_replace','image_index':region['index'],'bbox':region['bbox'],
              'digest':region['digest'],'asset':asset,'fit':'stretch'}
        assert client.post('/api/preview',headers=other,json={'page':{'id':'blank','ops':[op]}}).status_code == 404
        spec['ops'] = [op]
        result = client.post('/api/export',headers=headers,json={'pages':[spec]})
        assert result.status_code == 200, result.text
        with fitz.open(stream=result.content,filetype='pdf') as doc:
            assert doc[0].get_pixmap().pixel(40,50) == (0,0,255)


@pytest.mark.parametrize('rotate', [0, 90, 180, 270])
def test_asymmetric_image_preserves_its_orientation_and_existing_clip(rotate):
    picture = Image.new('RGB', (80, 40), 'blue')
    picture.paste('yellow', (0, 0, 25, 40))
    picture.paste('green', (25, 0, 80, 15))
    data = BytesIO();picture.save(data, format='PNG')

    def make(raw):
        doc = fitz.open();page = doc.new_page(width=200, height=200)
        page.insert_image((40, 40, 160, 160), stream=raw, rotate=rotate, keep_proportion=False)
        stream = page.get_contents()[0]
        doc.update_stream(stream, b'q 55 45 90 105 re W n\n' + doc.xref_stream(stream) + b'\nQ')
        return doc

    with make(png()) as doc, make(data.getvalue()) as expected:
        item = images.regions(doc[0])[0]
        op = ImageReplaceOp(kind='image_replace', image_index=item['index'], bbox=item['bbox'],
                            digest=item['digest'], asset='new', fit='stretch')
        images.replace(doc[0], op, data.getvalue())
        assert doc[0].get_pixmap().samples == expected[0].get_pixmap().samples


def test_inherited_inline_resources_are_resolved():
    with fitz.open() as doc:
        page = doc.new_page(width=200, height=150)
        page.insert_image((20, 20, 120, 80), stream=png())
        parent = int(doc.xref_get_key(page.xref, 'Parent')[1].split()[0])
        resource = int(doc.xref_get_key(page.xref, 'Resources')[1].split()[0])
        doc.xref_set_key(parent, 'Resources', doc.xref_object(resource))
        doc.xref_set_key(page.xref, 'Resources', 'null')
        page = doc.reload_page(page)
        item = images.regions(page)[0]
        assert item['editable'], item
        op = ImageReplaceOp(kind='image_replace', image_index=0, bbox=item['bbox'],
                            digest=item['digest'], asset='new', fit='stretch')
        images.replace(page, op, png('blue'))
        assert page.get_pixmap().pixel(30, 30) == (0, 0, 255)


def test_resource_name_collision_does_not_change_another_drawing():
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        old = page.insert_image((30, 40, 130, 100), stream=png())
        page.insert_image((210, 40, 310, 100), xref=old)
        # Predict the replacement form name, then deliberately use that name for
        # another occurrence, as can happen after a saved PDF is renumbered.
        with fitz.open(stream=doc.tobytes(),filetype='pdf') as probe:
            probe[0].insert_image((0,0,1,1), stream=png('blue'))
            collision = f'PDFStudio{probe.get_new_xref()}'
        resource = int(doc.xref_get_key(page.xref, 'Resources')[1].split()[0])
        original_name = page.get_images()[-1][7]
        doc.xref_set_key(resource, 'XObject/' + collision, f'{old} 0 R')
        stream = page.get_contents()[-1]
        doc.update_stream(stream, doc.xref_stream(stream).replace(('/'+original_name).encode(), ('/'+collision).encode()))
        assert ('/'+collision).encode() in doc.xref_stream(stream)
        page = doc.reload_page(page)
        item = images.regions(page)[0]
        op = ImageReplaceOp(kind='image_replace', image_index=0, bbox=item['bbox'],
                            digest=item['digest'], asset='new', fit='stretch')
        images.replace(page, op, png('blue'))
        assert page.get_pixmap().pixel(40,50) == (0,0,255)
        assert page.get_pixmap().pixel(220,50) == (255,0,0)


def test_inline_image_can_be_replaced_without_leaving_old_pixels():
    with fitz.open() as doc:
        page = doc.new_page(width=200, height=150)
        stream = doc.get_new_xref();doc.update_object(stream, '<<>>')
        doc.update_stream(stream, b'q 100 0 0 60 20 70 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00\nEI Q')
        page.set_contents(stream)
        item = images.regions(page)[0]
        assert item['editable'], item
        op = ImageReplaceOp(kind='image_replace', image_index=0, bbox=item['bbox'],
                            digest=item['digest'], asset='new', fit='stretch')
        images.replace(page, op, png('blue'))
        assert page.get_pixmap().pixel(30,30) == (0,0,255)
        assert len(page.get_image_info()) == 1
