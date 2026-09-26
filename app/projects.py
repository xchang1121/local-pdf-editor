"""Portable project archives. No filesystem extraction and no session credentials."""
from hashlib import sha256
from io import BytesIO
import json
import secrets
from typing import Literal
from zipfile import ZipFile, BadZipFile, ZIP_DEFLATED, ZIP_STORED

from pydantic import Field, model_validator, ValidationError
import pymupdf as fitz

from . import engine, images as image_tools
from .errors import EditError
from .schemas import PageSpec, StrictModel

MAX_ASSET_BYTES = 200 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PROJECT_BYTES = 220 * 1024 * 1024


class ProjectState(StrictModel):
    pages: list[PageSpec] = Field(max_length=300)
    active: str | None = Field(max_length=100)
    name: str = Field(min_length=1, max_length=250)
    dirty: bool = True


class HistoryState(StrictModel):
    pages: list[int] = Field(max_length=300)
    active: str | None = Field(max_length=100)
    name: str = Field(min_length=1, max_length=250)


class HistoryEntry(StrictModel):
    label: str = Field(min_length=1, max_length=250)
    time: str = Field(max_length=60)
    state: HistoryState


class History(StrictModel):
    pages: list[PageSpec] = Field(max_length=12300)
    entries: list[HistoryEntry] = Field(min_length=1, max_length=41)
    cursor: int = Field(ge=0)


class ProjectPayload(StrictModel):
    document: ProjectState
    history: History

    @model_validator(mode='after')
    def valid_history(self):
        h = self.history
        if h.cursor >= len(h.entries):
            raise ValueError('历史步骤超出范围。')
        states = [(self.document.pages, self.document.active)]
        for entry in h.entries:
            if any(index < 0 or index >= len(h.pages) for index in entry.state.pages):
                raise ValueError('历史记录引用了不存在的页面。')
            states.append(([h.pages[i] for i in entry.state.pages], entry.state.active))
        for pages, active in states:
            ids = [p.id for p in pages]
            if len(set(ids)) != len(ids) or (active not in ids if ids else active is not None):
                raise ValueError('项目页标识或当前页面无效。')
        if [p.model_dump() for p in self.document.pages] != [h.pages[i].model_dump() for i in h.entries[h.cursor].state.pages]:
            raise ValueError('当前文档与历史步骤不一致。')
        return self


