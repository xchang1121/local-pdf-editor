"""Native PDF highlights and notes added by this editor, with stable recipe IDs."""
import pymupdf as fitz

from .errors import EditError

PREFIX = 'PDFStudio:'


def _find(page, identity):
    return next((a for a in page.annots() or [] if a.info.get('subject') == PREFIX + identity), None)


def _color(value):
    return tuple(int(value[i:i+2], 16) / 255 for i in (1, 3, 5))


def _line_quad(group, direction):
    # Bound every glyph in the line's own axes. Using only the first and last
    # character clips a larger font in the middle of a mixed-size line.
    dx, dy = direction
    points = [point for quad in group for point in quad]
    along = [p.x * dx + p.y * dy for p in points]
    across = [-p.x * dy + p.y * dx for p in points]
    left, right, top, bottom = min(along), max(along), min(across), max(across)
    def point(u, v):
        return fitz.Point(u * dx - v * dy, u * dy + v * dx)
    return fitz.Quad(point(left,top),point(right,top),point(left,bottom),point(right,bottom))


def apply(page, op):
    if op.kind != 'annotation':
        annot = _find(page, op.id)
        if annot is None:
            raise EditError('找不到这条批注，请重新选择。')
        if op.kind == 'annotation_delete':
            page.delete_annot(annot)
            return
        if annot.type[0] == fitz.PDF_ANNOT_TEXT and not op.comment.strip():
            raise EditError('请先填写便签内容。')
    else:
        if _find(page, op.id) is not None:
            raise EditError('批注标识重复，请重新添加。')
        rect = fitz.Rect(op.rect)
        if rect.width < .2 or rect.height < .2 or not (rect & page.rect).get_area() >= rect.get_area() - .1:
            raise EditError('批注范围超出页面或太小。')
        if op.type == 'note':
            if not op.comment.strip():
                raise EditError('请先填写便签内容。')
            point = fitz.Point(min(rect.x0, max(0, page.rect.width - 20)), min(rect.y0, max(0, page.rect.height - 20)))
            annot = page.add_text_annot(point, op.comment, icon='Note')
        else:
            quads = []
            if op.snap:
                # Keep each selected line separate; no yellow fill between lines.
                raw = page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
                for block in raw['blocks']:
                    for line in block.get('lines', []):
                        group = []
                        for span in line['spans']:
                            for char in span['chars']:
                                box = fitz.Rect(char['bbox'])
                                center = (box.tl + box.br) / 2
                                if center in rect and box.intersects(page.rect):
                                    group.append(fitz.recover_char_quad(line['dir'], span, char))
                        if group:
                            quads.append(_line_quad(group, line['dir']))
            annot = page.add_highlight_annot(quads or [rect.quad])
        annot.set_info(title='PDF Studio', subject=PREFIX + op.id)
    annot.set_info(content=op.comment)
    annot.set_colors(stroke=_color(op.color))
    annot.set_opacity(op.opacity)
    annot.set_flags(fitz.PDF_ANNOT_IS_PRINT)
    annot.update()


def regions(page):
    result = []
    for annot in page.annots() or []:
        subject = annot.info.get('subject', '')
        if not subject.startswith(PREFIX):
            continue
        rect = annot.rect & page.rect
        if rect.is_empty:
            continue
        color = annot.colors.get('stroke') or (1, .83, .31)
        result.append({'id':subject[len(PREFIX):], 'type':'highlight' if annot.type[0] == fitz.PDF_ANNOT_HIGHLIGHT else 'note',
                       'rect':list(rect), 'comment':annot.info.get('content', ''),
                       'color':'#' + ''.join(f'{round(c*255):02x}' for c in color[:3]),
                       'opacity':annot.opacity if annot.opacity >= 0 else 1})
    return result
