"""Native browser checks for selection versus genuinely uncommitted edits."""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from browser_smoke import draw, ready, wait_state

ROOT = Path(__file__).resolve().parents[1]


def main(url):
    checks, dialogs, errors = [], [], []
    accept_dialogs = False

    def handle_dialog(dialog):
        dialogs.append(dialog.message)
        if accept_dialogs:
            dialog.accept()
        else:
            dialog.dismiss()

    with sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1512, 'height': 1000})
        page.on('dialog', handle_dialog)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        wait_state(page, '!!S.token')
        page.locator('#btn-demo').click()
        ready(page)
        first = '把想法，变成下一步。'
        second = '项目进展简报 · 可编辑示例文档'

        def select(text):
            page.get_by_role('button', name='编辑：' + text, exact=True).click()

        select(first)
        select(second)
        assert dialogs == [], dialogs
        expect(page.locator('#text-content')).to_have_value(second)
        checks.append('Switching untouched text selections produces no discard dialog')

        page.locator('[data-tool="crop"]').click()
        assert dialogs == []
        page.locator('[data-tool="select"]').click()
        select(first)
        page.locator('.page-card').nth(1).click()
        ready(page)
        assert dialogs == []
        page.locator('.page-card').first.click()
        ready(page)
        checks.append('Switching tools or pages after selection produces no discard dialog')

        select(first)
        page.locator('#text-content').fill('暂存修改，尚未应用')
        page.locator('[data-tool="select"]').click()
        assert dialogs == []
        expect(page.locator('#text-content')).to_have_value('暂存修改，尚未应用')
        checks.append('Clicking the already active tool preserves an edited draft silently')

        select(second)
        assert len(dialogs) == 1 and '尚未应用的修改' in dialogs[-1]
        expect(page.locator('#text-content')).to_have_value('暂存修改，尚未应用')
        accept_dialogs = True
        select(second)
        assert len(dialogs) == 2
        expect(page.locator('#text-content')).to_have_value(second)
        accept_dialogs = False
        checks.append('Real text edits prompt once; Cancel keeps them and OK switches selection')

        baseline = len(dialogs)
        page.locator('#text-content').fill('changed')
        page.locator('#text-content').fill(second)
        size = page.locator('#font-size').input_value()
        page.locator('#font-size').fill(str(float(size) + 1))
        page.locator('#font-size').fill(str(float(size)))
        page.locator('#font-bold').check()
        page.locator('#font-bold').uncheck()
        select(first)
        assert len(dialogs) == baseline
        checks.append('Editing then restoring content, numeric fields and styling leaves no pending change')

        x = page.locator('#rect-x').input_value()
        page.locator('#rect-x').fill(str(float(x) + 3))
        select(second)
        assert len(dialogs) == baseline + 1
        expect(page.locator('#rect-x')).to_have_value(str(float(x) + 3))
        page.locator('#rect-x').fill(x)
        select(second)
        assert len(dialogs) == baseline + 1
        checks.append('Moved text is protected; restoring its position removes the warning')

        baseline = len(dialogs)
        page.locator('#font-size').fill('')
        select(first)
        assert len(dialogs) == baseline + 1
        expect(page.locator('#font-size')).to_have_value('')
        page.locator('#cancel-edit').click()
        checks.append('Invalid or incomplete form input is preserved when discarding is canceled')

        select(first)
        page.locator('#btn-export').click()
        expect(page.locator('#export-dialog')).to_be_visible()
        page.locator('[data-close-dialog="export-dialog"]').first.click()
        assert len(dialogs) == baseline + 1
        checks.append('An untouched selection does not block opening Export')

        draw(page, 'text', [70, 200, 260, 230])
        baseline = len(dialogs)
        page.locator('[data-tool="select"]').click()
        assert len(dialogs) == baseline
        draw(page, 'text', [70, 200, 260, 230])
        page.locator('#text-content').fill('新文字草稿')
        page.locator('[data-tool="select"]').click()
        assert len(dialogs) == baseline + 1
        expect(page.locator('#text-content')).to_have_value('新文字草稿')
        page.locator('#cancel-edit').click()
        checks.append('Empty new text boxes can be left freely; typed new text stays protected')

        draw(page, 'crop', [20, 20, 550, 790])
        baseline = len(dialogs)
        page.locator('[data-tool="select"]').click()
        assert len(dialogs) == baseline + 1
        expect(page.locator('#crop-options')).to_be_visible()
        page.locator('#cancel-edit').click()
        checks.append('A newly drawn crop remains an actionable pending edit')

        page.locator('[data-tool="select"]').click()
        select(first)
        overlay = page.locator('#overlay').bounding_box()
        width, height = page.evaluate('[S.data.width,S.data.height]')
        page.mouse.click(overlay['x'] + 20 / width * overlay['width'],
                         overlay['y'] + 220 / height * overlay['height'])
        expect(page.locator('#draft-panel')).to_be_hidden()
        assert len(dialogs) == baseline + 1
        checks.append('Clicking blank page space clears an untouched selection without a dialog')

        select(first)
        page.locator('.page-card').first.drag_to(page.locator('.page-card').nth(1))
        wait_state(page, 'S.pages[0].index === 1')
        assert len(dialogs) == baseline + 1
        checks.append('An untouched text selection does not block dragging pages to reorder')

        assert errors == [], errors
        checks.append('No uncaught JavaScript errors')
        page.close(run_before_unload=False)
        browser.close()

    artifacts = ROOT / 'test-artifacts'
    artifacts.mkdir(exist_ok=True)
    (artifacts / 'draft-browser-checks.txt').write_text('\n'.join(checks), encoding='utf-8')
    for check in checks:
        print('PASS', check, flush=True)
    print(f'Draft browser checks: {len(checks)} passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    main(parser.parse_args().url)
