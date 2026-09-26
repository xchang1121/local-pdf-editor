"""Literal search of visible horizontal text and atomic replacement planning."""
from collections import Counter, defaultdict
import hashlib
import json
import re

import pymupdf as fitz

from . import engine
from .errors import EditError
from .schemas import PageSpec

MAX_MATCHES = 2000


def _matches(page, spec, number, pattern):
    fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    raw = page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
    line_number = 0
    for block in raw['blocks']:
        for line in block.get('lines', []):
            line_number += 1
            direction = line.get('dir', (1, 0))
            if abs(direction[0]-1) > .015 or abs(direction[1]) > .015 or line.get('wmode', 0):
                continue
            chars = [(char, span) for span in line['spans'] for char in span['chars']
                     if (fitz.Rect(char['bbox']).tl + fitz.Rect(char['bbox']).br) / 2 in page.rect]
            text = ''.join(char['c'] for char, _ in chars)
            # A glyph can represent more than one Unicode code point.
            mapped = [item for item in chars for _ in item[0]['c']]
            for match in pattern.finditer(text):
                selected = mapped[match.start():match.end()]
                rect = fitz.EMPTY_RECT()
                for char, _ in selected:
                    rect |= fitz.Rect(char['bbox'])
                rect &= page.rect
                if rect.is_empty or min(rect.width, rect.height) < .2:
                    continue
                weights = Counter((span['font'], span['size'], span['flags'], span['color']) for _, span in selected)
                font, size, flags, color = weights.most_common(1)[0][0]
                key = f'{fingerprint}:{line_number}:{match.start()}:{match.end()}'
                yield {'id':hashlib.sha256(key.encode()).hexdigest(), 'page_id':spec['id'], 'page_number':number,
                       'rect':list(rect), 'text':match.group(), 'before':text[max(0,match.start()-36):match.start()],
                       'after':text[match.end():match.end()+36], 'mixed':len(weights)>1,
                       '_op':{'kind':'replace', 'rect':list(rect), 'erase':[list(rect + (.01,.01,-.01,-.01))],
                              'text':'', 'font':font, 'style':{'family':'original', 'size':max(4,min(200,size)),
                                  'bold':bool(flags & 16), 'italic':bool(flags & 2), 'color':f'#{color & 0xffffff:06x}'}}}


def find(payload, sources, images, *, private=False):
    pattern = re.compile(re.escape(payload['query']), 0 if payload.get('case_sensitive') else re.IGNORECASE)
    results = []
    no_text = []
    selected = payload.get('page_ids')
    for number, spec in enumerate(payload['pages'], 1):
        if selected is not None and spec['id'] not in selected:
            continue
        doc, _ = engine.build_page(spec, sources.get(spec.get('source')), images)
        try:
            if not doc[0].get_text().strip():
                no_text.append(number)
            for item in _matches(doc[0], spec, number, pattern):
                if len(results) >= MAX_MATCHES:
                    return {'matches':results, 'limited':True, 'no_text':no_text}
                if not private:
                    item.pop('_op')
                results.append(item)
        finally:
            doc.close()
    return {'matches':results, 'limited':False, 'no_text':no_text}


def replace(payload, sources, images):
    found = find(payload, sources, images, private=True)
    ids = payload.get('ids')
    if ids is None and found['limited']:
        raise EditError('匹配超过 2000 处，请缩小查找范围后再全部替换。')
    if ids is not None and not set(ids) <= {item['id'] for item in found['matches']}:
        raise EditError('查找结果已改变，请重新查找后再替换。')
    grouped = defaultdict(list)
    count, warnings = 0, []
    for item in found['matches']:
        if ids is not None and item['id'] not in ids:
            continue
        if item['text'] == payload['replacement']:
            continue
        if item['mixed']:
            warnings.append(f"第 {item['page_number']} 页：匹配文字包含混合样式，已按其中的主要字体、字号和颜色替换。")
        op = item['_op']
        op['text'] = payload['replacement']
        op['style']['fit'] = payload.get('fit', True)
        grouped[item['page_id']].append(op)
        count += 1
    result = []
    for number, original in enumerate(payload['pages'], 1):
        spec = PageSpec.model_validate(original).model_dump()
        if spec['id'] in grouped:
            # Right-to-left within each line prevents later erasures from
            # touching a newly inserted neighboring replacement.
            ops = sorted(grouped[spec['id']], key=lambda op:(op['rect'][1],op['rect'][0]), reverse=True)
            spec['ops'].append({'kind':'replace_batch', 'replacements':ops})
            try:
                doc, notes = engine.build_page(spec, sources.get(spec['source']), images)
                doc.close()
                warnings.extend(f'第 {number} 页：{note}' for note in notes)
            except (EditError, ValueError) as exc:
                raise EditError(f'第 {number} 页无法完成替换，整批未提交：{exc}') from exc
        result.append(spec)
    return {'pages':result, 'count':count, 'changed_pages':len(grouped), 'warnings':list(dict.fromkeys(warnings))}
