# Third-party notices / 第三方依赖与发布说明

## 项目代码与依赖必须分开看待

本项目新增的前后端代码按根目录 `LICENSE`（MIT）提供。这个许可**不替代第三方依赖的许可**，也不意味着将本项目与依赖一起部署或分发就能不受依赖条款约束。

底层 PDF 引擎 **PyMuPDF / MuPDF 采用 AGPL 开源许可与商业许可双重授权**。请阅读实际安装版本附带的许可文件以及 Artifex 的官方说明，再决定你的组合应用、修改、分发和网络服务如何满足相应条件。闭源商用发布前尤其需要核对授权路线；本项目的 MIT 文件不覆盖这部分。

官方来源（2026-09-26 查阅）：

- PyMuPDF License and Copyright：<https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright>
- Artifex Licensing：<https://artifex.com/licensing>
- GNU AGPL：<https://www.gnu.org/licenses/agpl-3.0.html>

这不是对具体部署情形的法律结论。

## 安装清单

运行时直接依赖为 `requirements.txt` 中的 FastAPI、Uvicorn、python-multipart、PyMuPDF，以及 v1.1 新增的 pypdf（解析图片绘制指令）和 Pillow（图片验证、方向与透明度处理）。开发与测试依赖见 `requirements-dev.txt`。依赖及其传递依赖由 pip 单独安装；源码仓库不附带第三方 wheel、Python 解释器、浏览器二进制文件或独立字体文件。请保留实际依赖包附带的许可及版权声明。

这些版本是本项目实际验证所用的固定版本，**不是“当前最新”或“已通过全部安全审计”的声明**。升级 PDF 引擎后请重跑测试，尤其是文字移除和 CropBox/旋转坐标的回归测试。

示例 PDF 是本项目生成的虚构演示文档。排版使用 PDF 引擎自带的字体回退功能，不需要额外下载字体。
