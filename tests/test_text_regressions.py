"""Regression cases for real glyph boxes, font changes and repeated editing."""
from pathlib import Path

import pymupdf as fitz
import pytest

from app import engine


def build(source, ops=()):
    return engine.build_page({'id': 'p', 'source': 's', 'ops': list(ops)}, source)


def replacement(region, text=None, **style):
    return {'kind': 'replace', 'rect': region['rect'], 'erase': region['erase'],
            'font': region['font'], 'text': region['text'] if text is None else text,
            'style': {'size': region['size'], 'line_height': region['line_height'],
                      'color': region['color'],
                      'bold': region['bold'], 'italic': region['italic'],
                      'family': 'original', 'fit': False, **style}}


def fixture_pdf(text='VERSION 2025', size=12, font='helv', lines=1, lineheight=1.1):
    with fitz.open() as doc:
        p = doc.new_page(width=1000, height=700)
        for i in range(lines):
            p.insert_text((45, 100 + i * size * lineheight), text, fontsize=size, fontname=font)
        return engine.import_pdf(doc.tobytes())['data']


@pytest.mark.parametrize('family', ['original', 'sans-serif', 'serif', 'monospace'])
def test_repeated_replacement_does_not_drift(family):
    source = fixture_pdf()
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    anchor = region['rect'][:2]
    doc.close()
    ops = []
    for i in range(10):
        # Allow a different font width, but never a different x/y anchor.
        region['rect'][2] = 260
        ops.append(replacement(region, f'VERSION {2025 + i}', family=family))
        doc, _ = build(source, ops)
        regions = engine.text_regions(doc[0])['lines']
        assert len(regions) == 1
        region = regions[0]
        assert region['rect'][:2] == pytest.approx(anchor, abs=.05)
        assert region['size'] == pytest.approx(12, abs=.05)
        doc.close()


@pytest.mark.parametrize('size', [8, 12, 28, 72, 100])
def test_extracted_single_line_needs_no_artificial_height(size):
    source = fixture_pdf('TITLE', size=size)
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    doc.close()
    doc, warnings = build(source, [replacement(region, 'TITLF')])
    assert 'TITLF' in doc[0].get_text()
    assert engine.text_regions(doc[0])['lines'][0]['size'] == pytest.approx(size, abs=.1)
    assert not warnings
    doc.close()


def test_tight_paragraph_uses_detected_line_spacing():
    source = fixture_pdf('Dense paragraph line', lines=8, lineheight=1.1)
    doc, _ = build(source)
    regions = engine.text_regions(doc[0])['blocks']
    assert len(regions) == 1
    region = regions[0]
    assert region['line_height'] == pytest.approx(1.1, abs=.001)
    doc.close()
    doc, warnings = build(source, [replacement(region, region['text'].replace('line', 'text'))])
    assert len(engine.text_regions(doc[0])['lines']) == 8
    assert doc[0].get_text().count('text') == 8
    assert not warnings
    doc.close()


def test_separate_columns_and_page_numbers_are_not_one_paragraph():
    source = engine.import_pdf((Path(__file__).parents[1] / 'examples/demo.pdf').read_bytes())['data']
    doc, _ = build(source)
    regions = engine.text_regions(doc[0])['blocks']
    footer = next(r for r in regions if r['text'].startswith('EXAMPLE'))
    assert '\n01' not in footer['text']
    assert any(r['text'] == '01' for r in regions)
    doc.close()
    doc, _ = build(source, [replacement(footer, footer['text'].replace('ONLY', 'TEST'))])
    assert '01' in doc[0].get_text().splitlines()
    assert 'EXAMPLE TEST' in doc[0].get_text()
    doc.close()


def test_whitespace_and_blank_lines_survive_layout():
    op = {'kind': 'text', 'rect': [30, 40, 500, 150], 'text': '  A    B\r\n\r\nC\tD',
          'style': {'size': 12, 'family': 'monospace', 'fit': False}}
    doc, _ = engine.build_page({'id': 'blank', 'ops': [op]}, None)
    regions = engine.text_regions(doc[0])['lines']
    assert [r['text'] for r in regions] == ['  A    B', 'C   D']
    assert regions[1]['rect'][1] - regions[0]['rect'][1] == pytest.approx(30, abs=.1)
    doc.close()


@pytest.mark.parametrize('font', ['helv', 'tiro', 'cour', 'hebo', 'tibi'])
def test_original_base14_font_preserves_metrics(font):
    source = fixture_pdf('Original font 2025', font=font)
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    old = region['rect']
    doc.close()
    doc, warnings = build(source, [replacement(region, 'Original font 2026')])
    result = engine.text_regions(doc[0])['lines'][0]
    assert result['rect'] == pytest.approx(old, abs=.06)
    assert not warnings
    doc.close()


def test_original_font_missing_glyphs_warns_and_keeps_chinese_readable():
    source = fixture_pdf('TEST 2025')
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    region['rect'][2] = 250
    doc.close()
    doc, warnings = build(source, [replacement(region, '中文测试 2026')])
    assert '中文测试' in doc[0].get_text()
    assert any('缺少部分新字符' in w for w in warnings)
    doc.close()


