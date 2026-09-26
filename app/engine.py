"""PDF work runs in ONE dedicated worker process; no concurrent MuPDF threads.

A source is immutable. Replaying a page recipe builds a temporary, one-page PDF.
Preview and export use this same pipeline. PDF coordinates are in points (1/72 in),
origin at the current visible page's top-left. Rotation is normalized after each
rotation operation so future hit tests and crops use that same coordinate frame.
"""
from __future__ import annotations

import base64
import html
import math
import re
from collections import Counter
from statistics import median
from typing import Any

import pymupdf as fitz

from .schemas import PageSpec
from .errors import EditError
from . import images as image_tools

MAX_PAGES = 300
MAX_SIDE = 14400
MAX_RENDER_PIXELS = 12_000_000
MAX_EXPORT_PIXELS = 180_000_000
MAX_OUTPUT_BYTES = 200 * 1024 * 1024


def rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[i:i+2], 16) / 255 for i in (1, 3, 5))


def check_page_size(page: fitz.Page) -> None:
    w, h = page.rect.width, page.rect.height
    if not all(math.isfinite(n) and 8 <= n <= MAX_SIDE for n in (w, h)):
        raise EditError("页面尺寸不受支持；每条边需要在 8–14400 pt 范围内。")


def normalize_rotation(page: fitz.Page) -> None:
    """Keep cropped/offset-page appearance when baking a PDF page rotation.

    PyMuPDF 1.26.7's remove_rotation can reset an existing CropBox for quarter
    turns. Temporarily expose the full MediaBox, transform the saved CropBox
    in that full-page frame, remove rotation, then restore the transformed crop.
    Note: CropBox uses a top-left y origin even for an offset PDF MediaBox.
    """
    if not page.rotation:
        return
    crop, media = page.cropbox, page.mediabox
    full = fitz.Rect(media.x0, 0, media.x1, media.height)
    page.set_cropbox(full)
    off = page.cropbox_position
    target = (crop - (off.x, off.y, off.x, off.y)) * page.rotation_matrix
    page.remove_rotation()
    off = page.cropbox_position
    page.set_cropbox(target + (off.x, off.y, off.x, off.y))


def import_pdf(raw: bytes) -> dict[str, Any]:
    """Decode a PDF and rebuild static pages; do not execute PDF JavaScript."""
    if b"%PDF-" not in raw[:1024]:
        raise EditError("文件不是有效的 PDF。")
    source = None
    clean = None
    try:
        source = fitz.open(stream=raw, filetype="pdf")
        if source.needs_pass:
            raise EditError("此 PDF 有打开密码。请先在有权限的阅读器中另存为未加密 PDF。")
        if not 1 <= len(source) <= MAX_PAGES:
            raise EditError(f"每个文件需要包含 1–{MAX_PAGES} 页。")
        for page in source:
            check_page_size(page)
        # Preserve visible annotation/widget appearances, not their interactivity.
        # A new document drops source-level attachments, outlines and metadata.
        source.bake(annots=True, widgets=True)
        clean = fitz.open()
        clean.insert_pdf(source, links=False, annots=False, widgets=False)
        info = []
        for page in clean:
            normalize_rotation(page)
            check_page_size(page)
            info.append({"width": page.rect.width, "height": page.rect.height})
        clean.set_metadata({})
        data = clean.tobytes(garbage=4, deflate=True, clean=True)
        if len(data) > MAX_OUTPUT_BYTES:
            raise EditError("规范化后的 PDF 过大，超过 200 MB。")
        return {"data": data, "pages": info}
    except EditError:
        raise
    except Exception as exc:
        raise EditError("无法解析此 PDF；它可能损坏，或含有本版不支持的结构。") from exc
    finally:
        if clean is not None:
            clean.close()
        if source is not None:
            source.close()


