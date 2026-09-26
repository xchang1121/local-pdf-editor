# PDF Studio 项目文件

v1.2 编辑器使用 `.pdfstudio` 作为可继续编辑的项目文件扩展名。当前格式版本为 `1`，文件本身是 ZIP。它独立于浏览器缓存和服务会话；重新打开时素材 ID 会重新分配。

## 保存和恢复

“项目 → 保存项目”打包当前文档及所有仍保留的历史步骤。保存不覆盖原 PDF，不包括尚未应用的草稿，也不会自动写入一个固定路径；需要保留浏览器下载的文件。再次编辑后应再次保存。

“项目 → 打开项目文件”先校验并重放整个项目，成功后替换当前文档和会话素材。失败时当前文档、历史和未应用草稿保持原状。打开另一个项目会替换当前项目，请先保存需要保留的编辑。

项目包含源文件与历史中的原内容，包括已在当前页面移除的文字、图片。它用于编辑归档，不适合代替脱敏后的成品 PDF。项目不保存会话令牌、服务器地址或本地文件路径。

## 容器结构

```text
manifest.json
sources/0.pdf
sources/1.pdf
images/0.png
...
```

- `manifest.json`：UTF-8 JSON，包含 `format: "pdf-studio-project"`、`version: 1`、`engine_version`、`project` 和 `assets`。
- `project.document`：`pages`、当前页 `active`、文档名 `name` 与是否有未导出修改 `dirty`。
- `project.history.pages`：去重的完整页面状态；页面结构与 `app/schemas.py` 的 `PageSpec` 一致，包含源文件 ID、源页序号及顺序操作列表。
- `project.history.entries`：按时间排序的历史项，含 `label`、`time` 和 `state`。`state.pages` 引用去重页面数组的整数索引，另含 `active` 和 `name`。
- `project.history.cursor`：当前步骤的零基索引。其后步骤是可重做的分支，也必须包含在归档中。
- `assets.sources` / `assets.images`：以原会话素材 ID 为键，值为归档内相对路径 `path` 和字节内容的 `sha256`。源 PDF 已由导入流程规范化，替换图片使用规范化 PNG。

保存当前状态所需素材及保留历史引用的素材，不加入完全无引用的上传文件。当前文档页面必须与历史当前位置一致；界面导航可能使当前选中页不同于该步骤刚提交时的选中页。

归档最多 220 MiB，素材解压后最多 200 MiB，清单最多 8 MiB，最多 5000 个素材文件和 41 个状态（40 次变化加起始状态）。不支持加密 ZIP，也不支持 ZIP_STORED / ZIP_DEFLATED 以外的压缩方式。

## 打开时的检查

1. 检查归档体积、成员数量、压缩方式与声明的解压体积。
2. 校验清单版本、数据结构、页面 ID、历史索引及当前状态一致性。
3. 检查条目与清单一一对应，拒绝重复、缺失、未声明的条目及不合规则的路径。
4. 校验素材哈希，检查 PDF 页数/尺寸、图片格式/像素/大小。
5. 给源文件和图片分配新 ID，重映射当前文档及历史中的引用。
6. 重放所有独立页面状态，验证当前步骤和未来重做步骤均可重建。
7. 校验服务总容量，然后一次替换当前会话的素材并返回文档和历史。

归档在内存中读取，不把条目路径解压到磁盘。引擎版本不一致时会提示重新检查页面效果。当前不保证不同引擎版本得到完全相同的字形和排版，也不接受未知的未来格式版本。

## HTTP 接口

所有请求使用当前会话的 `X-Session-Token`。

`POST /api/projects/save` 接收上述 `project` 对象本身，即 `{ "document": ..., "history": ... }`，返回 `application/zip`，下载文件名后缀 `.pdfstudio`。服务端只允许使用当前会话拥有的素材。

`POST /api/projects/open` 接收 multipart `file`，成功返回 `{ "project": ..., "warnings": [...] }`；项目中的源文件和图片 ID 已替换为新会话素材 ID。现有会话令牌继续使用，归档中的旧会话信息不参与授权。

查找与替换接口配合项目中的页面结构：

```json
{
  "pages": [{"id": "p1", "source": "当前会话的源文件ID", "index": 0, "ops": []}],
  "query": "2025",
  "case_sensitive": false,
  "page_ids": null
}
```

发送到 `POST /api/search` 可获得匹配项 `id`、`page_id`、实际 `page_number`、`rect`、匹配文字和前后上下文。`page_ids: null` 搜索全文；传入页面 ID 列表可限制范围。

在同一请求体中加入 `replacement: "2026"`、`fit: true`，发到 `POST /api/search/replace`。`ids: null` 替换全部匹配，`ids: ["查找结果ID"]` 只替换指定项。结果 ID 与页面内容和位置关联，修改页面后需重新查找。返回 `pages`、`count`、`changed_pages`、`warnings`；客户端仅在成功后把返回页面列表作为一次历史提交。零匹配或内容相同不会新增修改。

新增批注使用 `annotation` 操作和稳定 `id`，`type` 为 `highlight` / `note`；更新使用 `annotation_update`，删除使用 `annotation_delete`。原生批注的标准导出与图像化导出的区别详见 README。
