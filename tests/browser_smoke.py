"""Run against an already-running local server.

    python -m playwright install chromium
    python tests/browser_smoke.py --url http://127.0.0.1:8000

Includes real mouse interaction and validates the downloaded PDFs.
"""
from __future__ import annotations
import argparse
import base64
import json
import httpx
import shutil
import time
from pathlib import Path

import pymupdf as fitz
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


def wait_state(page, expression, timeout=10000):
    # wait_for_function uses in-page eval, which the app correctly disallows.
    # Evaluate through the automation protocol without weakening the site's CSP.
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if page.evaluate('() => (' + expression + ')'):
            return
        page.wait_for_timeout(50)
    raise AssertionError('Timed out waiting for browser state: ' + expression)


def ready(page):
    wait_state(page, "!S.busy && S.displayPage === S.active && S.data !== null")


def draw(page, tool, rectangle):
    page.locator(f'[data-tool="{tool}"]').click()
    box=page.locator('#overlay').bounding_box()
    w,h=page.evaluate('[S.data.width,S.data.height]')
    x0,y0,x1,y1=rectangle
    # Scroll the page to the selection area when necessary.
    top=page.locator('#canvas-scroll').evaluate('(e) => e.scrollTop')
    page.locator('#canvas-scroll').evaluate('(e) => e.scrollTop = 0')
    box=page.locator('#overlay').bounding_box()
    def point(x,y):return (box['x']+x/w*box['width'],box['y']+y/h*box['height'])
    a,b=point(x0,y0),point(x1,y1)
    page.mouse.move(*a);page.mouse.down();page.mouse.move(*b,steps=10);page.mouse.up()
    expect(page.locator('#draft-panel')).to_be_visible()


def setup_offline(page, url, storage=None):
    """Offline DOM harness for environments forbidding browser URL navigation.

    Browser traffic is NOT enabled or policies changed. In-memory fetch responses
    come from Python HTTP calls to this local app only. Exported Blob bytes are
    inspected directly, not saved by a browser navigation/download subsystem.
    """
    def local_request(payload):
        path=payload['path']
        if not (path.startswith('/api/') or path=='/demo.pdf'):
            raise ValueError('Offline harness allows only local app API/demo paths')
        kwargs={'headers':payload.get('headers',{})}
        if payload.get('file'):
            file=payload['file']
            kwargs['files']={'file':(file['name'],base64.b64decode(file['data']),'application/pdf')}
        elif payload.get('body') is not None:
            kwargs['content']=payload['body']
        response=httpx.request(payload.get('method','GET'),url+path,timeout=120,**kwargs)
        return {'status':response.status_code,'headers':dict(response.headers),
                'body':base64.b64encode(response.content).decode()}
    page.expose_function('__local_request',local_request)
    load_offline(page,storage or {})


def load_offline(page, storage):
    html=(ROOT/'app/static/index.html').read_text(encoding='utf-8')
    html=html.replace('<link rel="stylesheet" href="/static/styles.css">','').replace('<script src="/static/app.js" defer></script>','')
    page.set_content(html)
    page.add_style_tag(content=(ROOT/'app/static/styles.css').read_text(encoding='utf-8'))
    page.evaluate("""(stored)=>{
      const values={...stored};
      Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
        getItem:k=>values[k]??null,setItem:(k,v)=>{values[k]=String(v)},
        removeItem:k=>{delete values[k]},clear:()=>{for(const k in values)delete values[k]},
        dump:()=>({...values})
      }});
      if(!crypto.randomUUID)crypto.randomUUID=()=>[...crypto.getRandomValues(new Uint8Array(18))].map(x=>x.toString(16).padStart(2,'0')).join('');
      const toBase64=bytes=>{let out='';for(let i=0;i<bytes.length;i+=16384)out+=String.fromCharCode(...bytes.subarray(i,i+16384));return btoa(out)};
      window.fetch=async(path,opts={})=>{
        const h={};new Headers(opts.headers||{}).forEach((v,k)=>h[k]=v);
        const payload={path:String(path),method:opts.method||'GET',headers:h};
        if(opts.body instanceof FormData){const f=opts.body.get('file');payload.file={name:f.name,data:toBase64(new Uint8Array(await f.arrayBuffer()))};}
        else if(opts.body!==undefined)payload.body=opts.body;
        const result=await window.__local_request(payload);
        return new Response(Uint8Array.from(atob(result.body),c=>c.charCodeAt(0)),{status:result.status,headers:result.headers});
      };
      window.__downloads=[];
      const blobs=new Map(),create=URL.createObjectURL.bind(URL);
      URL.createObjectURL=blob=>{const url=create(blob);blobs.set(url,blob);return url};
      HTMLAnchorElement.prototype.click=function(){
        const blob=blobs.get(this.href),name=this.download;
        if(blob)blob.arrayBuffer().then(buf=>window.__downloads.push({name,data:toBase64(new Uint8Array(buf))}));
      };
    }""",storage)
    page.add_script_tag(content=(ROOT/'app/static/app.js').read_text(encoding='utf-8'))


