# 第三方组件与许可证清单（U-16）

> 用途：回答「发布的安装包/绿色版里含哪些第三方组件、各自什么许可证」——满足
> `docs/engineering/borrow-rules.md` §1 的许可证记录要求。
> 生成方式：从**已验证环境**的 pip 元数据解析运行时依赖闭包（`requirements.txt` 的 11 个
> 直接依赖 → **38 个传递依赖**），版本与 `requirements.lock` 一致。**升级依赖后须同步本文件**。
> 生成日期：2026-09-15（版本基线 v0.10.2；`LICENSE` 追加于 AGPL 开源决策当日）；
> **2026-09-17 勘误**：闭包补 4 项（34→38）、`starlette` 升 1.6.0，详见下方「运行时依赖闭包」小节。
>
> **本项目自身以 [GNU AGPL-3.0](LICENSE) 发布**（见 `docs/adr/ADR-007-license-agpl.md`）。
> 下表仅供第三方依赖的许可证说明，不代表本项目许可证。

## ⚠️ 需注意的许可证（分发前必读）

| 组件 | 许可证 | 影响 |
|---|---|---|
| **PyMuPDF** | **AGPL-3.0 或 Artifex 商业许可（双授权）** | 本项目已接受 **AGPL-3.0**（`ADR-007`）——与 PyMuPDF 的 AGPL 分支许可口径一致，**无需替换**。仍须留意：以 AGPL 组件提供**网络服务**时需遵守 AGPL §13 源码提供义务；本项目为本地桌面应用（API 仅本机回环），不提供面向公众的网络化服务，触发风险低。 |
| **frozendict** | LGPL-3.0 | 弱传染；作为依赖库动态使用（非修改源码/非静态链接进单一二进制）通常可接受，但 PyInstaller onedir 打包属「随产物分发库文件」，建议保留其 LICENSE 原文并在安装包内提供获取源码的说明。 |
| certifi / tqdm | MPL-2.0（tqdm 为 MPL-2.0 AND MIT） | 文件级弱传染；未修改源码即可随产物分发。 |

## 运行时依赖闭包（38 项）

> **2026-09-17 勘误（S2-22 / R8+W）**：原表 34 项，漏掉 `uvicorn[standard]` 的 4 个 extras——
> `httptools` / `python-dotenv` / `watchfiles` / `websockets`（它们确在 `requirements.lock` 且确被打进
> `dist/_internal`）。已补齐为 **38 项**，与 lock 条目数一致。
> 同步更新：`starlette` 1.2.1 → **1.6.0**（CVE-2026-54283 修复线 ≥1.3.1，见 CHANGELOG）。

| 组件 | 版本 | 许可证 |
|---|---|---|
| annotated-doc | 0.0.4 | 见包内 LICENSE |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.13.0 | 见包内 LICENSE |
| cached-property | 2.0.1 | BSD |
| certifi | 2026.4.22 | MPL-2.0 |
| chevron | 0.14.0 | MIT |
| click | 8.4.1 | 见包内 LICENSE |
| colorama | 0.4.6 | BSD License |
| distro | 1.9.0 | Apache License, Version 2.0 |
| fastapi | 0.136.3 | 见包内 LICENSE（MIT） |
| frozendict | 2.4.7 | LGPL v3 |
| fsrs | 6.3.2 | MIT License |
| genanki | 0.13.1 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD License |
| httptools | 0.8.0 | 见包内 LICENSE（MIT） |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.15 | 见包内 LICENSE |
| jieba | 0.42.1 | MIT |
| jiter | 0.15.0 | 见包内 LICENSE |
| lxml | 6.1.0 | BSD-3-Clause |
| Markdown | 3.10.2 | 见包内 LICENSE（BSD） |
| openai | 2.41.0 | Apache-2.0 |
| pydantic | 2.13.4 | 见包内 LICENSE（MIT） |
| pydantic_core | 2.46.4 | 见包内 LICENSE（MIT） |
| PyMuPDF | 1.27.2.3 | **AGPL-3.0 或 Artifex 商业许可** |
| python-docx | 1.2.0 | MIT |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache Software License |
| PyYAML | 6.0.3 | MIT |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| starlette | 1.6.0 | 见包内 LICENSE（BSD） |
| tqdm | 4.68.1 | MPL-2.0 AND MIT |
| typing_extensions | 4.15.0 | 见包内 LICENSE |
| typing-inspection | 0.4.2 | 见包内 LICENSE |
| uvicorn | 0.49.0 | 见包内 LICENSE（BSD） |
| watchfiles | 1.2.0 | MIT |
| websockets | 16.0 | BSD-3-Clause |

> 「见包内 LICENSE」= 该发行包元数据未给出 SPDX 标识，需在安装包内保留其
> `dist-info/LICENSE*` 原文（PyInstaller onedir 默认保留 `dist-info`，打包后抽查确认）。

## ⚠️ 产物实况与声明不符（2026-09-17 实测，S2-18 / S2-22）

> 上表是**声明闭包**（lock 的 38 项）。实测 `dist/MedKit/_internal`（v0.10.3 产物）与声明**不一致**，
> 在重出包前请以本节为准：

1. **产物含未声明组件**：`attrs` / `email_validator` / `itsdangerous` / `importlib_metadata` 等
   出现在 `dist/_internal` 的 `*.dist-info` 中，但**不在** lock 闭包内——系构建机环境被 PyInstaller
   一并收集（S2-18）。**成因是打包环境不纯净，不是代码依赖**。
2. **`chardet` 被打进产物且是残件**：`dist/_internal/chardet/` 只有 `models/`、`pipeline/`、`py.typed`，
   **缺顶层 `__init__.py`**（无法 import），且**无 `chardet-*.dist-info`**（许可证原文未随产物分发）。
   - **许可证更正**：实测环境 `chardet 7.4.3` 的元数据为 **`License-Expression: 0BSD`**（宽松许可），
     **不是**早期版本的 LGPL-2.1——`S2-18`/`S2-22` 原文写「LGPL chardet」与实装版本不符。
     若打包机上是 **chardet ≤5.x**（LGPL-2.1），则需按弱传染处理并随产物提供源码获取说明。
3. **多数依赖缺 `dist-info`**：`dist/_internal` 有 28 个模块目录，却只有 **9 个 `*.dist-info`**，
   即「随产物保留各依赖 `LICENSE` 原文」这条要求**当前产物大面积未满足**（与下文「打包侧要求」第 1 条冲突）。
4. **修复方向**（属打包/发布动作，需重出包验证）：在干净虚拟环境里构建、把 `chardet` 等非闭包包
   排除出 spec、并断言 `dist` 的包集合 == lock 闭包集合（已加守卫：`pack/check-package.py` 的闭包断言）。

## 打包侧要求（与 `pack/check-package.py` 配套）

1. 安装包/绿色版内**保留**各依赖的 `*.dist-info/LICENSE*`（onedir 默认满足，出包后抽查）；
2. 新增依赖时按 `borrow-rules.md` §1 记录「名称 / 版本 / 许可证 / 体积」，并更新本文件与
   `requirements.lock`；
3. `PyMuPDF` 的 AGPL 决策已定案：**接受 AGPL 开源**（本项目整体以 AGPL-3.0 发布，见
   `docs/adr/ADR-007-license-agpl.md`）——与 PyMuPDF 许可口径一致，无需替换实现。
