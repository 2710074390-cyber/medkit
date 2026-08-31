# Agent 交接 · R4 批次 1 —— 流式主路径四缺陷修复

> 日期：2026-08-31　审查报告：`docs/reviews/ux-audit-r4-2026-08-31.md`
> 范围：批次 1 的 4 个缺陷（R4-01 / R4-02 / R4-03 / R4-04），全部 P0/P1，属「流式主路径」抑制体验与成本的双扣费/不可取消/断流重生成/空会话残留问题。
> 交接对象：接手批次 2（R4-05/06/07/08/12）或任何需改动 `library.py` 流式端点的后续 Agent。

## 0. 现状一句话

四个缺陷已按批次 1 全部修复，离线单测 **421 passed**、ruff **干净**；0.10.0 与本次改动**均已修改但尚未提交**（见 §6 提交建议）。

---

## 1. 改了哪些文件（精确到函数）

| 文件 | 函数/位置 | 改动 |
|---|---|---|
| `medkit/routers/library.py` | `_explain_client(cancel=None)` / `_tutor_client(cancel=None)` | 两个 client 工厂加 `cancel` 透传，作为**单一可 mock 注入触点**（否则测试无法拦截流式路径） |
| `medkit/routers/library.py` | `explain_stream`（R4-01/02） | `dedupe.begin/end` 从 FastAPI 依赖移入 `gen()`；`end`/`cancel_ev.set()` 放 `finally`（含断连 GeneratorExit）；client 经 `_explain_client(cancel=cancel_ev)` |
| `medkit/routers/library.py` | `tutor_start_stream`（R4-01/02/04） | 同上；新增 `seeded` 标志，`finally` 中未 `seed_first` → `tut.delete_session(sid)` 兜底清理空会话 |
| `medkit/agents/__init__.py` | `get_client(role, cancel)` | 已支持 `cancel`（0.10.0 已含，本次确认下透口径） |
| `medkit/core/llm.py` | `LLMClient.chat_stream` | `cancel_ev.is_set()` 时 yield `{canceled:True}`（0.10.0 已含） |
| `medkit/web/js/learn.js` | `sseAbortAll` / `sseStopUI` | 流式取消基础件：全局 `_sseAbort`；「■ 停止生成」按钮（插入 `btn_exp_gen`/`btn_tu_start` 后） |
| `medkit/web/js/learn.js` | `expGenerate` / `tutorStart` | `AbortController` + `signal` 绑 fetch + `sseStopUI(...)`；`finally { sseAbortAll() }` 清按钮；catch 区分 `AbortError`/streamed/未started |
| `medkit/web/js/learn.js` | `showLearnView` | 切子视图/离开学习中心 → `sseAbortAll()` 中止在途流式 |
| `medkit/web/js/app.js` | `showTab` | 切走 `learn` tab（且非切回 learn）→ `window.sseAbortAll()` |
| `docs/AGENT_HANDOFF.md` / `docs/reviews/ux-audit-r4-2026-08-31.md` | — | 追加批次 1 落地记录 / 批次 1 打 ✅ |

---

## 2. 四个缺陷的根因与修法

### R4-01 流式「在飞去重」过早释放 → 双击/双标签并发双扣费（P0）
- **根因**：原实现用 FastAPI `yield` 依赖持有 `dedupe` 锁；但 `StreamingResponse` 返回后依赖的 `finally` **立即**释放锁，流式请求实际仍在飞——并发双击/双标签可绕过在飞去重，形成两个并发 LLM 流 + 双份扣费。
- **修法**：把 `dedupe.begin(key)`（409 短路径，未获锁无需 end）放在 `gen()` 之前，`dedupe.end(key)` **只**放在 `gen()` 的 `finally`——锁的持有期 ≡ 流生命周期（`done`/`error`/`canceled`/客户端断连 `GeneratorExit` 任一出口都释放）。准备阶段异常也 `end` 防锁泄漏。
- **测试**：`tests/test_dedupe.py`（begin/end 契约、异 key 独立、end 幂等）。