def test_missing_original_font_falls_back_with_warning():
    source = fixture_pdf()
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    doc.close()
    op = replacement(region)
    op['font'] = 'Unavailable Font'
    doc, warnings = build(source, [op])
    assert 'VERSION 2025' in doc[0].get_text()
    assert any('原字体无法复用' in w for w in warnings)
    doc.close()


@pytest.mark.parametrize('align', ['center', 'right'])
def test_alignment_uses_requested_width_without_extra_tolerance(align):
    source = fixture_pdf('SHORT')
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    doc.close()
    region['rect'] = [45, 90, 300, 120]
    doc, _ = build(source, [replacement(region, align=align)])
    rect = doc[0].search_for('SHORT')[0]
    if align == 'right':
        assert rect.x1 == pytest.approx(300, abs=.1)
    else:
        assert (rect.x0 + rect.x1) / 2 == pytest.approx(172.5, abs=.1)
    doc.close()


def test_auto_tolerance_does_not_overlap_neighbor():
    with fitz.open() as raw:
        p = raw.new_page(width=400, height=200)
        p.insert_text((40, 70), 'AAA', fontsize=12)
        p.insert_text((66, 70), 'NEIGHBOR', fontsize=12)
        source = engine.import_pdf(raw.tobytes())['data']
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    # Nearby content can share an extraction span. Explicit API erasure of the
    # first word must still leave the adjacent word intact.
    region['rect'] = list(doc[0].search_for('AAA')[0])
    region['erase'] = [region['rect']]
    doc.close()
    with pytest.raises(engine.EditError, match='文字放不下'):
        build(source, [replacement(region, 'AAAA')])


def test_fit_keeps_minimum_scale_and_reports_reduction():
    op = {'kind': 'text', 'rect': [20, 20, 130, 32], 'text': 'A moderately long line',
          'style': {'size': 14, 'fit': True}}
    doc, warnings = engine.build_page({'id': 'blank', 'ops': [op]}, None)
    result = engine.text_regions(doc[0])['lines'][0]
    assert 14 * .65 - .1 <= result['size'] < 14
    assert any('缩小' in w for w in warnings)
    doc.close()


@pytest.mark.parametrize('subset', [False, True])
def test_embedded_chinese_font_and_subset_remain_editable(subset):
    with fitz.open() as raw:
        p = raw.new_page(width=500, height=300)
        p.insert_font(fontname='original', fontbuffer=fitz.Font('cjk').buffer)
        p.insert_text((40, 80), '项目计划 2025', fontname='original', fontsize=18)
        if subset:
            raw.subset_fonts()
        source = engine.import_pdf(raw.tobytes())['data']
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    doc.close()
    doc, warnings = build(source, [replacement(region, '项目安排 2026')])
    assert '项目安排 2026' in doc[0].get_text()
    result = engine.text_regions(doc[0])['lines'][0]
    assert result['rect'][:2] == pytest.approx(region['rect'][:2], abs=.08)
    assert result['size'] == pytest.approx(18, abs=.1)
    if subset:
        assert any('缺少部分新字符' in w for w in warnings)
    else:
        assert not warnings
    doc.close()


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('group', ['blocks', 'lines'])
def test_every_demo_region_can_be_replaced_at_original_size(index, group):
    source = engine.import_pdf((Path(__file__).parents[1] / 'examples/demo.pdf').read_bytes())['data']
    spec = {'id': 'p', 'source': 's', 'index': index}
    doc, _ = engine.build_page(spec, source)
    regions = engine.text_regions(doc[0])[group]
    doc.close()
    for region in regions:
        doc, warnings = engine.build_page({**spec, 'ops': [replacement(region)]}, source)
        assert not any('缩小' in w for w in warnings), region['text']
        assert region['text'].splitlines()[0].strip() in doc[0].get_text()
        doc.close()


@pytest.mark.parametrize('filename', ['arial.ttf', 'arialn.ttf', 'simsun.ttc', 'msyh.ttc', 'simhei.ttf'])
def test_windows_embedded_font_resource_and_postscript_names(filename):
    path = Path('C:/Windows/Fonts') / filename
    if not path.exists():
        pytest.skip('Optional Windows system font fixture')
    original = 'Project version 2025' if filename.startswith('arial') else '项目计划：2025 年工作安排'
    with fitz.open() as raw:
        p = raw.new_page(width=595, height=300)
        p.insert_font(fontname='local-font', fontfile=str(path))
        p.insert_text((40, 80), original, fontname='local-font', fontsize=18)
        source = engine.import_pdf(raw.tobytes())['data']
    doc, _ = build(source)
    region = engine.text_regions(doc[0])['lines'][0]
    doc.close()
    doc, warnings = build(source, [replacement(region, region['text'].replace('2025', '2026'))])
    result = engine.text_regions(doc[0])['lines'][0]
    assert result['rect'] == pytest.approx(region['rect'], abs=.1)
    assert '2026' in doc[0].get_text()
    assert not warnings
    doc.close()
