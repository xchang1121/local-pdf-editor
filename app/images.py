"""Image assets and occurrence-local PDF image replacement.

Replace a drawing command, not a shared image object: the same logo may appear
several times, even inside a reused Form. Clone only the selected Form path so
other occurrences and the existing clipping, transform and paint order survive.
"""
from __future__ import annotations

import base64
from io import BytesIO
import math

from PIL import Image, ImageOps, UnidentifiedImageError
import pymupdf as fitz
from pypdf.generic import ContentStream, DecodedStreamObject, NameObject

from .errors import EditError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000


def import_image(raw: bytes, max_bytes=MAX_IMAGE_BYTES) -> dict:
    if len(raw) > max_bytes:
        raise EditError(f'单张图片最大支持 {max_bytes // 1024 // 1024} MB。')
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                raise EditError('请选择 PNG、JPEG 或 WebP 图片。')
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise EditError('图片超过 2400 万像素，请先缩小图片。')
            if getattr(source, 'is_animated', False):
                raise EditError('请使用静态图片；暂不支持动画图片。')
            # Bake camera orientation, remove file metadata, preserve transparency.
            oriented = ImageOps.exif_transpose(source)
            pic = oriented.convert('RGBA' if 'A' in oriented.getbands() or 'transparency' in source.info else 'RGB')
            pic.info.clear()
            output = BytesIO()
            pic.save(output, format='PNG')
            data = output.getvalue()
            if len(data) > 50 * 1024 * 1024:
                raise EditError('规范化后的图片超过 50 MB，请先缩小图片。')
            width, height = pic.size
            pic.thumbnail((320, 200))
            thumb = BytesIO()
            pic.save(thumb, format='PNG')
            return {'data': data, 'width': width, 'height': height,
                    'preview': base64.b64encode(thumb.getvalue()).decode('ascii')}
    except EditError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise EditError('无法读取此图片，请使用完整的 PNG、JPEG 或 WebP 文件。') from exc


def _content(data):
    stream = DecodedStreamObject()
    stream.set_data(data)
    return ContentStream(stream, None)


def _resource_owner(doc, owner):
    visited = set()
    while owner and owner not in visited:
        visited.add(owner)
        kind, value = doc.xref_get_key(owner, 'Resources')
        if kind != 'null':
            return owner, kind, value
        kind, parent = doc.xref_get_key(owner, 'Parent')
        owner = int(parent.split()[0]) if kind == 'xref' else 0
    return 0, 'dict', '<<>>'


def _resource_value(doc, owner):
    return _resource_owner(doc, owner)[1:]


def _xobject(doc, owner, name):
    owner, kind, resources = _resource_owner(doc, owner)
    if not owner:
        return 0
    if kind == 'xref':
        kind, value = doc.xref_get_key(int(resources.split()[0]), 'XObject/' + name)
    else:
        kind, value = doc.xref_get_key(owner, 'Resources/XObject/' + name)
    return int(value.split()[0]) if kind == 'xref' else 0


def _calls(page):
    """Return drawing paths plus parsed streams. Keep inline images in order."""
    doc, calls, parsed = page.parent, [], {}
    budget = 100_000

    def walk(owner, path, ancestors):
        nonlocal budget
        if owner in ancestors or len(ancestors) > 32:
            raise EditError('图片嵌套结构过于复杂，暂不能安全替换。')
        if owner not in parsed:
            parsed[owner] = _content(page.read_contents() if owner == page.xref else doc.xref_stream(owner))
        for index, (operands, operator) in enumerate(parsed[owner].operations):
            budget -= 1
            if budget < 0:
                raise EditError('页面对象过多，暂不能安全替换图片。')
            if operator == b'INLINE IMAGE':
                calls.append((path + [(owner, index)], 0))
            elif operator == b'Do' and operands:
                xref = _xobject(doc, owner, str(operands[0])[1:])
                if not xref:
                    continue
                subtype = doc.xref_get_key(xref, 'Subtype')[1]
                if subtype == '/Image':
                    calls.append((path + [(owner, index)], xref))
                elif subtype == '/Form':
                    walk(xref, path + [(owner, index)], ancestors | {owner})

    walk(page.xref, [], set())
    return calls, parsed


def _matching_calls(page, info):
    calls, parsed = _calls(page)
    # Unusual patterns or optional-content streams can have a different drawing
    # order. Refuse an ambiguous match rather than change an unrelated image.
    if len(calls) != len(info):
        raise EditError('此页图片绘制结构复杂，无法准确定位单张图片。')
    for (_, xref), item in zip(calls, info):
        if xref and item['xref'] and xref != item['xref']:
            # Identical images can be stored under several xrefs; MuPDF reports
            # one matching digest. Compare decoded image bytes in that case.
            pix = fitz.Pixmap(page.parent, xref)
            if pix.digest != item['digest']:
                raise EditError('此页图片标识不唯一，无法准确定位单张图片。')
    return calls, parsed


