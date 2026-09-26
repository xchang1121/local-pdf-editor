"""Single-user/local-first HTTP app. Run with ONE uvicorn worker."""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import secrets
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import engine
from . import images as image_tools
from .schemas import ExportRequest, PreviewRequest

BASE = Path(__file__).resolve().parent
MAX_UPLOAD = 50 * 1024 * 1024
MAX_SESSION_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
SESSION_TTL = 4 * 3600
log = logging.getLogger("pdf-studio")


@dataclass
class Session:
    touched: float = field(default_factory=time.monotonic)
    sources: dict[str, bytes] = field(default_factory=dict)
    images: dict[str, bytes] = field(default_factory=dict)

    @property
    def size(self):
        return sum(map(len, self.sources.values())) + sum(map(len, self.images.values()))


@asynccontextmanager
async def lifespan(app):
    app.state.sessions = {}
    # All PyMuPDF work happens in this one separate process, never in concurrent
    # FastAPI request threads. Use process supervision for untrusted deployments.
    app.state.pdf_pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    async def sweep_expired():
        while True:
            await asyncio.sleep(60)
            prune_sessions(app.state.sessions)

    sweeper = asyncio.create_task(sweep_expired())
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        app.state.pdf_pool.shutdown(wait=True, cancel_futures=True)
        app.state.sessions.clear()


app = FastAPI(title="PDF Studio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
allowed = os.getenv("PDF_STUDIO_HOSTS", "127.0.0.1,localhost,[::1]").split(",")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in allowed])