def bounded_rect(values, page: fitz.Page) -> fitz.Rect:
    r = fitz.Rect(values)
    if not all(math.isfinite(v) for v in r) or r.width < 0.2 or r.height < 0.2:
        raise EditError("选区太小或坐标无效。")
    bounds = page.rect
    # The browser rounds controls to two decimals. Tolerate only that rounding.
    if r.x0 < -0.1 or r.y0 < -0.1 or r.x1 > bounds.width + 0.1 or r.y1 > bounds.height + 0.1:
        raise EditError("选区超出页面。请调整位置或尺寸。")
    return r & bounds


def _redact(page: fitz.Page, rects, *, text_only=False, color="#000000") -> None:
    for rect in rects:
        page.add_redact_annot(rect, fill=False if text_only else rgb(color), cross_out=False)
    page.apply_redactions(images=0 if text_only else 2,
                          graphics=0 if text_only else 2,
                          text=0)


def _replacement_layout_rect(r: fitz.Rect, page: fitz.Page, font_size: float) -> fitz.Rect:
    """Allow bounded fallback-font tolerance, without changing text erasure.

    Used only after glyph measurement fails in the exact destination. Keep the
    anchor stable and stop the extra room at neighboring text or the page edge.
    """
    extra_x = min(18.0, max(6.0, font_size * 0.65))
    extra_y = min(14.0, max(4.0, font_size * 0.45))
    result = fitz.Rect(
        r.x0, r.y0,
        min(page.rect.width, r.x1 + extra_x),
        min(page.rect.height, r.y1 + extra_y),
    )
    # Called after the selected text has been removed. Automatic tolerance must
    # not consume the next table cell / line. User-drawn destinations stay valid.
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                other = fitz.Rect(span["bbox"])
                if other.y0 < result.y1 and other.y1 > r.y0 and other.x0 >= r.x1 - .05:
                    result.x1 = min(result.x1, max(r.x1, other.x0 - .5))
                if other.x0 < result.x1 and other.x1 > r.x0 and other.y0 >= r.y1 - .05:
                    result.y1 = min(result.y1, max(r.y1, other.y0 - .5))
    return result


def _font_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", re.sub(r"^[A-Z]{6}\+", "", name).lower())
    # PDF resource names and text-span PostScript names can differ for the same
    # font, e.g. "Arial Regular" / "ArialMT", "SimSun Regular" / "SimSun".
    key = re.sub(r"(?:regular|psmt|mt)$", "", key)
    return re.sub(r"ps(?=bold|italic|$)", "", key)


def _text_font(page, op, warnings):
    """Reuse an embedded / Base-14 font only through this document's resources."""
    if op.style.family != "original":
        return op.style.family, "", None
    wanted = _font_key(op.font or "")
    seen = set()
    for xref, _ext, _type, name, *_rest in page.get_fonts(full=True):
        if xref in seen or not wanted:
            continue
        seen.add(xref)
        # Do not decode every font in a large CJK document just to find a match.
        if _font_key(name) != wanted:
            continue
        try:
            _, _, _, buffer = page.parent.extract_font(xref)
            font = fitz.Font(fontbuffer=buffer) if buffer else fitz.Font(name)
            if bool(font.flags["bold"]) != op.style.bold or bool(font.flags["italic"]) != op.style.italic:
                warnings.append("加粗或斜体已改变，使用所选样式的替代字体。")
                break
            if any(not font.has_glyph(ord(c)) for c in set(op.text) if not c.isspace()):
                warnings.append("原字体缺少部分新字符；这些字符使用回退字体，请检查字形。")
            archive = fitz.Archive()
            archive.add((font.buffer, "original-font.ttf"))
            weight = "bold" if op.style.bold else "normal"
            slant = "italic" if op.style.italic else "normal"
            css = ("@font-face {font-family:pdf-original;src:url(original-font.ttf);"
                   f"font-weight:{weight};font-style:{slant};}}")
            return "pdf-original", css, archive
        except (RuntimeError, ValueError):
            warnings.append("此原字体无法复用，已使用替代字体；请检查字形和排版。")
            break
    else:
        warnings.append("此原字体无法复用，已使用替代字体；请检查字形和排版。")
    family = "monospace" if re.search(r"courier|mono", op.font or "", re.I) else (
        "serif" if re.search(r"times|ming|simsun|song|serif", op.font or "", re.I)
        and not re.search(r"sans", op.font or "", re.I) else "sans-serif")
    return family, "", None


