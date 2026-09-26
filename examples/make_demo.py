"""Create the bundled, fictional sample. No external font files are required."""
from pathlib import Path
import pymupdf as fitz


def box(p, rect, text, size=12, color='#45556c', bold=False, family='sans-serif'):
    css=f'*{{margin:0;padding:0}}body{{font-family:{family};font-size:{size}pt;color:{color};line-height:1.5;font-weight:{"bold" if bold else "normal"};}}'
    spare,_=p.insert_htmlbox(fitz.Rect(rect),text,css=css,scale_low=1)
    assert spare>=0,(text,rect)


def create(path):
    d=fitz.open()
    p=d.new_page(width=595,height=842)
    p.draw_rect((0,0,595,13),fill=(.22,.41,.92),color=None)
    box(p,(48,43,460,70),'FIELDNOTES &nbsp; / &nbsp; PROJECT BRIEF',10,'#6480ab',True)
    box(p,(48,95,550,159),'把想法，变成下一步。',28,'#253752',True)
    box(p,(48,159,547,200),'项目进展简报 · 可编辑示例文档',12,'#8a96a8')
    p.draw_line((48,218),(547,218),color=(.87,.90,.95),width=1)
    box(p,(48,245,220,270),'01 &nbsp; 项目概览',13,'#3868ec',True)
    box(p,(48,284,542,354),'这是一个用于测试 PDF 编辑功能的虚构示例。你可以修改标题、调整段落位置，也可以删除页面或裁掉空白区域。',12)
    box(p,(48,375,542,432),'选择上方“编辑文字”，点击这段内容。右侧支持字号、颜色、行距和对齐方式。修改完成后，点击“应用修改”。',12)
    p.draw_rect((48,465,547,580),fill=(.95,.97,1),color=None)
    box(p,(68,486,526,513),'下一步 / NEXT STEP',10,'#5878b0',True)
    box(p,(68,523,526,561),'让复杂的文档操作，回到直观的页面上。',14,'#3d536f')
    box(p,(48,625,540,675),'Draft version: 2025<br>Owner: Design Team',11,'#8491a6')
    box(p,(48,777,510,803),'EXAMPLE ONLY · NOT A REAL BUSINESS DOCUMENT',8,'#a4adbb')
    box(p,(525,774,550,806),'01',10,'#9aa9bf')
    p=d.new_page(width=595,height=842)
    box(p,(48,48,545,90),'02 / 页面整理与内容移除',21,'#253752',True)
    box(p,(48,127,540,197),'试着把这一页拖到最前面，或者复制一份。左下角箭头也可以移动当前页；导出时填写页码，就能单独提取页面。',12)
    p.draw_rect((48,246,547,418),fill=(.98,.97,.95),color=None)
    box(p,(70,268,522,305),'仅供测试的敏感字段',13,'#8a7355',True)
    box(p,(70,325,521,364),'SECRET-DEMO-8472',20,'#5a4c3d',True)
    box(p,(48,457,542,546),'用“遮盖”处理上面的字段：它虽然看不见，仍然可能被复制。用“真正删除”处理后，选区内的原文字才会从导出页面内容中移除。',12)
    box(p,(48,589,540,677),'普通裁剪只改变可见范围。导出为“图像化 PDF”时，会用当前可见的页面像素重新生成 PDF，不再保留原文字层。',12)
    box(p,(48,777,515,808),'请在导出后检查效果。本示例不包含真实个人信息。',9,'#97a3b6')
    box(p,(525,774,550,806),'02',10,'#9aa9bf')
    # Page 3 deliberately has an image instead of a selectable text layer.
    scan=fitz.open();q=scan.new_page(width=595,height=842)
    box(q,(48,56,543,111),'03 / 扫描件示例',23,'#253752',True)
    box(q,(48,162,542,267),'这一页是图像，没有可点选的原文字层。你仍然可以裁剪页面、添加文字或移除指定区域。本版编辑器不会自动进行 OCR。',15)
    for y in range(340,600,52):q.draw_line((48,y),(547,y),color=(.87,.9,.94),width=1)
    box(q,(48,663,540,711),'在横线上方，试着新增一个文本框。',14,'#7a8aa5')
    box(q,(48,777,510,808),'IMAGE-ONLY PAGE · SCANNED DOCUMENT EXAMPLE',9,'#97a3b6')
    pix=q.get_pixmap(matrix=fitz.Matrix(1.3,1.3),alpha=False)
    p=d.new_page(width=595,height=842);p.insert_image(p.rect,stream=pix.tobytes('png'))
    scan.close();d.set_metadata({'title':'PDF Studio — fictional example'})
    d.save(str(path),garbage=4,deflate=True);d.close()

if __name__=='__main__':create(Path(__file__).with_name('demo.pdf'))
