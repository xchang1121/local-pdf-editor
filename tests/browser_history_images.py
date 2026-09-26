"""Real browser history, image upload/replacement and downloaded PDF checks.

Run against an already-running server: python tests/browser_history_images.py --url http://127.0.0.1:8001
"""
import argparse
import base64
import sys
from collections import deque
from pathlib import Path

import pymupdf as fitz
from playwright.sync_api import sync_playwright, expect

from browser_smoke import ready, wait_state

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_images import png, source_pdf


def main(url):
    checks, errors, dialogs = [], [], []
    answers = deque()
    artifacts = ROOT / 'test-artifacts'
    artifacts.mkdir(exist_ok=True)
    source = artifacts / 'image-occurrences.pdf'
    source.write_bytes(source_pdf(shared=True))
    replacement = artifacts / 'replacement-blue.png'
    replacement.write_bytes(png('blue', (40, 100)))
    invalid = artifacts / 'invalid-image.png'
    invalid.write_bytes(b'not an image')

    def handle_dialog(dialog):
        dialogs.append((dialog.type, dialog.message))
        if dialog.type == 'beforeunload':
            dialog.accept()
            return
        assert answers, f'Unexpected browser dialog: {dialog.message}'
        if answers.popleft():
            dialog.accept()
        else:
            dialog.dismiss()

    def pixel(page, x, y):
        raw = page.evaluate('S.data.image')
        pix = fitz.Pixmap(base64.b64decode(raw))
        scale = pix.width / page.evaluate('S.data.width')
        return pix.pixel(int(x * scale), int(y * scale))[:3]

    with sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1512, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        page.on('dialog', handle_dialog)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        wait_state(page, '!!S.token')
        page.locator('#file-input').set_input_files(str(source))
        ready(page)
        page.locator('#btn-history').click()
        expect(page.locator('#history-panel')).to_be_visible()
        expect(page.locator('.history-entry')).to_have_count(2)
        checks.append('Import appears as a labelled step in the history sidebar')

        page.locator('[data-tool="image"]').click()
        expect(page.locator('.image-hit')).to_have_count(2)
        page.get_by_role('button', name='选择图片 1', exact=True).click()
        expect(page.locator('#image-fields')).to_be_visible()
        expect(page.locator('#geometry-fields')).to_be_hidden()
        expect(page.locator('#draft-badge')).to_be_hidden()
        expect(page.locator('#apply-edit')).to_be_disabled()
        page.get_by_role('button', name='选择图片 2', exact=True).click()
        assert not dialogs
        page.locator('#cancel-edit').click()
        page.locator('.image-list-item').first.click()
        checks.append('Image overlays and image list select individual occurrences without false dirty dialogs')

        with page.expect_file_chooser() as chooser:
            page.locator('#choose-image-file').click()
        chooser.value.set_files(str(invalid))
        wait_state(page, '!S.busy')
        expect(page.locator('#toast')).to_contain_text('无法读取')
        expect(page.locator('#apply-edit')).to_be_disabled()
        expect(page.locator('.history-entry')).to_have_count(2)
        checks.append('Invalid image upload reports an error and adds no history or operation')

        with page.expect_file_chooser() as chooser:
            page.locator('#choose-image-file').click()
        chooser.value.set_files(str(replacement))
        wait_state(page, '!S.busy && !!S.draft.asset')
        expect(page.locator('#replacement-preview')).to_be_visible()
        expect(page.locator('#draft-badge')).to_be_visible()
        expect(page.locator('.history-entry')).to_have_count(2)
        before = page.evaluate('JSON.stringify(S.pages)')
        asset = page.evaluate('S.draft.asset')
        answers.append(False)
        page.locator('.history-entry').first.click()
        expect(page.locator('#draft-panel')).to_be_visible()
        assert page.evaluate('JSON.stringify(S.pages)') == before
        assert page.evaluate('S.draft.asset') == asset
        checks.append('Uploaded image stays a draft; cancelling discard preserves its asset and current history')

        page.locator('#apply-edit').click()
        ready(page)
        expect(page.locator('#draft-panel')).to_be_hidden()
        expect(page.locator('.history-entry')).to_have_count(3)
        expect(page.locator('.history-entry.current')).to_contain_text('替换图片')
        assert pixel(page, 80, 50) == (0, 0, 255)
        assert pixel(page, 35, 50) == (255, 255, 255)
        assert pixel(page, 220, 50) == (255, 0, 0)
        checks.append('Contain mode preserves aspect and replaces only the selected shared-image occurrence')

        page.locator('#undo').click()
        ready(page)
        assert pixel(page, 40, 50) == (255, 0, 0)
        expect(page.locator('.history-entry.future')).to_have_count(1)
        page.locator('#redo').click()
        ready(page)
        assert pixel(page, 80, 50) == (0, 0, 255)
        checks.append('Undo/redo restores actual image pixels and marks future history steps')

        page.locator('.history-entry').nth(1).click()
        ready(page)
        page.reload()
        ready(page)
        expect(page.locator('#redo')).to_be_enabled()
        page.locator('#btn-history').click()
        expect(page.locator('.history-entry')).to_have_count(3)
        expect(page.locator('.history-entry.current')).to_have_attribute('data-index', '1')
        page.locator('.history-entry').nth(2).click()
        ready(page)
        assert pixel(page, 80, 50) == (0, 0, 255)
        checks.append('Same-tab refresh retains the selected history step, redo branch and uploaded image assets')

        page.locator('#undo').click()
        ready(page)
        page.locator('[data-tool="image"]').click()
        page.get_by_role('button', name='选择图片 1', exact=True).click()
        page.locator('#image-file-input').set_input_files(str(replacement))
        wait_state(page, '!S.busy && !!S.draft.asset')
        page.locator('#image-fit').select_option('cover')
        page.locator('#apply-edit').click()
        ready(page)
        assert pixel(page, 35, 50) == (0, 0, 255)
        assert pixel(page, 20, 50) == (255, 255, 255)
        expect(page.locator('.history-entry')).to_have_count(3)
        expect(page.locator('#redo')).to_be_disabled()
        assert page.evaluate("S.timeline.at(-1).state.pages[0].ops[0].fit") == 'cover'
        checks.append('Editing after rollback replaces the redo branch; cover mode clips to the original frame')

        page.get_by_role('button', name='选择图片 2', exact=True).click()
        page.locator('#image-file-input').set_input_files(str(replacement))
        wait_state(page, '!S.busy && !!S.draft.asset')
        page.locator('#image-fit').select_option('stretch')
        page.locator('#apply-edit').click()
        ready(page)
        assert pixel(page, 220, 50) == (0, 0, 255)
        assert page.evaluate('S.pages[0].ops.length') == 2
        checks.append('Second occurrence can be replaced independently using stretch mode')

        page.locator('[data-tool="select"]').click()
        page.get_by_role('button', name='编辑：KEEP TEXT', exact=True).click()
        page.locator('#text-content').fill('TEXT KEPT')
        page.locator('#apply-edit').click()
        ready(page)
        expect(page.locator('.history-entry.current')).to_contain_text('修改文字')
        page.locator('#btn-export').click()
        with page.expect_download() as downloaded:
            page.locator('#confirm-export').click()
        export = artifacts / 'images-history-export.pdf'
        downloaded.value.save_as(str(export))
        wait_state(page, '!S.busy')
        with fitz.open(export) as doc:
            assert 'TEXT KEPT' in doc[0].get_text()
            assert 'OVERLAY' in doc[0].get_text()
            assert 'KEEP TEXT' not in doc[0].get_text()
            assert doc[0].get_pixmap().pixel(35, 50) == (0, 0, 255)
        checks.append('Native downloaded PDF contains edited images and selectable edited text with foreground text retained')

        # Preview and history remain usable after export and at smaller viewports.
        page.screenshot(path=str(artifacts / 'history-images-desktop.png'), full_page=True)
        for width, height in [(1024, 768), (390, 844)]:
            page.set_viewport_size({'width':width, 'height':height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            expect(page.locator('#tab-history')).to_be_visible()
            page.locator('.history-entry').nth(1).click()
            ready(page)
            page.locator('#redo').click()
            ready(page)
        page.screenshot(path=str(artifacts / 'history-images-mobile.png'), full_page=True)
        page.set_viewport_size({'width':1512, 'height':1000})
        checks.append('History remains operable without page-width overflow at 1024 and 390 pixels')

        page.locator('.history-entry').first.click()
        wait_state(page, 'S.pages.length===0 && !S.busy')
        page.reload()
        wait_state(page, '!!S.token && !S.busy')
        expect(page.locator('#empty-state')).to_be_visible()
        expect(page.locator('#redo')).to_be_enabled()
        page.locator('#redo').click()
        ready(page)
        assert page.evaluate('S.pages.length') == 1
        checks.append('Rollback to an empty document survives refresh and can redo import')

        page.locator('#tab-pages').click()
        answers.append(True)
        page.locator('#page-delete').click()
        wait_state(page, 'S.pages.length===0 && !S.busy')
        page.reload()
        wait_state(page, '!!S.token && !S.busy')
        page.locator('#undo').click()
        ready(page)
        assert page.evaluate('S.pages.length') == 1
        checks.append('Deleting all pages survives refresh and can be undone')

        # Exercise the retention boundary via visible page actions, not synthetic commits.
        page.locator('#tab-pages').click()
        for _ in range(42):
            page.locator('#btn-blank').click()
            ready(page)
        page.locator('#btn-history').click()
        expect(page.locator('.history-entry')).to_have_count(41)
        count = page.evaluate('S.pages.length')
        page.locator('.history-entry').first.click()
        ready(page)
        assert page.evaluate('S.pages.length') == count - 40
        expect(page.locator('#undo')).to_be_disabled()
        page.reload()
        ready(page)
        page.locator('#btn-history').click()
        expect(page.locator('.history-entry')).to_have_count(41)
        assert page.evaluate('S.historyCursor') == 0
        checks.append('Forty-step retention has a reachable baseline and survives refresh at the boundary')

        assert not answers, list(answers)
        assert not errors, errors
        assert len([d for d in dialogs if d[0] != 'beforeunload']) == 2, dialogs
        checks.append('No JavaScript errors or unexpected discard dialogs during the complete workflow')
        context.close()
        browser.close()
    for check in checks:
        print('PASS', check)
    print(f'{len(checks)} history/image browser checks passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    main(parser.parse_args().url)
