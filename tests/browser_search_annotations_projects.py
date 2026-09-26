"""Native browser search/review/project round trip, including a fresh session."""
import argparse
from collections import deque
from pathlib import Path
import sys
from zipfile import ZipFile
import json

import pymupdf as fitz
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from browser_smoke import ready, wait_state, draw
from test_search_annotations_projects import text_source
from test_images import png, source_pdf


def main(url):
    artifacts = ROOT / 'test-artifacts';artifacts.mkdir(exist_ok=True)
    fixture = artifacts / 'search-review.pdf'
    with fitz.open(stream=text_source(),filetype='pdf') as doc, fitz.open(stream=source_pdf(shared=True),filetype='pdf') as pictures:
        doc.insert_pdf(pictures);doc.save(fixture)
    image = artifacts / 'project-blue.png';image.write_bytes(png('blue'))
    invalid = artifacts / 'invalid.pdfstudio';invalid.write_bytes(b'not a project')
    checks, errors, dialogs = [], [], []
    answers = deque()

    def dialog_handler(dialog):
        if dialog.type == 'beforeunload':dialog.accept();return
        dialogs.append(dialog.message)
        assert answers, f'Unexpected dialog: {dialog.message}'
        if answers.popleft():dialog.accept()
        else:dialog.dismiss()

    def lookup(page, query, count):
        page.locator('#search-query').fill(query)
        page.locator('#run-search').click()
        wait_state(page,f'!S.busy && F.search !== null && F.search.matches.length==={count}')
        ready(page)

    def note_click(page,x,y):
        box = page.locator('#overlay').bounding_box()
        w,h = page.evaluate('[S.data.width,S.data.height]')
        page.mouse.click(box['x']+x/w*box['width'],box['y']+y/h*box['height'])

    with sync_playwright() as runner:
        browser=runner.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1512,'height':1000},accept_downloads=True)
        page=context.new_page();page.on('dialog',dialog_handler);page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url);wait_state(page,'!!S.token')
        page.locator('#file-input').set_input_files(str(fixture));ready(page)
        page.keyboard.press('Control+f')
        expect(page.locator('#search-panel')).to_be_visible()
        lookup(page,'2025',4)
        expect(page.locator('.search-result')).to_have_count(4)
        expect(page.locator('.search-hit')).to_have_count(2)
        page.locator('.search-result').nth(3).click();ready(page)
        assert page.evaluate('S.pages.findIndex(p=>p.id===S.active)') == 1
        checks.append('Literal search lists page/snippet results, navigates and highlights the selected occurrence')

        page.locator('#search-case').check();lookup(page,'Alpha',2)
        page.locator('#search-scope').select_option('page');lookup(page,'Alpha',1)
        page.locator('#tab-pages').click();page.locator('.page-card').nth(1).click();ready(page)
        assert page.evaluate('F.search===null')
        page.locator('#tab-search').click();page.locator('#search-scope').select_option('all')
        page.locator('#search-case').uncheck();lookup(page,'A+B',2)
        checks.append('Case sensitivity, current-page scope, scope invalidation and literal punctuation work')

        lookup(page,'2025',4)
        page.locator('#replace-content').fill('2026');page.locator('#replace-one').click();ready(page)
        wait_state(page,'F.search?.matches.length===3')
        assert page.evaluate('S.timeline.length') == 3
        page.locator('#replace-content').fill('2030');page.locator('#replace-all').click();ready(page)
        wait_state(page,'F.search?.matches.length===0')
        assert page.evaluate('S.timeline.length') == 4
        page.locator('#undo').click();ready(page);lookup(page,'2025',3)
        page.locator('#redo').click();ready(page)
        checks.append('Single and all-occurrence replacements update real PDF text; a batch is one undoable step')

        lookup(page,'2030',3)
        before=page.evaluate('JSON.stringify(S.pages)');history=page.evaluate('S.timeline.length')
        page.locator('#replace-content').fill('Very long replacement text ' * 30)
        page.locator('#replace-fit').uncheck();page.locator('#replace-all').click();ready(page)
        expect(page.locator('#toast')).to_contain_text('整批未提交')
        assert page.evaluate('JSON.stringify(S.pages)') == before
        assert page.evaluate('S.timeline.length') == history
        expect(page.locator('#replace-content')).not_to_be_empty()
        checks.append('Overflow rejects the whole batch and preserves the replacement input and history')

        page.locator('#tab-pages').click();page.locator('.page-card').first.click();ready(page)
        draw(page,'highlight',[30,39,235,62])
        page.locator('#annotation-comment').fill('核对这行的年份。')
        page.locator('#apply-edit').click();ready(page)
        assert page.evaluate('S.data.annotations[0].comment') == '核对这行的年份。'
        page.locator('[data-tool="note"]').click();note_click(page,260,155)
        page.locator('#annotation-comment').fill('请检查 <数据> & 来源。')
        page.locator('#apply-edit').click();ready(page)
        expect(page.locator('.annotation-list-item')).to_have_count(2)
        page.locator('.annotation-list-item').nth(1).click()
        expect(page.locator('#draft-badge')).to_be_hidden()
        page.locator('#annotation-comment').fill('已补充来源，等待复核。')
        page.locator('#annotation-color').fill('#44aaff')
        page.locator('#apply-edit').click();ready(page)
        page.locator('.annotation-list-item').first.click();page.locator('#remove-annotation').click();ready(page)
        assert page.evaluate('S.data.annotations.length') == 1
        page.locator('#undo').click();ready(page)
        assert page.evaluate('S.data.annotations.length') == 2
        checks.append('Highlights and notes can be added, edited, recolored, deleted and restored through history')

        # Verify native annotation metadata in a real downloaded PDF.
        page.locator('#btn-export').click()
        with page.expect_download() as downloaded:page.locator('#confirm-export').click()
        pdf=artifacts/'review-export.pdf';downloaded.value.save_as(str(pdf));ready(page)
        with fitz.open(pdf) as doc:
            text=''.join(p.get_text() for p in doc)
            assert text.count('2026')==1 and text.count('2030')==3 and '2025' not in text
            p=doc[0];items=list(p.annots())
            assert len(items)==2 and items[1].info['content']=='已补充来源，等待复核。'
            assert items[0].type[0]==fitz.PDF_ANNOT_HIGHLIGHT
        checks.append('Downloaded standard PDF retains selectable edited text and native Chinese annotation contents')
        page.locator('#toast').evaluate('(e)=>e.hidden=true')
        page.screenshot(path=str(artifacts/'search-review-desktop.png'))

        page.locator('.annotation-list-item').nth(1).click();page.locator('#annotation-comment').fill('尚未应用的便签')
        page.keyboard.press('Control+s')
        expect(page.locator('#toast')).to_contain_text('先应用或取消')
        expect(page.locator('#annotation-comment')).to_have_value('尚未应用的便签')
        page.locator('#cancel-edit').click()
        page.locator('.page-card').nth(2).click();ready(page)
        page.locator('[data-tool="image"]').click();page.locator('.image-list-item').first.click()
        page.locator('#image-file-input').set_input_files(str(image));wait_state(page,'!S.busy && !!S.draft.asset')
        page.locator('#apply-edit').click();ready(page)
        page.locator('#undo').click();ready(page)
        old_sources=page.evaluate('[...new Set(S.pages.map(p=>p.source))]')
        cursor=page.evaluate('S.historyCursor');steps=page.evaluate('S.timeline.length')
        with page.expect_download() as downloaded:page.keyboard.press('Control+s')
        project=artifacts/'complete-project.pdfstudio';downloaded.value.save_as(str(project));ready(page)
        assert not page.evaluate('hasUnsavedChanges()')
        with ZipFile(project) as z:
            manifest=json.loads(z.read('manifest.json'))
            assert len(manifest['assets']['images'])==1
            assert manifest['project']['history']['cursor']==cursor
        checks.append('Project save blocks unapplied drafts and packages the undone image asset and redo history')

        # Independent browser context has no sessionStorage and no access to the
        # old session's source IDs; all restoration must come from the file.
        fresh=browser.new_context(viewport={'width':1512,'height':1000},accept_downloads=True)
        restored=fresh.new_page();restored.on('dialog',dialog_handler);restored.on('pageerror',lambda error:errors.append(str(error)))
        restored.goto(url);wait_state(restored,'!!S.token')
        assert restored.evaluate('S.pages.length')==0
        restored.locator('#project-menu summary').click()
        with restored.expect_file_chooser() as chooser:restored.locator('#btn-open-project').click()
        chooser.value.set_files(str(project));ready(restored)
        assert restored.evaluate('S.pages.length')==3
        assert restored.evaluate('S.historyCursor')==cursor and restored.evaluate('S.timeline.length')==steps
        assert restored.evaluate('S.pages[0].source') not in old_sources
        expect(restored.locator('#redo')).to_be_enabled()
        restored.locator('#redo').click();ready(restored)
        assert restored.evaluate("S.pages[2].ops.at(-1).kind")=='image_replace'
        restored.locator('.page-card').first.click();ready(restored)
        assert restored.evaluate('S.data.annotations.length')==2
        checks.append('Opening the project in a fresh session restores sources, notes, active history and image redo')

        restored.locator('.annotation-list-item').nth(1).click();restored.locator('#annotation-comment').fill('不能丢失的草稿')
        before=restored.evaluate('JSON.stringify(S.pages)')
        answers.append(True)
        restored.locator('#project-file-input').set_input_files(str(invalid))
        wait_state(restored,'!S.busy')
        expect(restored.locator('#toast')).to_contain_text('无法打开项目')
        assert restored.evaluate('JSON.stringify(S.pages)')==before
        expect(restored.locator('#annotation-comment')).to_have_value('不能丢失的草稿')
        restored.locator('#cancel-edit').click()
        checks.append('A corrupt project does not replace the current document or destroy its pending draft')

        with restored.expect_download() as downloaded:restored.keyboard.press('Control+s')
        downloaded.value.save_as(str(artifacts/'resaved-project.pdfstudio'));ready(restored)
        restored.reload();ready(restored)
        assert restored.evaluate('S.projectMode && !S.projectDirty')
        restored.locator('#btn-search').click();lookup(restored,'2030',3)
        for width,height in [(1024,768),(390,844)]:
            restored.set_viewport_size({'width':width,'height':height})
            assert restored.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
            expect(restored.locator('#run-search')).to_be_visible()
        restored.screenshot(path=str(artifacts/'search-review-mobile.png'))
        checks.append('Project save state survives refresh; search controls remain usable at 1024 and 390 pixels')
        assert not errors,errors
        assert not answers and len(dialogs)==1,dialogs
        checks.append('No uncaught browser errors or unexpected discard prompts during the complete round trip')
        fresh.close();context.close();browser.close()
    for check in checks:print('PASS',check)
    print(f'{len(checks)} search/annotation/project browser checks passed')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000')
    main(parser.parse_args().url)