class BodyLimitMiddleware:
    """Bound request bodies before multipart parsing; also handles chunked input."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        maximum = ({'/api/import': MAX_UPLOAD, '/api/images': image_tools.MAX_IMAGE_BYTES}.get(scope['path'], 7 * 1024 * 1024)
                   + 1024 * 1024)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = maximum + 1
        if declared > maximum:
            return await JSONResponse({"detail": "请求过大；单个 PDF 上限为 50 MB，图片上限为 20 MB。"}, 413)(scope, receive, send)
        size = 0
        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > maximum:
                return await JSONResponse({"detail": "请求超过大小限制。"}, 413)(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        payload = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


app.add_middleware(BodyLimitMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # No CORS grant. Reject cross-origin mutations even before session checking.
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        if origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
            return JSONResponse({"detail": "不允许跨站请求。"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def prune_sessions(sessions: dict[str, Session]):
    cutoff = time.monotonic() - SESSION_TTL
    for token, value in list(sessions.items()):
        if value.touched < cutoff:
            del sessions[token]


def cleanup(request: Request):
    prune_sessions(request.app.state.sessions)


async def get_session(request: Request, x_session_token: str = Header(default="")) -> Session:
    cleanup(request)
    session = request.app.state.sessions.get(x_session_token)
    if session is None:
        raise HTTPException(401, "会话不存在或已过期。请刷新网页并重新导入 PDF。")
    session.touched = time.monotonic()
    return session


async def pdf_job(request, fn, *args):
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(request.app.state.pdf_pool, partial(fn, *args))
    except engine.EditError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.exception("PDF operation failed")
        raise HTTPException(422, "PDF 处理失败。文件可能含有不支持的内容；原文件未修改。") from exc


@app.get("/api/session")
async def session_status(request: Request, x_session_token: str = Header(default="")):
    cleanup(request)
    sessions = request.app.state.sessions
    current = sessions.get(x_session_token)
    if current:
        current.touched = time.monotonic()
        return {"token": x_session_token, "resumed": True, "sources": list(current.sources), "images": list(current.images)}
    if len(sessions) >= 32:
        raise HTTPException(503, "本地会话数已达上限，请关闭闲置会话或重启服务。")
    token = secrets.token_urlsafe(32)
    sessions[token] = Session()
    return {"token": token, "resumed": False, "sources": [], "images": []}


@app.delete("/api/session")
async def delete_session(request: Request, session: Session = Depends(get_session)):
    session.sources.clear()
    session.images.clear()
    request.app.state.sessions.pop(request.headers.get("x-session-token"), None)
    return {"ok": True}


@app.post("/api/import")
async def upload(request: Request, file: UploadFile = File(...), session: Session = Depends(get_session)):
    try:
        raw = await file.read(MAX_UPLOAD + 1)
    finally:
        await file.close()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "单个 PDF 最大支持 50 MB。")
    if session.size + len(raw) > MAX_SESSION_BYTES:
        raise HTTPException(413, "本会话文件总量超过 200 MB，请导出后新建会话。")
    result = await pdf_job(request, engine.import_pdf, raw)
    data = result.pop("data")
    if session.size + len(data) > MAX_SESSION_BYTES:
        raise HTTPException(413, "本会话规范化文件总量超过 200 MB。")
    if sum(s.size for s in request.app.state.sessions.values()) + len(data) > MAX_TOTAL_BYTES:
        raise HTTPException(503, "服务器文件内存已达上限，请结束闲置会话。")
    source_id = secrets.token_urlsafe(18)
    session.sources[source_id] = data
    filename = (file.filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1][:180]
    return {"source": source_id, "filename": filename, "pages": result["pages"],
            "notice": "已导入静态页面；表单和批注保留外观，不保留交互。书签、链接、附件、签名验证信息不予保留。"}


@app.post('/api/images')
async def upload_image(request: Request, file: UploadFile = File(...), session: Session = Depends(get_session)):
    try:
        raw = await file.read(image_tools.MAX_IMAGE_BYTES + 1)
    finally:
        await file.close()
    if len(raw) > image_tools.MAX_IMAGE_BYTES:
        raise HTTPException(413, '单张图片最大支持 20 MB。')
    result = await pdf_job(request, image_tools.import_image, raw)
    data = result.pop('data')
    if session.size + len(data) > MAX_SESSION_BYTES:
        raise HTTPException(413, '本会话文件总量超过 200 MB，请导出后新建会话。')
    if sum(s.size for s in request.app.state.sessions.values()) + len(data) > MAX_TOTAL_BYTES:
        raise HTTPException(503, '服务器文件内存已达上限，请结束闲置会话。')
    asset = secrets.token_urlsafe(18)
    session.images[asset] = data
    filename = (file.filename or 'image.png').replace('\\', '/').rsplit('/', 1)[-1][:180]
    return {**result, 'asset': asset, 'filename': filename}


def require_images(session, pages):
    result = {}
    for page in pages:
        for op in page.ops:
            if op.kind == 'image_replace':
                if op.asset not in session.images:
                    raise HTTPException(404, '图片不属于当前会话，或会话已经过期。')
                result[op.asset] = session.images[op.asset]
    return result


def require_source(session, source):
    if source and source not in session.sources:
        raise HTTPException(404, "源文件不属于当前会话，或会话已经过期。")
    return session.sources.get(source)


@app.post("/api/preview")
async def preview(payload: PreviewRequest, request: Request, session: Session = Depends(get_session)):
    source = require_source(session, payload.page.source)
    images = require_images(session, [payload.page])
    return await pdf_job(request, engine.preview_page, payload.page.model_dump(), source, payload.scale, payload.text, images)


@app.post("/api/export")
async def export(payload: ExportRequest, request: Request, session: Session = Depends(get_session)):
    sources = {}
    for page in payload.pages:
        if page.source:
            sources[page.source] = require_source(session, page.source)
    images = require_images(session, payload.pages)
    data = await pdf_job(request, engine.export_pdf, [p.model_dump() for p in payload.pages], sources, payload.mode, payload.dpi, images)
    name = payload.filename.replace("\\", "/").rsplit("/", 1)[-1].strip() or "edited.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=edited.pdf; filename*=UTF-8''{quote(name)}"
    })


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/demo.pdf")
async def demo():
    return FileResponse(BASE.parent / "examples" / "demo.pdf", media_type="application/pdf")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