def _layout_text(text, css, archive, width, height, size, scale):
    """Measure actual output glyphs, not the HTML line box's top/bottom leading.

    A padded temporary page avoids clipping accents/italic overhangs when copied.
    No temporary text is committed to the destination until it really fits.
    """
    margin = max(8, size * 2)
    # A 14,400 pt scratch page amplifies PDF matrix rounding into visible drift.
    # Only the requested height plus font leading is needed for a fit decision.
    layout_height = min(MAX_SIDE, height / scale + size * 4)
    doc = fitz.open()
    try:
        page = doc.new_page(width=width / scale + margin * 2, height=layout_height + margin * 2)
        spare, _ = page.insert_htmlbox(
            fitz.Rect(margin, margin, margin + width / scale, layout_height + margin),
            text, css=css, archive=archive, scale_low=1,
        )
        if spare < 0:
            doc.close()
            return None
        bounds = fitz.EMPTY_RECT()
        for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    bounds |= fitz.Rect(span["bbox"])
        if bounds.is_empty or bounds.height * scale > height + .05:
            doc.close()
            return None
        return doc, bounds, margin, scale
    except Exception:
        doc.close()
        raise


def _insert_text(page: fitz.Page, op, warnings: list[str]) -> None:
    r = bounded_rect(op.rect, page)
    family, font_css, archive = _text_font(page, op, warnings) if op.text.strip() else ("sans-serif", "", None)
    if op.kind == "replace":
        areas = [bounded_rect(rect, page) for rect in op.erase]
        _redact(page, areas, text_only=True)
    if op.style.background:
        page.draw_rect(r, color=None, fill=rgb(op.style.background), overlay=True)
    if not op.text.strip():
        return
    style = op.style
    # User text is never interpreted as HTML, CSS, URLs or local file paths.
    safe_text = html.escape(op.text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4))
    css = (
        font_css + "* {margin:0;padding:0;} "
        # insert_htmlbox prepends body{margin:1px}; a universal '*' rule loses
        # on specificity. Explicitly override it and preserve user whitespace.
        f"body {{margin:0;padding:0;font-family:{family};font-size:{style.size}pt;"
        f"color:{style.color};line-height:{style.line_height};"
        f"font-weight:{'bold' if style.bold else 'normal'};"
        f"font-style:{'italic' if style.italic else 'normal'};"
        f"text-align:{style.align};}} div {{white-space:pre-wrap;}}"
    )
    markup = f"<div>{safe_text}</div>"
    # Try the exact destination first: adding tolerance unconditionally shifts
    # centered/right-aligned text and lets successive edits creep across a page.
    layout_r = r
    result = _layout_text(markup, css, archive, r.width, r.height, style.size, 1)
    if result is None and op.kind == "replace":
        layout_r = _replacement_layout_rect(r, page, style.size)
        result = _layout_text(markup, css, archive, layout_r.width, layout_r.height, style.size, 1)
    if result is None and style.fit:
        low, high = .65, 1.0
        result = _layout_text(markup, css, archive, layout_r.width, layout_r.height, style.size, low)
        if result is not None:
            for _ in range(9):
                scale = (low + high) / 2
                trial = _layout_text(markup, css, archive, layout_r.width, layout_r.height, style.size, scale)
                if trial is None:
                    high = scale
                else:
                    result[0].close()
                    result, low = trial, scale
    if result is None:
        raise EditError("文字放不下（确实超出当前文本框）：请扩大文本框、缩小字号，或开启自动缩小。此次修改未提交。")
    rendered, bounds, margin, scale = result
    try:
        clip = fitz.Rect(0, 0, rendered[0].rect.width, min(rendered[0].rect.height, bounds.y1 + margin))
        x, y = r.x0 - margin * scale, r.y0 - bounds.y0 * scale
        destination = fitz.Rect(x, y, x + clip.width * scale, y + clip.height * scale)
        page.show_pdf_page(destination, rendered, 0, clip=clip, keep_proportion=False)
    finally:
        rendered.close()
    if scale < 0.995:
        warnings.append(f"部分文字为适应文本框已缩小至 {scale:.0%}（约 {style.size * scale:.1f} pt）。")