def regions(page):
    info = page.get_image_info(xrefs=True)
    if not info:
        return []
    reason = ''
    try:
        _matching_calls(page, info)
    except Exception:
        reason = '这页的图片结构暂不支持准确替换。'
    result = []
    for index, item in enumerate(info):
        rect = fitz.Rect(item['bbox']) & page.rect
        if rect.is_empty or min(rect.width, rect.height) < .2:
            continue
        result.append({'index': index, 'rect': list(rect), 'bbox': list(item['bbox']),
                       'digest': item['digest'].hex(), 'width': item['width'], 'height': item['height'],
                       'editable': not reason, 'reason': reason,
                       'full_page': rect.get_area() > page.rect.get_area() * .9})
    return result


def _dictionary_copy(doc, kind, value):
    xref = doc.get_new_xref()
    doc.update_object(xref, doc.xref_object(int(value.split()[0])) if kind == 'xref' else (value if kind == 'dict' else '<<>>'))
    return xref


def _add_resource(doc, owner, name, target):
    resource = _dictionary_copy(doc, *_resource_value(doc, owner))
    objects = _dictionary_copy(doc, *doc.xref_get_key(resource, 'XObject'))
    # Resource names survive PDF object renumbering. A previous editor may have
    # used our proposed name for another image; never overwrite that binding.
    base, suffix = name, 1
    while doc.xref_get_key(objects, name)[0] != 'null':
        name = f'{base}_{suffix}'
        suffix += 1
    doc.xref_set_key(objects, name, f'{target} 0 R')
    doc.xref_set_key(resource, 'XObject', f'{objects} 0 R')
    doc.xref_set_key(owner, 'Resources', f'{resource} 0 R')
    return name


def replace(page, op, raw):
    info = page.get_image_info(xrefs=True)
    if op.image_index >= len(info):
        raise EditError('找不到所选图片，请重新选择。')
    selected = info[op.image_index]
    if selected['digest'].hex() != op.digest or any(abs(a-b) > .1 for a,b in zip(selected['bbox'], op.bbox)):
        raise EditError('图片位置或内容已改变，请重新选择后再替换。')
    try:
        calls, parsed = _matching_calls(page, info)
        path = calls[op.image_index][0]
        doc = page.parent
        # This temporarily appends a drawing; replacing the page's content stream
        # below removes that drawing while retaining the newly created image xref.
        image_xref = page.insert_image(fitz.Rect(0, 0, 1, 1), stream=raw)
        pix = fitz.Pixmap(raw)
        a,b,c,d,_,_ = selected['transform']
        frame_ratio = math.hypot(a,b) / max(.0001, math.hypot(c,d))
        image_ratio = pix.width / pix.height
        sx, sy = 1., 1.
        if op.fit == 'contain':
            sx, sy = min(1., image_ratio / frame_ratio), min(1., frame_ratio / image_ratio)
        elif op.fit == 'cover':
            sx, sy = max(1., image_ratio / frame_ratio), max(1., frame_ratio / image_ratio)
        form = doc.get_new_xref()
        doc.update_object(form, f'<</Type/XObject/Subtype/Form/BBox[0 0 1 1]/Resources<</XObject<</I {image_xref} 0 R>>>>>>')
        doc.update_stream(form, (f'q 0 0 1 1 re W n {sx:.9f} 0 0 {sy:.9f} {(1-sx)/2:.9f} {(1-sy)/2:.9f} cm /I Do Q').encode('ascii'))
        replacement = form
        for owner, index in reversed(path):
            content = parsed[owner]
            operations = list(content.operations)
            if owner == page.xref:
                target_owner = owner
            else:
                target_owner = doc.get_new_xref()
                doc.update_object(target_owner, doc.xref_object(owner))
            name = f'PDFStudio{replacement}'
            name = _add_resource(doc, target_owner, name, replacement)
            operations[index] = ([NameObject('/' + name)], b'Do')
            content.operations = operations
            data = content.get_data()
            if owner == page.xref:
                stream = doc.get_new_xref()
                doc.update_object(stream, '<<>>')
                doc.update_stream(stream, data)
                page.set_contents(stream)
            else:
                doc.update_stream(target_owner, data)
            replacement = target_owner
    except EditError:
        raise
    except Exception as exc:
        raise EditError('此图片的结构暂不能安全替换；本次修改未提交。') from exc
