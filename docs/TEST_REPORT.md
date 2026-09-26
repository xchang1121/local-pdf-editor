# 实际测试报告

## v1.2：查找替换、批注与项目文件（2026-09-26）

本节记录当前版本。后文保留以前版本的测试证据，其中“未实现”与会话限制以对应版本为准。

### 结果

- Windows，Python 3.12.5、PyMuPDF 1.26.7、pypdf 6.10.0、Pillow 12.1.1；Playwright 1.57.0 / Chromium 143.0.7499.4。运行依赖未变更。
- **117 项引擎/API 测试通过**：`117 passed, 1 warning in 23.09s`，其中 27 项覆盖本次功能；本机无跳过。仍有一条来自 Starlette/AnyIO 的弃用提示。
- **62 项原生浏览器检查通过**：通用编辑 14、文字回归 9、草稿/弹窗 13、历史/图片 15、新功能整套流程 11。实际访问本地 HTTP，使用原生文件选择与下载；未用离线桥接替代本节检查。
- 两份 JavaScript 的语法检查及 Python 编译检查通过。

### 新功能覆盖

| 范围 | 验证内容 |
| --- | --- |
| 查找 | 多页结果、上下文和坐标、点击定位、当前页/全文、大小写、字面 `A+B`、裁剪后的可见范围、无文字层页、旋转文字跳过、页面修改后结果失效。 |
| 替换 | 单处/整批、空替换删除、中文词与夹在中文中的数字、紧邻重复关键词、保留左右字符、整批一条历史、撤销/重做、过期结果拒绝、超长文本整批不提交且保留输入。 |
| 原生批注 | 自动按行贴合高亮、任意区域高亮、中文便签、内容/颜色/透明度更新、删除及恢复、裁剪与 90/180/270° 旋转后操作；混合字号以及旋转后四边形覆盖完整文字。 |
| 导出 | 下载的标准 PDF 包含修改后的可选文字、原生高亮与中文批注正文；图像化 PDF 不携带文字层和批注对象；加批注后继续替换正文仍保留批注。 |
| 项目保存 | 源 PDF、当前状态、批注、保留历史、待重做图片及其素材；未应用草稿阻止保存，未提交内容不被误记为已保存。空当前文档仍可保存可重做历史。 |
| 项目恢复 | API 测试完整结束原服务生命周期与工作进程，再启动新生命周期；旧令牌失效，新会话恢复独立项目。真实浏览器另开无缓存上下文，恢复后仍可重做图片替换、编辑批注和导出。 |
| 无效项目 | 未知格式版本、非法路径、素材校验失败、素材缺失、重复条目、历史与当前状态不符、损坏输入和文件大小上限。校验失败保留当前会话、文档及草稿。 |
| 界面 | 项目保存状态在刷新后保留；Ctrl / ⌘ F、Ctrl / ⌘ S；1024 和 390 px 下功能控件可用，无整体横向溢出。测试拒绝未预期的确认弹窗，无未捕获 JavaScript 异常。 |

中文边界测试发现并修复了一个额外问题：未嵌入的 `Heiti` 字体不能以同名 Base-14 字体加载，底层抛出的异常类型不在原回退分支内。现正确转入字体回退并提示用户，中文词语和相邻数字替换均可完成。

独立 Poppler 对真实下载的审阅 PDF 进行渲染并人工检查：黄色高亮覆盖目标文字，替换数字与左右正文未丢失，蓝色便签图标在所选位置。中文备注正文通过 PDF 批注结构检查。Poppler 仍提示本机缺少 Symbol / ArialUnicode 显示字体，该样页目标文字显示正常。桌面/窄屏截图来自实际界面。

界面证据：[查找与批注](screenshot-search.png)、[批注列表](screenshot-review.png)、[390 px 查找面板](screenshot-search-mobile.png)。

### 复现

启动新版 `python run.py --port 8004`，运行：

```text
python -m pytest -q
node --check app/static/app.js
node --check app/static/features.js
python tests/browser_smoke.py --url http://127.0.0.1:8004
python tests/browser_text_regressions.py --url http://127.0.0.1:8004
python tests/browser_draft_regressions.py --url http://127.0.0.1:8004
python tests/browser_history_images.py --url http://127.0.0.1:8004
python tests/browser_search_annotations_projects.py --url http://127.0.0.1:8004
```

测试只使用可控生成的样本与项目示例，尚未取得用户的实际业务 PDF。未覆盖任意复杂字距、所有原字体、所有 PDF 阅读器的批注表现、Safari/Firefox、真机下载或极端容量/高并发。查找不支持跨行、竖排/旋转文字或 OCR；项目需手动下载保存，不是磁盘自动保存或云端同步。原文件已有批注仍在导入时静态化，只有本工具新增的批注及项目操作可继续编辑。