### R4-02 流式不可取消 + 服务端 `canceled` 是死代码（P1）
- **根因**：前端无取消按钮，切视图也不中止 fetch；服务端虽有 `canceled` 分支，但从未有 `cancel` 事件传入 client，`chat_stream` 里的 `canceled` 分支永不可达。
- **修法**：前端 `expGenerate`/`tutorStart` 用 `AbortController` 绑 fetch，`sseStopUI` 展示停止按钮，`showLearnView`/`showTab` 切走即 `sseAbortAll()`；后端创建 `cancel_ev=threading.Event()` 传入 client（经 `_explain_client(cancel=...)`），`gen()` finally `cancel_ev.set()` 打断 provider 残余流式请求。
- **测试**：`tests/test_explain_stream.py::test_explain_stream_canceled_no_save`（服务端 cancel → 发 `canceled`、不落盘）。

### R4-03 断流自动回退非流式 → 重复整题生成 / token 双花（P1）
- **根因**：`expGenerate`/`tutorStart` 的 catch 对**任何**异常都再调非流式端点（`/explain`、`/tutor/start`）——一次断流或取消会触发第二次整题生成 + 二次扣费。
- **修法**：仅当 `streamed` **未置位**（即流式接口本身不可用：header 非 SSE）才降级非流式；`AbortError`→「已停止生成（未保存）」、断流/出错→仅提示，**一律不再二次请求**。用 `throw new Error("stream-unavailable")` 区分「接口不可用」与「流式进行中失败」。

### R4-04 tutor 流客户端断连残留空会话（P1）
- **根因**：`tutor_start_stream` 里 `start_session` 建会话在前、LLM 出首问在后；若流中断/断连/模型返回空，`seed_first` 未执行，空会话残留在册。
- **修法**：`finally` 中若 `not seeded`（未 `seed_first`）→ `tut.delete_session(sid)`（幂等），把回滚覆盖到「客户端异常断开」而非仅显式取消。
- **测试**：`tests/test_tutor_stream.py::test_tutor_start_stream_canceled_session_cleaned`（cancel → sessions 为空）。

---

## 3. 交互决策（为什么这样设计）

- **取消后不保留半成品**：流量进行中停止＝「已停止生成（未保存）」，不落讲解产物、撤销 tutor 会话；与「完成才落盘」的既有语义一致，避免垃圾产物（讲解不可用、tutor 缺首问）。
- **一次性失败不自动重试**：成本敏感应用里，断流/出错优先给用户可重试提示，而不是静默二次请求——网络抖动不再叠加双倍计费。
- **单一 mock 触点**：流式端点统一走 `_explain_client`/`_tutor_client`（带 `cancel`），保证「测试可注入假 client」与「生产可注入取消事件」收敛到同一入口；后续加角色/Provider 只改一处。
- **切视图即中止**：学习中心隐藏容器里继续白烧 token 是浪费，因此 `showLearnView` 与 `showTab` 都触发 `sseAbortAll()`。

---

## 4. 验证

- `python -m pytest tests/ --ignore=tests/browser -q` → **421 passed**。
- 定向：`test_explain_stream.py`（5）、`test_tutor_stream.py`（4）、`test_dedupe.py`（3）。
- `ruff check medkit/routers/library.py tests/...` → 干净。
- 浏览器层（Playwright，需 `SET SKIP_BROWSER=1` 可跳过）：`tests/browser/test_tutor_immersive.py` 可跑，含流式沉浸。

---

## 5. 接手批次 2 / 后续注意

- 批次 2（下一步）：R4-05 structurize 落库、R4-06 上传 200MB 限界、R4-07 config 原子写、R4-08 syllabus 纯读事务、R4-12 配额超界统一 400。
- 改 `_explain_client`/`_tutor_client` 时同步 `tests/test_explain_stream.py`、`tests/test_tutor_stream.py` 里 mock 的 `cancel=None` 形参，别用无参 lambda。
- 新增流式端点：必须沿用「`dedupe.begin` 前置 + `gen()` finally 里 `end`/`cancel_ev.set()`」模式，勿退回 FastAPI 依赖持锁（那是 R4-01 的坑）。
- 若给 tutor 流式「canceled」补发 SSE 帧（当前服务端 cancel 分支直接 `return` 不广播），注意前端已能自处理 `AbortError`，属可选增强。

## 6. 提交建议

本次改动与 0.10.0 未提交的既有改动混在工作区。按本仓库警示（AGENT_HANDOFF §4.7），强烈建议批次 1 锁定后**立即单独提交**（仅相关文件 + 测试 + 两份 doc），再继续批次 2，规避多 Agent 并发覆盖风险。