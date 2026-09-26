"""Native HTTP / file upload / download regression checks for text editing."""
import argparse
from pathlib import Path

import pymupdf as fitz
from playwright.sync_api import sync_playwright, expect

from browser_smoke import ready, wait_state

ROOT = Path(__file__).resolve().parents[1]


def main(url):
    checks, errors = [], []
    artifacts = ROOT / 'test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1512, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto(url)
        wait_state(page, '!!S.token')
        page.locator('#file-input').set_input_files(str(ROOT / 'examples/demo.pdf'))
        ready(page)
        expect(page.locator('.page-card')).to_have_count(3)
        checks.append('Native file upload imports three pages')
        page.locator('#text-granularity').select_option('lines')
        original = page.evaluate("S.data.lines.find(r=>r.text==='Draft version: 2025').rect")
        current = 'Draft version: 2025'
        for i in range(10):
            page.get_by_role('button', name='编辑：' + current, exact=True).click()
            expect(page.locator('#font-family')).to_have_value('original')
            page.locator('#text-fit').uncheck()
            current = f'Draft version: {2026 + i}'
            page.locator('#text-content').fill(current)
            page.locator('#apply-edit').click()
            ready(page)
            expect(page.locator('#draft-panel')).to_be_hidden()
            rect = page.evaluate('(text)=>S.data.lines.find(r=>r.text===text).rect', current)
            assert max(abs(rect[j] - original[j]) for j in range(4)) < .08, (original, rect)
        checks.append('Ten original-font replacements keep position, width and size without auto-shrink')

        before = page.evaluate('JSON.stringify(S.pages)')
        page.get_by_role('button', name='编辑：' + current, exact=True).click()
        page.locator('#text-content').fill('This cannot fit in the original small field. ' * 35)
        page.locator('#text-fit').uncheck()
        page.locator('#apply-edit').click()
        ready(page)
        expect(page.locator('#toast')).to_contain_text('文字放不下')
        expect(page.locator('#draft-panel')).to_be_visible()
        assert page.evaluate('JSON.stringify(S.pages)') == before
        page.locator('#cancel-edit').click()
        checks.append('Genuine overflow retains draft and leaves committed pages unchanged')

        page.get_by_role('button', name='编辑：' + current, exact=True).click()
        page.locator('#text-content').fill('版本更新 2035')
        page.locator('#apply-edit').click()
        ready(page)
        expect(page.locator('#draft-panel')).to_be_hidden()
        expect(page.locator('#toast')).to_contain_text('缺少部分新字符')
        assert page.evaluate("S.data.lines.some(r=>r.text==='版本更新 2035')")
        checks.append('Missing Chinese glyphs produce visible fallback notice and readable text')

        page.locator('#text-granularity').select_option('blocks')
        paragraph = page.evaluate("S.data.blocks.find(r=>r.text.startsWith('这是一个'))")
        page.get_by_role('button', name='编辑：' + paragraph['text'][:80], exact=True).click()
        assert abs(float(page.locator('#line-height').input_value()) - 1.5) < .01
        page.locator('#text-content').fill(paragraph['text'].replace('虚构示例', '调试示例'))
        page.locator('#text-fit').uncheck()
        page.locator('#apply-edit').click()
        ready(page)
        expect(page.locator('#draft-panel')).to_be_hidden()
        after = page.evaluate("S.data.blocks.find(r=>r.text.includes('调试示例'))")
        assert after['rect'][:2] == paragraph['rect'][:2] or max(abs(a-b) for a,b in zip(after['rect'][:2], paragraph['rect'][:2])) < .08
        checks.append('Chinese paragraph keeps its original line spacing and anchor')

        page.locator('#btn-export').click()
        with page.expect_download() as download:
            page.locator('#confirm-export').click()
        target = artifacts / 'text-regression-result.pdf'
        download.value.save_as(str(target))
        with fitz.open(target) as doc:
            text = doc[0].get_text()
            assert '版本更新 2035' in text and '调试示例' in text
            assert 'Draft version:' not in text and '虚构示例' not in text
            assert 'Owner: Design Team' in text
            assert len(doc) == 3
        checks.append('Native PDF download contains new text, removes old text and preserves neighbors')
        page.reload()
        ready(page)
        assert page.evaluate("S.data.blocks.some(r=>r.text.includes('版本更新'))")
        checks.append('Real sessionStorage restores edits after a normal page reload')
        page.screenshot(path=str(artifacts / 'text-regression-ui.png'))
        page.locator('#btn-open').click()
        page.locator('#file-input').set_input_files(str(target))
        ready(page)
        assert page.evaluate("S.data.blocks.some(r=>r.text.includes('版本更新'))")
        checks.append('Downloaded PDF can be uploaded and edited again')
        assert not errors, errors
        checks.append('No uncaught JavaScript errors; original CSP remains enabled')
        context.close()
        browser.close()
    (artifacts / 'text-browser-checks.txt').write_text('\n'.join(checks), encoding='utf-8')
    for check in checks:
        print('PASS', check)
    print(f'Text browser checks: {len(checks)} passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    main(parser.parse_args().url)