---

## v1.1：编辑历史与图片替换（2026-09-26）

本节为 v1.1 的历史验证结果，后文保留更早的修复与初版验证记录。

### 当前结果

- Windows，Python 3.12.5、PyMuPDF 1.26.7、pypdf 6.10.0、Pillow 12.1.1；Playwright 1.57.0 / Chromium 143.0.7499.4。
- **90 项引擎/API 测试通过**（`python -m pytest -q`，90 passed, 1 warning in 11.26s）。包括原有 71 项与新增 19 项图片验证。本机无跳过；依赖本机 Windows 字体的 5 项测试在找不到相应字体的其他系统上会跳过。Starlette/AnyIO 有一条弃用提示。
- **51 项原生浏览器检查通过**：通用编辑 14 项、文字回归 9 项、草稿/弹窗 13 项、历史/图片 15 项。通过真实本地 HTTP、原生文件选择和下载验证；没有用离线注入代替本节结果。
- JavaScript 语法检查及 Python 编译检查通过。

### 历史与图片覆盖

历史列表包括导入、改字、图片替换和页面操作；验证了点击步骤、撤销/重做、回退后的分支替换、同标签页刷新、删光页面后的撤销、回到导入前空状态后的重做，以及 40 步保留上限。图片上传未应用或输入失败时不新增历史；真实草稿的取消丢弃行为保留输入。测试明确记录弹窗次数，拒绝未预期的弹窗。

图片验证包括同一图片资源在不同位置使用、共享的嵌套 Form、连续替换、源图不再以无用资源残留、原图上方文字的顺序保留、完整显示/填满/拉伸、透明度与手机 EXIF 方向、页面裁剪后旋转、图片自身 0/90/180/270° 方向与原有裁切、内嵌图片、继承资源字典和资源名称冲突。无效图片、缺失/过期的选择与跨会话图片引用均不能提交。

额外的独立 Poppler 渲染检查了真实浏览器下载的 PDF：两张替换图片显示正常，前景文字仍在正确位置，改过的正文可提取，原正文已移除。Poppler 报告其环境缺少 Symbol / ArialUnicode 显示字体；该样页使用的目标文字显示正常。

桌面与 1024 / 390 px 视口已检查，历史入口可操作，页面无整体横向溢出。截图为运行中应用的实际界面。

### 复现

先运行 `python run.py --port 8002`，再运行：

```text
python -m pytest -q
node --check app/static/app.js
python tests/browser_smoke.py --url http://127.0.0.1:8002
python tests/browser_text_regressions.py --url http://127.0.0.1:8002
python tests/browser_draft_regressions.py --url http://127.0.0.1:8002
python tests/browser_history_images.py --url http://127.0.0.1:8002
```

### 边界

测试使用项目示例与可控生成的 PDF，尚未取得用户实际业务 PDF。并未覆盖任意复杂图片结构、所有颜色配置与透明度混合、所有字体和文字变换、Safari/Firefox 或真机。图片结构无法一一对应时会明确提示不支持。历史暂存依赖同一标签页和仍有效的服务会话；关闭服务后不可当作永久项目恢复。没有新增 OCR、图片自由拖动、交互表单或签名维护。

---

## 后续修复：选择文字反复弹出“丢弃修改”（2026-09-26）

用户反馈切换位置反复出现未应用修改确认框。已用真实浏览器复现：只点选第一段文字，再点另一段，未编辑任何内容也弹出确认。

原因是前端把“存在选区”直接当成“存在修改”。现保存选中时的表单基线，仅在内容、样式或位置实际改变时确认；改动后又还原不会误报。比较使用已显示的坐标，避免 PDF 原坐标与两位小数显示值的差异触发误判。空的新文本框可直接离开；已输入的新文字与已画出的裁剪等操作仍受保护。已选中的工具可重复点击，未修改的文字选区不再阻止导出和页面拖拽。

新增 `tests/browser_draft_regressions.py`，13 项真实浏览器检查通过，明确断言弹窗次数，并验证取消保留草稿、确认切换、文本/样式/坐标还原、无效输入、空白处取消选择、导出及页面拖拽。原来的 14 项通用浏览器检查和 9 项文字浏览器检查均再次通过。JavaScript 语法检查通过。

此前套件自动接受弹窗，未断言“未经修改的选择应无弹窗”，因此未能检出本问题。新套件默认取消并记录每个弹窗，补上该遗漏。

仅修改前端及相关测试；无需重启服务，在当前标签页刷新即可加载修复。若有尚未应用的输入，应先应用再刷新。修改前的前端备份位于 `work/app-before-draft-fix.js`。

## 最新 Windows 调试：文字编辑修复（2026-09-26）