def build_page(spec_data: dict, source_data: bytes | None, images: dict[str, bytes] | None = None) -> tuple[fitz.Document, list[str]]:
    """Caller owns the resulting Document and must close it."""
    fitz.TOOLS.set_small_glyph_heights(True)
    spec = PageSpec.model_validate(spec_data)
    doc = fitz.open()
    warnings: list[str] = []
    try:
        if spec.source is None:
            doc.new_page(width=spec.width, height=spec.height)
        else:
            if source_data is None:
                raise EditError("找不到源 PDF；会话可能已过期，请重新导入。")
            with fitz.open(stream=source_data, filetype="pdf") as source:
                if spec.index >= len(source):
                    raise EditError("源页码超出范围。")
                doc.insert_pdf(source, from_page=spec.index, to_page=spec.index,
                               links=False, annots=False, widgets=False)
        page = doc[0]
        normalize_rotation(page)
        for op in spec.ops:
            if op.kind == "image_replace":
                raw = (images or {}).get(op.asset)
                if raw is None:
                    raise EditError("替换图片不存在或会话已过期，请重新上传图片。")
                image_tools.replace(page, op, raw)
            elif op.kind in ("text", "replace"):
                _insert_text(page, op, warnings)
            elif op.kind == "cover":
                page.draw_rect(bounded_rect(op.rect, page), color=None, fill=rgb(op.color), overlay=True)
            elif op.kind == "redact":
                _redact(page, [bounded_rect(op.rect, page)], color=op.color)
            elif op.kind == "crop":
                r = bounded_rect(op.rect, page)
                if min(r.width, r.height) < 8:
                    raise EditError("裁剪后每条边至少需要 8 pt。")
                # set_cropbox uses MediaBox-relative, UNROTATED coordinates;
                # all other edits use the visible CropBox-relative coordinates.
                off = page.cropbox_position
                page.set_cropbox(r + (off.x, off.y, off.x, off.y))
            elif op.kind == "rotate":
                page.set_rotation(op.angle)
                normalize_rotation(page)
            page = doc.reload_page(page)
        check_page_size(page)
        return doc, list(dict.fromkeys(warnings))
    except Exception:
        doc.close()
        raise


def text_regions(page: fitz.Page) -> dict:
    """Block and line hit areas; rotated/vertical text is viewable, not auto-editable."""
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    data = page.get_text("dict", flags=flags)
    blocks, lines = [], []
    skipped = 0
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        group = []

        def append_group():
            if not group:
                return
            bounds = fitz.EMPTY_RECT()
            for item in group:
                bounds |= fitz.Rect(item[0]["bbox"])
            br = _region(bounds, [s for _, spans in group for s in spans],
                         "\n".join("".join(s["text"] for s in spans) for _, spans in group),
                         page, [spans[0]["origin"][1] for _, spans in group])
            if br:
                blocks.append(br)
            group.clear()

        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            if abs(direction[0] - 1) > 0.015 or abs(direction[1]) > 0.015 or line.get("wmode", 0):
                append_group()
                skipped += 1
                continue
            ls = [s for s in line.get("spans", []) if s.get("text")]
            if not ls:
                continue
            txt = "".join(s["text"] for s in ls)
            lr = _region(line["bbox"], ls, txt, page)
            if lr:
                lines.append(lr)
            if group:
                previous, ps = group[-1]
                a, b = fitz.Rect(previous["bbox"]), fitz.Rect(line["bbox"])
                size = max(s["size"] for s in ps + ls)
                gap = ls[0]["origin"][1] - ps[0]["origin"][1]
                overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
                # MuPDF may put distant columns, labels and page numbers in one
                # block. Only vertically successive, aligned lines reflow together.
                aligned = min(abs(a.x0 - b.x0), abs(a.x1 - b.x1),
                              abs((a.x0 + a.x1 - b.x0 - b.x1) / 2)) < size * 2
                if not (.45 * size < gap <= 3 * size and overlap > 0 and aligned):
                    append_group()
            group.append((line, ls))
        append_group()
    return {"blocks": blocks, "lines": lines, "skipped": skipped,
            "has_text": bool(blocks or lines or skipped)}


