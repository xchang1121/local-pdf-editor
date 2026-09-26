"""Validated, replayable editing commands. Coordinates use visible page points."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[float, Field(allow_inf_nan=False)]
Box = tuple[Number, Number, Number, Number]
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextStyle(StrictModel):
    size: float = Field(default=14, ge=4, le=200, allow_inf_nan=False)
    color: HexColor = "#172033"
    family: Literal["original", "sans-serif", "serif", "monospace"] = "sans-serif"
    bold: bool = False
    italic: bool = False
    align: Literal["left", "center", "right", "justify"] = "left"
    line_height: float = Field(default=1.25, ge=0.85, le=3, allow_inf_nan=False)
    fit: bool = True
    background: HexColor | None = None


class TextOp(StrictModel):
    kind: Literal["text", "replace"]
    rect: Box
    text: str = Field(default="", max_length=20000)
    style: TextStyle = Field(default_factory=TextStyle)
    # A name extracted from this page, never a filesystem path or font URL.
    font: str | None = Field(default=None, max_length=250)
    # Original glyph areas are distinct from the new text's destination.
    # Moving a replacement must remove text at its old position, not the new one.
    erase: list[Box] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def check_erase(self):
        if self.kind == "replace" and not self.erase:
            raise ValueError("替换文字必须指定原文字区域。")
        if self.kind == "text" and self.erase:
            raise ValueError("新增文字不应包含删除区域。")
        return self


class RectOp(StrictModel):
    kind: Literal["cover", "redact", "crop"]
    rect: Box
    color: HexColor = "#ffffff"


class RotateOp(StrictModel):
    kind: Literal["rotate"]
    angle: Literal[90, 180, 270] = 90


class ImageReplaceOp(StrictModel):
    kind: Literal["image_replace"]
    image_index: int = Field(ge=0, le=100000)
    bbox: Box
    digest: str = Field(pattern=r"^[0-9a-f]{32}$")
    asset: str = Field(min_length=1, max_length=100)
    fit: Literal["contain", "cover", "stretch"] = "contain"


Operation = Annotated[TextOp | RectOp | RotateOp | ImageReplaceOp, Field(discriminator="kind")]


class PageSpec(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    source: str | None = Field(default=None, max_length=100)
    index: int = Field(default=0, ge=0, le=10000)
    label: str = Field(default="", max_length=250)
    width: float = Field(default=595, ge=8, le=14400, allow_inf_nan=False)
    height: float = Field(default=842, ge=8, le=14400, allow_inf_nan=False)
    ops: list[Operation] = Field(default_factory=list, max_length=250)


class PreviewRequest(StrictModel):
    page: PageSpec
    scale: float = Field(default=1.5, ge=0.1, le=4, allow_inf_nan=False)
    text: bool = True


class ExportRequest(StrictModel):
    pages: list[PageSpec] = Field(min_length=1, max_length=300)
    mode: Literal["vector", "raster"] = "vector"
    dpi: int = Field(default=180, ge=72, le=300)
    filename: str = Field(default="edited.pdf", max_length=150)