def export_blob(page, target, offline):
    if offline:
        count=page.evaluate('window.__downloads.length')
        page.locator('#confirm-export').click()
        wait_state(page, f'window.__downloads.length > {count}', timeout=120000)
        item=page.evaluate('window.__downloads.at(-1)')
        target.write_bytes(base64.b64decode(item['data']))
    else:
        with page.expect_download() as download:
            page.locator('#confirm-export').click()
        download.value.save_as(str(target))


def main(url, offline=False):
    docs=ROOT/'docs';docs.mkdir(exist_ok=True)
    artifacts=ROOT/'test-artifacts';artifacts.mkdir(exist_ok=True)
    class Checks(list):
        def append(self, text):
            super().append(text); print('PASS',text,flush=True)
    checks=Checks()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1512,'height':1000},device_scale_factor=1,accept_downloads=True)
        page=context.new_page();page.set_default_timeout(10000);errors=[]
        page.on('pageerror',lambda error:(errors.append(str(error)),print('JS ERROR',error,flush=True)))
        page.on('dialog',lambda dialog:dialog.accept())
        if offline:setup_offline(page,url)
        else:page.goto(url)
        wait_state(page, "S.token.length > 0")
        expect(page.locator('#empty-state')).to_be_visible()
        page.screenshot(path=str(docs/'screenshot-welcome.png'))
        checks.append('Welcome UI and local session bootstrap')
        page.locator('#btn-demo').click();ready(page)
        expect(page.locator('.page-card')).to_have_count(3)
        expect(page.locator('.text-hit')).not_to_have_count(0)
        checks.append('Import and thumbnail rendering for three-page PDF')

        page.get_by_role('button',name='编辑：把想法，变成下一步。',exact=True).click()
        expect(page.locator('#text-content')).to_have_value('把想法，变成下一步。')
        page.locator('#text-content').fill('2026 项目进展简报')
        page.locator('#font-size').fill('26')
        page.locator('#rect-w').fill('470')
        page.locator('#apply-edit').click();ready(page)
        assert page.evaluate("S.data.blocks.some(b => b.text.includes('2026'))")
        page.get_by_role('button', name='编辑：2026 项目进展简报', exact=True).click()
        page.locator('#toast').wait_for(state='hidden', timeout=10000)
        page.screenshot(path=str(docs/'screenshot-editor.png'))
        page.locator('#cancel-edit').click()
        checks.append('Click original text, change Chinese/Latin content, font size and box width')

        page.locator('#undo').click();ready(page)
        assert page.evaluate("S.data.blocks.some(b => b.text.includes('把想法'))")
        page.locator('#redo').click();ready(page)
        assert page.evaluate("S.data.blocks.some(b => b.text.includes('2026'))")
        checks.append('Undo and redo a real text replacement')

        draw(page,'text',[48,190,515,215])
        page.locator('#text-content').fill('新增说明：本文件已完成修改。')
        page.locator('#font-size').fill('12')
        page.locator('#apply-edit').click();ready(page)
        assert page.evaluate("S.data.blocks.some(b => b.text.includes('新增说明'))")
        checks.append('Draw a text box, insert Chinese, and re-render')

        draw(page,'crop',[20,20,570,790])
        page.locator('#apply-edit').click();ready(page)
        w,h=page.evaluate('[S.data.width,S.data.height]')
        assert abs(w-550)<.1 and abs(h-770)<.1,(w,h)
        page.locator('#rotate').click();ready(page)
        w,h=page.evaluate('[S.data.width,S.data.height]')
        assert abs(w-770)<.1 and abs(h-550)<.1,(w,h)
        page.locator('#undo').click();ready(page)
        checks.append('Mouse crop, rotation of cropped page, and undo')

        # Duplicate / delete via actual page controls.
        page.locator('#page-duplicate').click();ready(page)
        expect(page.locator('.page-card')).to_have_count(4)
        page.locator('#page-delete').click();ready(page)
        expect(page.locator('.page-card')).to_have_count(3)
        checks.append('Duplicate and delete pages')

        # Drag two visible thumbnails. Use controls/auto-scroll for long lists.
        page.locator('.page-card').first.drag_to(page.locator('.page-card').nth(1))
        wait_state(page, 'S.pages[0].index === 1')
        page.locator('.page-card').last.click();ready(page)
        assert not page.evaluate('S.data.has_text')
        assert '未检测到文字层' in page.locator('#text-status').inner_text()
        checks.append('HTML drag-and-drop reorder and image-only page detection')
        page.locator('.page-card').nth(1).click();ready(page)

        # Export current order, preserving selectable text.
        page.locator('#btn-export').click()
        page.locator('#export-name').fill('browser-result.pdf')
        path=artifacts/'browser-result.pdf'
        export_blob(page,path,offline)
        with fitz.open(path) as result:
            assert len(result)==3
            assert 'SECRET-DEMO' in result[0].get_text()
            assert not result[2].get_text().strip()
            assert '2026' in result[1].get_text()
            assert '把想法，变成下一步' not in result[1].get_text()
            assert '新增说明' in result[1].get_text()
        checks.append('Exported PDF bytes contain actual edited text and new page order')

        # Area removal on the formerly second page (now first).
        page.locator('.page-card').first.click();ready(page)
        draw(page,'redact',[60,315,430,368])
        page.locator('#apply-edit').click();ready(page)
        assert not page.evaluate("S.data.blocks.some(b=>b.text.includes('SECRET-DEMO'))")
        checks.append('Redact sample field through mouse selection and confirm')

        page.locator('#btn-export').click()
        page.locator('#export-name').fill('raster-result.pdf')
        page.locator('#export-range').fill('2')
        page.locator('input[name="export-mode"][value="raster"]').check()
        page.locator('#export-dpi').select_option('120')
        raster=artifacts/'raster-result.pdf'
        export_blob(page,raster,offline)
        with fitz.open(raster) as result:
            assert len(result)==1 and not result[0].get_text().strip()
            assert len(result[0].get_images())==1
        checks.append('Extract one page as an image-only PDF')

        if offline:
            saved=page.evaluate('sessionStorage.dump()')
            page.reload();load_offline(page,saved)
        else:page.reload()
        ready(page)
        expect(page.locator('.page-card')).to_have_count(3)
        checks.append('Same-tab refresh restores recipe and session')

        # Responsive layout check, not a substitute for Safari / real-device QA.
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(250)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(docs/'screenshot-mobile.png'))
        checks.append('390px Chromium responsive viewport: no document-level horizontal overflow')
        assert errors==[],errors
        checks.append('No uncaught browser JavaScript exceptions')
        browser.close()
    print('Mode: offline DOM + local HTTP bridge (native navigation/download not tested)' if offline else 'Mode: native browser HTTP + download')
    print('\n'.join('PASS  '+c for c in checks))
    (artifacts/'browser-checks.txt').write_text('\n'.join(checks),encoding='utf-8')
    print(f'Browser checks: {len(checks)} passed')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--offline-bridge',action='store_true')
    args=parser.parse_args()
    main(args.url,args.offline_bridge)