class Asset(StrictModel):
    path: str = Field(pattern=r'^(sources|images)/[0-9]+\.(pdf|png)$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Assets(StrictModel):
    sources: dict[str, Asset] = Field(max_length=5000)
    images: dict[str, Asset] = Field(max_length=5000)


class Manifest(StrictModel):
    format: Literal['pdf-studio-project']
    version: Literal[1]
    engine_version: str = Field(max_length=40)
    project: ProjectPayload
    assets: Assets


def all_pages(payload):
    return [*payload['document']['pages'], *payload['history']['pages']]


def references(payload):
    sources, images = set(), set()
    for page in all_pages(payload):
        if page.get('source'):
            sources.add(page['source'])
        for op in page['ops']:
            if op['kind'] == 'image_replace':
                images.add(op['asset'])
    return sources, images


def save(payload, sources, images):
    payload = ProjectPayload.model_validate(payload).model_dump()
    needed_sources, needed_images = references(payload)
    if not needed_sources <= sources.keys() or not needed_images <= images.keys():
        raise EditError('部分源文件或图片不在当前会话中，无法保存完整项目。')
    manifest = {'format':'pdf-studio-project', 'version':1, 'engine_version':fitz.VersionBind,
                'project':payload, 'assets':{'sources':{},'images':{}}}
    files = {}
    for category, keys, data, extension in [('sources',needed_sources,sources,'pdf'), ('images',needed_images,images,'png')]:
        for i, key in enumerate(sorted(keys)):
            path = f'{category}/{i}.{extension}'
            blob = data[key]
            manifest['assets'][category][key] = {'path':path, 'sha256':sha256(blob).hexdigest()}
            files[path] = blob
    if sum(map(len, files.values())) > MAX_ASSET_BYTES:
        raise EditError('项目内源文件与图片超过 200 MB。')
    if len(files) > 5000:
        raise EditError('项目包含超过 5000 个素材，请拆分项目。')
    encoded = json.dumps(manifest, ensure_ascii=False, separators=(',',':')).encode('utf-8')
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise EditError('项目历史超过 8 MB，请另存 PDF 后建立较小项目。')
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('manifest.json', encoded)
        for path, blob in files.items():
            archive.writestr(path, blob)
    result = out.getvalue()
    if len(result) > MAX_PROJECT_BYTES:
        raise EditError('项目文件超过 220 MB。')
    return result


def open_project(raw):
    if len(raw) > MAX_PROJECT_BYTES:
        raise EditError('项目文件最大支持 220 MB。')
    try:
        with ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            names = [entry.filename for entry in members]
            if len(names) > 5001 or len(set(names)) != len(names) or 'manifest.json' not in names:
                raise EditError('项目文件结构无效或有重复条目。')
            if any(entry.flag_bits & 1 or entry.compress_type not in (ZIP_STORED,ZIP_DEFLATED) for entry in members):
                raise EditError('不支持加密或使用特殊压缩方式的项目。')
            if sum(entry.file_size for entry in members) > MAX_ASSET_BYTES + MAX_MANIFEST_BYTES:
                raise EditError('项目解压后的文件超过大小限制。')
            if archive.getinfo('manifest.json').file_size > MAX_MANIFEST_BYTES:
                raise EditError('项目历史超过 8 MB。')
            manifest = Manifest.model_validate_json(archive.read('manifest.json'))
            assets = manifest.assets.model_dump()
            declared = ['manifest.json'] + [asset['path'] for group in assets.values() for asset in group.values()]
            if len(set(declared)) != len(declared) or set(names) != set(declared):
                raise EditError('项目包含缺失、重复或未声明的文件。')
            payload = manifest.project.model_dump()
            needed = references(payload)
            if needed != (set(assets['sources']),set(assets['images'])):
                raise EditError('项目引用的源文件或图片不完整。')
            sources, images, mapping = {}, {}, {'sources':{},'images':{}}
            for category, output, extension in [('sources',sources,'pdf'),('images',images,'png')]:
                for key, asset in assets[category].items():
                    if not asset['path'].startswith(category+'/') or not asset['path'].endswith('.'+extension):
                        raise EditError('项目素材路径无效。')
                    blob = archive.read(asset['path'])
                    if sha256(blob).hexdigest() != asset['sha256']:
                        raise EditError('项目文件校验失败，素材可能已损坏。')
                    new_key = secrets.token_urlsafe(18)
                    mapping[category][key] = new_key
                    if category == 'images':
                        blob = image_tools.import_image(blob, max_bytes=50*1024*1024)['data']
                    else:
                        with fitz.open(stream=blob,filetype='pdf') as doc:
                            if doc.needs_pass or not 1 <= len(doc) <= engine.MAX_PAGES:
                                raise EditError('项目源 PDF 加密或页数无效。')
                            for page in doc:
                                engine.check_page_size(page)
                    output[new_key] = blob
            if sum(map(len,sources.values())) + sum(map(len,images.values())) > MAX_ASSET_BYTES:
                raise EditError('项目素材超过 200 MB。')
            checked = set()
            for page in all_pages(payload):
                if page['source']:
                    page['source'] = mapping['sources'][page['source']]
                for op in page['ops']:
                    if op['kind'] == 'image_replace':
                        op['asset'] = mapping['images'][op['asset']]
                key = json.dumps(page,sort_keys=True,ensure_ascii=False)
                if key not in checked:
                    doc, _ = engine.build_page(page, sources.get(page['source']), images)
                    doc.close()
                    checked.add(key)
            ProjectPayload.model_validate(payload)
            return {'project':payload, 'sources':sources, 'images':images,
                    'warnings':[] if manifest.engine_version == fitz.VersionBind else ['项目来自不同编辑器版本；已检查页面，请核对导出效果。']}
    except EditError:
        raise
    except (BadZipFile, ValidationError, ValueError, KeyError, OSError, RuntimeError, fitz.mupdf.FzErrorBase) as exc:
        raise EditError('无法打开项目，格式、版本或文件内容无效；当前文档未改变。') from exc