此节描述本地项目的最新验证。后文保留此前 Linux / 离线浏览器验证记录，不能将旧记录当成本次原生浏览器验证。

### 已复现并修复

- 原默认边距和行框定位造成连续改字偏移：示例同一行连续应用 5 次，即向右约 5 pt、向下约 12.84 pt。现在显式清除默认边距，先测量实际字形，再锚定原位置；新增 10 次连续编辑回归，位置误差要求小于 0.05 pt（浏览器坐标取两位小数，要求小于 0.08 pt）。
- 页脚与远处页码被合成一个段落，不改内容也可能在关闭自动缩小时失败。现在按真实行距、水平位置和行间关系拆分；示例前两页的全部文本块与单行都经过原字号重排检查。
- 紧密多行段落被固定的默认行距撑高。现在读取原基线间距，并以测得的字形高度判断能否放下。
- 原字体统一替换带来字形和宽度变化。新增原字体优先模式，复用当前文档中可用的字体；识别 ArialMT / Arial Regular 等名称差异。缺失字符、不可复用字体和样式改变均有回退提示。
- 连续空格、缩进、空行被 HTML 合并。现在保留这些文本布局信息，并统一处理 Windows 换行符。
- 自动容差无条件改变居中/右对齐位置、可能占用邻近文字区域。现在先尝试准确原框，再在相邻文字与页面边缘内尝试有限容差。
- 原生浏览器测试中的等待代码与站点 CSP 不兼容。已修正测试等待方式，保留应用原有 CSP，没有开启 unsafe-eval。

### 环境与执行方式

Windows，Python 3.12.5，PyMuPDF 1.26.7，FastAPI 0.128.2，Uvicorn 0.48.0，pytest 9.0.2，Playwright 1.57.0，Chromium 143.0.7499.4。运行依赖版本未变更；补装了项目已列出的开发测试依赖。

```text
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tests\browser_smoke.py --url http://127.0.0.1:8000
.venv\Scripts\python.exe tests\browser_text_regressions.py --url http://127.0.0.1:8000
node --check app/static/app.js
.venv\Scripts\python.exe -m compileall -q app tests run.py
```

最新完整运行结果：**71 passed, 1 warning in 14.98s**。引擎/API 测试包含原有 37 项与新增 34 项，本机无跳过；5 项使用本机 Windows 字体的测试在其他系统找不到字体时会跳过。测试期间有一条来自 Starlette/AnyIO 的弃用提示，不影响测试结果。JavaScript 语法检查和 Python 编译检查均通过。

原浏览器套件 14 项与新增文字套件 9 项使用真实本地 HTTP、浏览器文件上传、原生下载和真实 sessionStorage。新增套件覆盖连续 10 次原字体改字、溢出不提交、中文回退提示、段落原行距、导出新旧文字检查、邻近文字保留、刷新恢复和导出文件重新导入。

### 字体与视觉检查

使用本机 Arial、Arial Narrow、宋体（SimSun）、微软雅黑（Microsoft YaHei）、黑体（SimHei）生成可控 PDF，将 2025 替换为 2026。五种字体均无需缩小且没有回退提示；本次测得的字形框最大坐标变化小于 0.001 pt。另测试了内嵌中文字体及字体子集缺字时的回退。

使用独立 Poppler 渲染浏览器导出的文字修改页、裁剪后的修改页、字体验证页，目视检查中文/拉丁文字、段落间距、页码和邻近行。对应样页未见裁字、重叠或缺字方块。Poppler 对其环境中的 Symbol / ArialUnicode 显示字体报告提示；本次样页使用的目标字体已嵌入且显示正常。