def _region(bounds, spans, text, page, baselines=()):
    rect = fitz.Rect(bounds) & page.rect
    if rect.is_empty:
        return None
    first = max(spans, key=lambda s: len(s["text"]))
    weights = Counter()
    for s in spans:
        weights[round(s["size"], 1)] += len(s["text"])
    size = weights.most_common(1)[0][0]
    steps = [b - a for a, b in zip(baselines, baselines[1:]) if b > a]
    line_height = min(3, max(.85, median(steps) / size)) if steps else 1.25
    # Shrink glyph rectangles only by a numerical epsilon, avoiding touching
    # adjacent lines due to shared boundary rounding. No opaque white cover.
    erase = []
    for s in spans:
        sr = fitz.Rect(s["bbox"]) & page.rect
        if sr.width > 0.05 and sr.height > 0.05:
            sr = sr + (0.01, 0.01, -0.01, -0.01)
            erase.append(list(sr))
    return {"rect": list(rect), "erase": erase, "text": text,
            "size": size, "line_height": round(line_height, 4),
            "color": f"#{first['color'] & 0xffffff:06x}",
            "font": first.get("font", ""), "bold": bool(first.get("flags", 0) & 16),
            "italic": bool(first.get("flags", 0) & 2),
            "mixed": len({(s['font'], round(s['size'], 1), s['color']) for s in spans}) > 1}


def preview_page(spec_data: dict, source_data: bytes | None, scale: float, include_text=True, images=None) -> dict:
    doc, warnings = build_page(spec_data, source_data, images)
    try:
        page = doc[0]
        area = page.rect.width * page.rect.height
        scale = min(scale, math.sqrt(MAX_RENDER_PIXELS / area), 3200 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        result = {"width": page.rect.width, "height": page.rect.height,
                  "image": base64.b64encode(pix.tobytes("png")).decode("ascii"),
                  "warnings": warnings}
        if include_text:
            result.update(text_regions(page))
            result['images'] = image_tools.regions(page)
        return result
    finally:
        doc.close()


def export_pdf(page_specs: list[dict], sources: dict[str, bytes], mode: str, dpi: int, images=None) -> bytes:
    out = fitz.open()
    total_pixels = 0
    try:
        for number, spec in enumerate(page_specs, 1):
            try:
                one, _ = build_page(spec, sources.get(spec.get("source")), images)
                try:
                    if mode == "raster":
                        p = one[0]
                        scale = dpi / 72
                        area = math.ceil(p.rect.width * scale) * math.ceil(p.rect.height * scale)
                        if area > MAX_RENDER_PIXELS:
                            raise EditError("该页图像化像素超过 1200 万；请降低导出 DPI。")
                        total_pixels += area
                        if total_pixels > MAX_EXPORT_PIXELS:
                            raise EditError("图像化导出总像素超过 1.8 亿；请降低 DPI 或分批导出。")
                        pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        target = out.new_page(width=p.rect.width, height=p.rect.height)
                        # Brand-new, image-only page contains no original objects,
                        # hidden CropBox content, metadata, attachments or text layer.
                        target.insert_image(target.rect, stream=pix.tobytes("png"))
                    else:
                        out.insert_pdf(one, links=False, annots=False, widgets=False)
                finally:
                    one.close()
            except EditError as exc:
                raise EditError(f"第 {number} 页：{exc}") from exc
        out.set_metadata({})
        out.del_xml_metadata()
        result = out.tobytes(garbage=4, clean=True, deflate=True, deflate_images=True, deflate_fonts=True)
        if len(result) > MAX_OUTPUT_BYTES:
            raise EditError("输出文件超过 200 MB，请分批导出。")
        return result
    finally:
        out.close()
