# Agent 交接 · R4 批次 2 —— 大纲可回读 / 上传限界 / 原子写 / 纯读事务 / 配额校验

> 日期：2026-08-31　前置报告：`docs/reviews/r4-batch1-handoff-2026-08-31.md`
> 范围：批次 2 的 5 个缺陷（R4-05 / R4-06 / R4-07 / R4-08 / R4-12），均为付费/并发/边界类中低危优化，无流式二批遗留。
> 交接对象：接手批次 3 或任何改动 `syllabus.py` / `config.py` / `projects.py` 的后续 Agent。

## 0. 现状一句话

五个缺陷已全部修复并有测试覆盖，离线单测 **424 passed**、ruff **干净**；与 0.10.0/批次 1 的改动**均尚未提交**（见 §6 提交建议，仓规强烈建议逐批提交）。

---

## 1. 改了哪些文件（精确到函数）

| 文件 | 函数/位置 | 改动 |
|---|---|---|
| `medkit/core/syllabus.py` | `structurize_outline`（R4-05） | `ok=True`（完整性 ≥95%）→ 自动 `add_seed_items(outline_drafts())` **幂等落库**为官方大纲（`source='seed'`），返回 `source`/`added`——解决了「AI 结构化大纲无出口、烧钱买预览死胡同」 |
| `medkit/core/syllabus.py` | `_rows` / `list_subjects`（R4-08） | 事务由 `tx()` 改 `tx(write=False)` 纯读，避免不必要的写锁争抢 |
| `medkit/core/config.py` | `save`（R4-07） | 手动临时文件+rename 改为统一 `write_json_atomic`（唯一临时名 + Windows 共享冲突重试），与仓库其它状态文件口径一致 |
| `medkit/routers/projects.py` | `upload_asset`（R4-06） | 新增 `_MAX_ASSET_BYTES = 200MB`；`await file.read()` 后立即判定体积超限 → 400，不写磁盘/不进切片索引 |
| `medkit/routers/projects.py` | `create_project`（R4-12） | `official_quota` 越界由静默钳制改为显式 400（与 `web_ref_quota`/`bloom` 口径一致），`meta` 直接存原值不再钳制，依赖上游校验 |
| `tests/test_syllabus_manage.py` | `test_structurize_roundtrip_and_original_store` | 增加 `source`/`added` 断言 + 落库后 `chapter_items_text(source="seed")` 回读验证 |
| `tests/test_wp04.py` | `test_asset_upload_size_limit` | 用 monkeypatch 压缩 `_MAX_ASSET_BYTES`=8 触发超限 → 400，且不落盘/不进切片 |
| `tests/test_s1_backend.py` | `test_create_project_rejects_quota_out_of_range`（新增） | 越界 31/-1/999 → 400 带配额关键词；边界 0/30 放行 |
| `tests/test_s1_backend.py` | `test_config_save_atomic_roundtrip`（新增） | `config.save`→`load` 回读一致 + 无残留 `.tmp` |
| `docs/AGENT_HANDOFF.md` | — | 变更记录追加批次 2 |

---

## 2. 五个缺陷的根因与修法

### R4-05 structurize 编排大纲「无可回读出口」（P1）
- **根因**：`/api/syllabus/outline/structurize` 付费调用 LLM 产出结构化大纲后仅返回 JSON 给前端，产物不落任何库——用户刷新即丢，「烧钱买预览」。官方大纲体系（`load_seed`/`add_seed_items`）空置未复用。
- **修法**：`structurize_outline` 在 `ok=True`（结构化条目数 ≥ 原文 ×95%）时自动 `add_seed_items(outline_drafts(outline))` 幂等落库为 `source='seed'`；返回 `source`/`added`/更新的 `note`。原文仍按 sha1 存 `outline_originals/`（双存储可审计）。不达标保持 `ok=False` 且不静默替换。
- **测试**：`test_syllabus_manage.py::test_structurize_roundtrip_and_original_store` 断言 `source=="seed"`、`added==2`，并 `chapter_items_text(..., source="seed")` 回读含「细胞的基本功能」。