这批验证使用随附示例与可控生成的字体样本；用户实际业务 PDF 尚未提供。未验证任意嵌入字体、任意字距变换、复杂公式、竖排、OCR 扫描文字修补、所有浏览器和极端负载。实现依据与接口说明可见 [PyMuPDF Page 文档](https://pymupdf.readthedocs.io/en/latest/page.html)，具体行为以本项目固定版本和测试结果为准。

---

## 历史验证记录（此前版本）

验证日期：2026-09-26。此报告只描述本次执行结果，不是对所有 PDF、浏览器或部署环境的兼容性保证。

## 环境

Linux / Debian 系环境，Python 3.13.5；PyMuPDF 1.26.7；FastAPI 0.128.2；Uvicorn 0.48.0；python-multipart 0.0.29；pytest 9.0.2；httpx 0.28.1；Playwright 1.57.0；Chromium 144.0.7559.96；Poppler pdftoppm 25.06.0。

## PDF 引擎与 API：34 项通过

执行：

```text
python -m pytest -q --tb=short
.................................. [100%]
34 passed in 3.51s
```

覆盖范围包括 PDF 导入/渲染/文字区域提取、中文与拉丁文字替换、新旧文字位置分离、原字移除与其他内容保留、文本框溢出拒绝、空文字删除、HTML 转义、遮盖和真正移除的差异、图像像素移除、扫描页、裁剪/恢复 CropBox 与图像化导出的差别、旋转、偏移 MediaBox、裁剪后旋转、重复裁剪、页面合并/选取/重排、批注与表单外观静态化、附件移除、加密/损坏输入、参数校验、会话隔离/清空/过期、跨源/Host 检查、请求大小限制及中文导出文件名。

发现并修复的一项实际问题：固定版本 PDF 引擎在对已经裁剪的页面做 90° 旋转规范化时，原 CropBox 可能被重置。当前代码保存/变换/恢复裁剪范围，并加入裁剪页面和偏移 MediaBox 的回归测试。不能据此推断所有异常 PDF 几何结构均已覆盖。

## 浏览器交互：14 项检查通过（离线 DOM + 真实本地 HTTP 桥接）

执行：

```text
python tests/browser_smoke.py --offline-bridge
Browser checks: 14 passed
```

本环境的 Chromium 存在组织管理的 URL 禁止策略，普通访问 `http://127.0.0.1:8000` 返回 `ERR_BLOCKED_BY_ADMINISTRATOR`。**没有修改该策略，也没有将普通网页导航测试记为通过。**

测试在浏览器空白页中注入实际 HTML/CSS/JavaScript，用真实鼠标和输入操作界面；测试用 Python 桥接将请求发送到实际运行的 FastAPI 服务。下载改为读取网页生成的 Blob 字节。`sessionStorage` 在空白来源中由测试替身模拟。该模式验证的是界面和服务的联调，不覆盖原生导航、原生下载、真实存储权限及网络策略。

| 检查项 | 结果 |
| --- | --- |
| 首页加载、本地会话初始化 | 通过 |
| 导入三页 PDF、缩略图渲染 | 通过 |
| 点击原文字、中英混排替换、字号与框尺寸修改 | 通过 |
| 真实文字修改的撤销与重做 | 通过 |
| 鼠标新增文本框、输入中文、重新渲染 | 通过 |
| 鼠标裁剪、裁剪后的旋转与撤销 | 通过 |
| 页面复制和删除 | 通过 |
| 两个可见缩略图的拖拽重排、无文字层页面识别 | 通过 |
| 导出 PDF 字节内的文字与页序验证 | 通过 |
| 鼠标选区移除演示字段 | 通过 |
| 提取一页并图像化导出，确认无文字层 | 通过 |
| 同标签页刷新流程恢复操作记录（存储为测试替身） | 通过 |
| 390px Chromium 视口无文档级横向溢出 | 通过 |
| 该测试过程无未捕获的 JavaScript 异常 | 通过 |

截图 `screenshot-welcome.png`、`screenshot-editor.png`、`screenshot-mobile.png` 由这一真实 DOM 测试生成。它们不是另行绘制的界面模型，但也不构成 Safari/真机兼容性验证。

原生浏览器测试入口已提供：

```bash
python -m playwright install chromium
python tests/browser_smoke.py --url http://127.0.0.1:8000
```

请在允许访问本地服务的环境另行执行，确认原生导航与下载。

## 导出视觉检查

除引擎自身渲染之外，使用独立的 Poppler `pdftoppm -cropbox` 渲染了界面测试导出的文字 PDF 与图像化 PDF，并查看页面图像。检查的样页中中文/数字显示正常，标题替换与新增文字可见，页面可见范围符合该次裁剪操作。未对任意字体、任意复杂图形、透明度或所有 PDF 规范变体做全面视觉回归。

## 其他检查与未验证项

已执行 JavaScript 语法检查 `node --check`、Python 编译检查，以及 `bash -n start.sh`。启动器的参数帮助已运行。

**没有实测**：Windows/macOS 启动脚本的完整安装启动过程、全新机器的 pip 下载、Docker 镜像构建、Safari、Firefox、iOS/Android 真机、原生下载对话框、公网多用户部署、恶意 PDF 抗攻击、极限负载和长期运行稳定性。

当前测试数据主要是可控生成的 PDF 和随附演示文档；没有取得你的实际文档，因此不能承诺其原字体和特殊版式必然兼容。首次处理重要文件，请保留原件并检查导出结果。


## 2026-09-26 text replacement regression fix

- Fixed false overflow failures caused by using extracted glyph bounding boxes as hard HTML layout boxes.
- Replacement layout now receives a small, capped right/bottom tolerance while the original erasure rectangles remain unchanged.
- Added regression coverage for tight same-size replacement with auto-shrink disabled, Chinese replacement, and genuine long-text overflow.
- Backend/API suite: 37 passed. Browser smoke suite: 14 passed (offline DOM + local HTTP bridge).