### R4-06 上传文件大小无硬限制（P2）
- **根因**：`/api/projects/{pid}/assets` 对上传图片体积无上限，超大图可撑爆内存/磁盘并拖垮切片索引。
- **修法**：新增 `_MAX_ASSET_BYTES=200*1024*1024` 常量；`await file.read()`（内存）后立即断言 `len(raw) <= _MAX_ASSET_BYTES`，超限 → 400「图片过大（限 200MB）」，后续写磁盘/建切片索引逻辑不再执行。
- **测试**：`tests/test_wp04.py::test_asset_upload_size_limit` 用 monkeypatch 把常量压到 8 字节模拟超限 → 断言 400、且项目 `assets/` 目录不存在（未落盘）。

### R4-07 config 写回非原子（P2）
- **根因**：`config.save` 手动临时文件+`os.replace`，无统一唯一临时名与 Windows 共享冲突重试，多进程/并发保存有中断残留或覆盖风险。
- **修法**：改为 `write_json_atomic(CONFIG_FILE, cfg)`，与仓库 FTS/状态文件同一原子写实现。
- **测试**：`test_s1_backend.py::test_config_save_atomic_roundtrip`（`save`→`load` 回读一致、无 `.tmp` 残留）。

### R4-08 syllabus 纯读事务持写锁（P2）
- **根因**：`syllabus._rows`、`list_subjects` 走默认 `tx()` 开启写事务，纯读操作也抢写锁，与其它写事务争抢。
- **修法**：改为 `tx(write=False)` 纯读事务。由 `db.py` 的 `tx` 支持。

### R4-12 配额超界静默钳制（P2）
- **根因**：`create_project` 的 `official_quota` 超 0~30 时静默钳制，用户以为输入生效、实际被改小，且与 `web_ref_quota`/`bloom` 的 400 口径不一致。
- **修法**：与其它配额统一——`if not (0 <= int(body.official_quota or 0) <= 30): raise HTTPException(400, "官方大纲补充配额需在 0~30% 之间")`；`meta["official_quota"]` 直接存原值，不再本地钳制。
- **测试**：`test_s1_backend.py::test_create_project_rejects_quota_out_of_range`。

---

## 3. 交互决策（为什么这样设计）

- **付费 LLM 产物必须有回读出口**：完整性过关即幂等确立为官方大纲（seed），用户可随时回读/复用；不达标绝不动原文，避免污染官方大纲。
- **统一原子写**：所有状态文件（config/FTS/切片检查点）共用 `write_json_atomic`，一处防并发、处处生效。
- **限界前置在内存读取后、落盘前**：既避免提前流式读整形（复杂），又保证超限不产生任何磁盘副作用。
- **配额 400 而非钳制**：与 repo 既有配额口径一致，用户立即感知输入非法，而非被悄悄改数值。

---

## 4. 验证

- `python -m pytest tests/ --ignore=tests/browser -q` → **424 passed**（批次 2 相关：`test_syllabus_manage.py`/`test_wp04.py`/`test_s1_backend.py`）。
- `ruff check medkit/ tests/` → 干净。

---

## 5. 接手后续注意

- 改动 `structurize_outline` 返回值时同步前端 `syllabus.js`/`learn.js` 对 `source`/`added` 的展示（当前仅 API 层立库，前端展示为增量项）。
- `_MAX_ASSET_BYTES` 若产品侧要调，只需改 `projects.py` 一处常量；前端提示文案同步。
- 新增读取类查询一律 `tx(write=False)`；新增状态写一律 `write_json_atomic`，勿逐文件自造临时名。
- 批次 1 遗留的可选增强（tutor 流式 canceled 是否补发 SSE 帧）仍开放，见批次 1 §5。

## 6. 提交建议

沿用仓规（AGENT_HANDOFF §4.7）：批次 1 与批次 2 改动均未提交。建议按「批次 1 → 批次 2」顺序各自独立提交（仅相关文件 + 测试 + 两份 handoff doc），避免多 Agent 并发覆盖与二合一提审噪声。