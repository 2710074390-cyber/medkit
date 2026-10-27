"""题库同步（banksync）：从 MedAgentWork 项目拉取最新题库 → 并入本地「题库」域。

## 为什么要有这条通道（而不是让用户手动导 JSON）

MedAgentWork 是配套的内容仓（`2710074390-cyber/MedAgentWork`），题库按批次持续新增/修复。
用户此前只能：打开 GitHub → 找到 `最终产物/batchNNN/ALL_questions_FIXED.json` → 下载 →
在「刷题」页导入。四个动作、每次都重复，而且**哪个批次更新过**全靠记。
本模块把这条路径收成一个按钮：列出远端题库 → 用户勾选 → 同步入库。

## 为什么单独写适配器，不复用 `parse_import_text(json)`

通用 JSON 导入只认 `question/stem/options/answer/analysis` 这几个字段，其余全丢：

| 上游字段 | 通用导入的结果 | 本适配器 |
|---|---|---|
| `type`（A1/A2/B1/A3/A4/X） | 丢 → 按答案字母数**重推**为 A1/X，B1·A2·A3·A4 全塌成 A1 | 原样保留 |
| `bloom`（记忆/理解/应用/分析） | 丢 | 原样保留（英文值译回中文） |
| `topic` / `module_name` | 落到 `know_tags`（错题本字段，题库不认） | 落到 `subtopic`（题库页签按它分组） |
| `source_page` | 丢 | 进 `origin_ref.page` |
| `kaoyan_origin`（真题出处 + 年份） | 丢 | 进 `source_type` / `source_year` |

「导入即降级」是本仓明确的红线（见 `routers/bank.import_project` 的注释），
所以这里**保留原义**，而不是图省事复用通用通道。

## 远端契约（2026-10-27 实测，不是猜的）

仓库里**没有**一份「全部题库」的合并 JSON，只有：

- `最终产物/<batch>/ALL_questions_FIXED.json` —— 每批一份完整题目（本地实测 8 份 / 2047 题）；
- `最终产物/押题卷_<科目>_<N>题.json` —— 单卷题库；
- `question_bank/registry.jsonl` —— **只有 qid + 题干片段**（`stem_snippet`），**没有完整题干**
  ⇒ 不能拿它当题库用，只用来给 UI 报「远端共 6490 题」这个量级。

因此同步面 = 上面两类完整题目文件（`_is_bank_file()`）。**不扫** `AGENT4_追溯日志.json` /
`learning_value_*.json`（日志与合并记录，不是题目）。

### 上游存在两代题面结构（必须都认）

| | v1（batch032） | v2（batch033+） |
|---|---|---|
| 题干 | `question_text` | `stem` |
| 答案 | `correct_answer` | `answer` |
| 题型 | `question_type` | `type` |
| Bloom | `bloom_level` | `bloom` |
| 选项 | `["A. 甲", …]`（带字母的字符串） | `[{label, text}, …]` |

适配器按「先 v2 再 v1」依次取值，两种结构落到同一条 `adapt_item()` 上。

### 刻意**不**搬运的字段（并说明理由）

- `b1_group` / `case_group`：上游 B1 组题**每题都自带完整 5 个选项**、A3/A4 的
  `stem` **已含完整病例**。映射成 MedKit 的 `group` / `case_id` 会让渲染层
  再显示一遍共享选项/病例（`qbank_html._case_blocks` 会取 `case_stem` 当组头），
  即**同一段文字出现两次**。故按「独立题」落库——内容一字不少，只是不重组。
- `learning_value`（逐项解析/误区编号）：结构嵌套且随批次版本漂移，本题库域无对应字段，
  硬塞进 `analysis` 会把正文与结构化数据搅在一起。原文仍在 GitHub，需要时可回查。

## 网络

**只走 `api.github.com`**：实测 `raw.githubusercontent.com` 在本机连不上（`curl` exit 35，
SSL 握手失败），而 API 正常。故一律用 Blob API（base64）取文件——顺带绕开 raw 域名的可用性问题。

未认证 GitHub API 限流 60 次/小时：一次完整同步 ≈ 1（tree）+ 1（meta）+ N（题库文件）次，
N 目前 11 ⇒ 一次约 13 次，够用两三轮。设 `GITHUB_TOKEN`（或配置 `github_token`）可提到 5000。
限流命中时抛 `SyncError(kind="ratelimit")`，把重置时间如实告诉用户，不静默重试到死。

## 幂等与「不覆盖用户改动」

- 每个源记 `sha`；远端未变且题库还在 ⇒ 直接 `unchanged`，**不发请求也不动数据**；
- 变了才下载，并用 `dedupe="update"` **按内容键**合并：命中同题的旧行保留 `id`，
  ⇒ 该题的**作答流水与收藏/笔记全在**（`bank.add_items` 的既有语义，本模块不另造一套）。
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .. import __version__
from . import bank as banklib
from . import config as cfg
from . import errors as errs
from .fsutil import write_json_atomic

REPO = "2710074390-cyber/MedAgentWork"
HTML_URL = f"https://github.com/{REPO}"
API = "https://api.github.com"

STATE_VERSION = 1
TREE_TTL = 600.0            # tree 内存缓存（10 分钟）：反复开同步面板不重复吃限流
MAX_BLOB = 32 * 1024 * 1024  # 单文件上限（实测最大 1.0MB；32MB 留足余量又挡异常大文件）
TIMEOUT = 30.0
RETRIES = 3


class SyncError(Exception):
    """同步失败（带 `kind` 供上层决定 HTTP 状态与文案）。

    kind ∈ {network, ratelimit, notfound, format, busy, empty}
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


# ---------------------------------------------------------------- 状态文件
def _state_file() -> Path:
    """同步状态落盘位置（**运行时取** `cfg.CONFIG_DIR`）。

    ⚠️ 不能写成模块级常量：测试与「数据目录迁移」都会在 import 之后改 `cfg.CONFIG_DIR`，
    常量会把状态写到旧目录（本仓 `db.py` / `library.py` 的历史坑，见 conftest 的隔离说明）。
    """
    return cfg.CONFIG_DIR / "banksync" / "state.json"


def load_state() -> dict[str, Any]:
    """读同步状态；缺失/损坏 → 空状态（**不阻断**，下次同步会当作首次）。"""
    path = _state_file()
    empty: dict[str, Any] = {"version": STATE_VERSION, "sources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except Exception as e:  # noqa: BLE001  损坏不阻断同步，但要留痕
        errs.record("banksync.state", "同步状态文件损坏，按空状态继续", e=e)
        return empty
    if not isinstance(data, dict):
        return empty
    srcs = data.get("sources")
    return {"version": STATE_VERSION, "sources": srcs if isinstance(srcs, dict) else {}}


def save_state(state: dict[str, Any]) -> None:
    state = dict(state or {})
    state["version"] = STATE_VERSION
    state["updated_at"] = _now()
    write_json_atomic(_state_file(), state)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- HTTP
def _token() -> str:
    """GitHub Token（可选）：环境变量优先，其次配置项。空串 = 匿名请求。"""
    tok = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        return str(cfg.load().get("github_token") or "").strip()
    except Exception as e:  # noqa: BLE001  配置不可读 → 匿名继续（限流更紧但不致命）
        errs.record("banksync.token", "读取 github_token 失败，按匿名请求继续", e=e)
        return ""


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": f"MedKit/{__version__}"}
    tok = _token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _ratelimit_msg(resp: httpx.Response) -> str:
    reset = resp.headers.get("x-ratelimit-reset") or ""
    when = ""
    try:
        when = datetime.fromtimestamp(int(reset)).strftime("%H:%M")
    except (TypeError, ValueError):
        when = ""
    hint = "（可在设置里填 github_token 把限额提到 5000 次/小时）"
    return "GitHub 接口限流（匿名 60 次/小时）" + (f"，{when} 后重置" if when else "") + hint


def _http_json(url: str, what: str) -> Any:
    """GET → JSON。**明确区分**四类失败，并只对「可能自愈」的那类重试。

    ⚠️ 不重试 4xx（限流 / 不存在 / 仓库私有）：重试三次只会把 60 次限额烧得更快，
    且结果不会变——除非用户在中间填了 token，那也该让他自己点重试。
    ⚠️ 要重试 `IncompleteRead`（`httpx.RemoteProtocolError` 一族）——实测本机拉 1MB blob
    会偶发分块截断（本次开发中 11 份里命中 2 次），不重试就是「同步到一半随机失败」。
    """
    last: Optional[Exception] = None
    for attempt in range(RETRIES):
        try:
            resp = httpx.get(url, headers=_headers(), timeout=TIMEOUT, follow_redirects=True)
            if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
                raise SyncError("ratelimit", _ratelimit_msg(resp))
            if resp.status_code == 404:
                raise SyncError("notfound", f"{what}不存在（仓库或文件已改名/删除）")
            if 400 <= resp.status_code < 500:
                raise SyncError("format", f"{what}请求被拒（HTTP {resp.status_code}）")
            resp.raise_for_status()
            return resp.json()
        except SyncError:
            raise
        except Exception as e:  # noqa: BLE001  网络/协议/解析：可重试
            last = e
            if attempt < RETRIES - 1:
                time.sleep(0.4 * (attempt + 1))
    raise SyncError("network", f"{what}拉取失败：{type(last).__name__} {last}")


# ---------------------------------------------------------------- 远端目录（tree）
_tree_lock = threading.Lock()
_tree_cache: dict[str, Any] = {"at": 0.0, "data": None}
_meta_lock = threading.Lock()
_meta_cache: dict[str, Any] = {"at": 0.0, "sha": "", "data": None}


def _tree(refresh: bool = False) -> dict[str, Any]:
    """仓库文件树（缓存 10 分钟）。返回 `{"tree": [...], "fetched_at": iso}`。"""
    with _tree_lock:
        cached = _tree_cache.get("data")
        if not refresh and cached and (time.time() - float(_tree_cache.get("at") or 0)) < TREE_TTL:
            return cached
        data = _http_json(f"{API}/repos/{REPO}/git/trees/HEAD?recursive=1", "远端文件树")
        if not isinstance(data, dict) or not isinstance(data.get("tree"), list):
            raise SyncError("format", "远端文件树格式异常（不是预期的 tree 结构）")
        if data.get("truncated"):
            # GitHub 超过 10 万条目会截断。本仓实测 2022 条；真截断了如实说，不假装完整。
            raise SyncError("format", "远端文件树被 GitHub 截断，无法可靠列举题库")
        out = {"tree": data["tree"], "fetched_at": _now()}
        _tree_cache["data"] = out
        _tree_cache["at"] = time.time()
        return out


def _reset_cache() -> None:
    """清空 tree / meta 缓存（测试 / 「刷新」按钮用）。"""
    with _tree_lock:
        _tree_cache["data"] = None
        _tree_cache["at"] = 0.0
    with _meta_lock:
        _meta_cache.update({"sha": "", "at": 0.0, "data": None})


# ---------------------------------------------------------------- 源发现
_BATCH_FILE_RE = re.compile(r"^最终产物/(?P<dir>[^/]+)/ALL_questions_FIXED\.json$")
_PAPER_FILE_RE = re.compile(r"^最终产物/(?P<dir>[^/]+)/押题卷[^/]*\.json$")
_ROOT_PAPER_RE = re.compile(r"^最终产物/(?P<name>押题卷[^/]*)\.json$")

# 科目名后缀（从上游 md 文件名反推科目）：**按长度降序**剥，短后缀先剥会把长后缀切坏
_SUBJECT_SUFFIXES = (
    "教师重点版题库", "真题专项题库", "期末题库", "教学题库", "题库", "押题卷",
    "期末", "复习资料",
)


def _clean_subject(name: str) -> str:
    """`外科教学题库` → `外科`；`耳鼻咽喉头颈外科学·期末题库` → `耳鼻咽喉头颈外科学`；
    `押题卷_耳鼻咽喉头颈外科学_100题` → `耳鼻咽喉头颈外科学`。

    只做**机械剥离**，不做同义词归一（不把「外科」改写成「外科学」）——
    科目名是上游的命名，替它改名等于凭空造事实；用户看到的就是上游原文的简化。
    """
    s = str(name or "").strip()
    s = re.sub(r"[_\-—]\s*\d+\s*题$", "", s)     # `…_100题` 收尾的计数不是科目名
    s = re.sub(r"^押题卷[_\-—]\s*", "", s)        # 卷别前缀同样不是科目名
    changed = True
    while changed and s:
        changed = False
        for pat in _SUBJECT_SUFFIXES:
            if s.endswith(pat) and len(s) > len(pat):
                s = s[: -len(pat)].strip(" ·_-—")
                changed = True
                break
    return s.strip(" ·_-—")


def _is_bank_file(path: str) -> bool:
    """是不是「完整题目」文件（见模块 docstring 的远端契约）。"""
    return bool(_BATCH_FILE_RE.match(path) or _PAPER_FILE_RE.match(path)
                or _ROOT_PAPER_RE.match(path))


def _subject_from_paths(path: str, tree_paths: list[str]) -> str:
    """从同目录的 `.md` 产物推断科目（`外科教学题库.md` → 外科）。

    ## 判据是**正例**，不是黑名单

    实测踩过：按「取最短的同目录 md 名」推断，`最终产物/batch035/AGENT4_修改声明.md`
    比 `ALL_questions_FIXED.md` 短 ⇒ 整批科目被认成「AGENT4_修改声明」，题库名跟着变成
    「AGENT4_修改声明（MedAgentWork · batch035）」。batch038/040/042 同病
    （`escalations_for_human.md` / `…_溯源表.md`）。

    故改为：**只有文件名里带「题库」或「押题卷」的 md 才算科目线索**。
    实测 11 份产物全部命中这个正例（`外科教学题库.md` / `…·期末押题卷.md` /
    `…·真题专项题库.md`），而修改声明/溯源表/升级单一个都不带这两个词。
    一条都取不到就返回空串——**不猜**（题库名回退成批次号，用户至少知道是哪一批）。
    """
    m = _BATCH_FILE_RE.match(path) or _PAPER_FILE_RE.match(path)
    if m:
        d = m.group("dir")
        prefix = f"最终产物/{d}/"
        names = [p[len(prefix):] for p in tree_paths
                 if p.startswith(prefix) and p.count("/") == 2 and p.endswith(".md")]
        cands = [n for n in names if ("题库" in n or "押题卷" in n)
                 and n != "ALL_questions_FIXED.md"]
        for n in sorted(cands, key=len):       # 最短者最可能只是科目名
            sub = _clean_subject(n[:-3])
            if sub:
                return sub
        return ""
    m = _ROOT_PAPER_RE.match(path)
    if m:
        return _clean_subject(m.group("name"))
    return ""


def _batch_of(path: str) -> str:
    m = _BATCH_FILE_RE.match(path) or _PAPER_FILE_RE.match(path)
    return m.group("dir") if m else ""


def _meta(tree_paths: list[dict[str, Any]]) -> dict[str, Any]:
    """`question_bank/registry_meta.json`：只用来给 UI 报「远端共 N 题 / 各批多少题」。

    ⚠️ 这是**元数据不是题库**（`registry.jsonl` 只有 `stem_snippet`，没有完整题干）。
    取不到不影响同步，一律降级为空 dict。结果按 sha 缓存，与 tree 同寿命——
    否则每次开面板都多打一次接口，匿名额度（60 次/小时）白烧。
    """
    sha = next((t.get("sha") for t in tree_paths
                if t.get("path") == "question_bank/registry_meta.json"), "")
    if not sha:
        return {}
    with _meta_lock:
        if (_meta_cache.get("sha") == sha
                and (time.time() - float(_meta_cache.get("at") or 0)) < TREE_TTL):
            return _meta_cache.get("data") or {}
    data: dict[str, Any] = {}
    try:
        got = _blob_json(str(sha), "题库索引元数据")
        data = got if isinstance(got, dict) else {}
    except SyncError:
        data = {}
    with _meta_lock:
        _meta_cache.update({"sha": sha, "at": time.time(), "data": data})
    return data


def _blob_json(sha: str, what: str) -> Any:
    """取 Blob → JSON。**字节数对不上就重取**（见下）。

    ⚠️ `_http_json` 的重试只管「HTTP/传输层报错」。实测还有一种**不报错**的截断：
    响应 200、`content` 是一段合法 base64，但解出来比 `size` 短——直接 `json.loads`
    只会得到 `JSONDecodeError`，看起来像「远端文件坏了」，其实是这一趟没拉全。
    故在此按 `size` 校验并重取，且**只对这一类**重取（真格式错重试也没用）。
    """
    last = ""
    for attempt in range(RETRIES):
        data = _http_json(f"{API}/repos/{REPO}/git/blobs/{sha}", what)
        if not isinstance(data, dict) or "content" not in data:
            raise SyncError("format", f"{what}内容格式异常")
        size = int(data.get("size") or 0)
        if size > MAX_BLOB:
            raise SyncError("format", f"{what}过大（{size / 1024 / 1024:.1f}MB），已跳过")
        raw = base64.b64decode(data.get("content") or "", validate=False)
        if size and len(raw) != size:
            last = f"{what}下载不完整（{len(raw)}/{size} 字节）"
            if attempt < RETRIES - 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            raise SyncError("network", last)
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SyncError("format", f"{what}不是合法 UTF-8 JSON：{e}")
    raise SyncError("network", last or f"{what}下载失败")


# ---------------------------------------------------------------- 对外：源列表
def list_sources(refresh: bool = False) -> dict[str, Any]:
    """远端题库清单（含批次/科目/大小/预估题数）。"""
    tree = _tree(refresh)
    entries: list[dict[str, Any]] = tree["tree"]
    paths = [str(t.get("path") or "") for t in entries if t.get("type") == "blob"]
    by_path = {str(t.get("path")): t for t in entries}
    meta = _meta(entries)
    by_batch = (((meta.get("stats") or {}).get("by_batch")) or {}) if isinstance(meta, dict) else {}
    sources: list[dict[str, Any]] = []
    for p in paths:
        if not _is_bank_file(p):
            continue
        t = by_path.get(p) or {}
        batch = _batch_of(p)
        sources.append({
            "path": p,
            "batch": batch,
            "subject": _subject_from_paths(p, paths),
            "sha": str(t.get("sha") or ""),
            "size": int(t.get("size") or 0),
            # 预估题数：registry_meta 按批次聚合，未必与文件逐题对齐 ⇒ 只作量级提示
            "est_count": int(by_batch.get(batch) or 0) if batch else 0,
        })
    sources.sort(key=lambda s: (s["batch"] or "~", s["path"]))
    return {
        "repo": REPO,
        "url": HTML_URL,
        "fetched_at": tree.get("fetched_at"),
        "remote_total": int(((meta.get("stats") or {}).get("total") or 0)) if meta else 0,
        "remote_updated_at": str(meta.get("updated_at") or "") if isinstance(meta, dict) else "",
        "sources": sources,
    }


def _bank_exists(bid: str) -> bool:
    if not bid:
        return False
    try:
        return banklib.get_bank(bid) is not None
    except Exception as e:  # noqa: BLE001  库不可读 → 当作不存在（会重建题库），但要留痕
        errs.record("banksync.bank_exists", "查询题库失败，按不存在处理", e=e, bank_id=bid)
        return False


def status(refresh: bool = False) -> dict[str, Any]:
    """远端清单 + 本地同步状态（**只读**，不建库、不写文件）。

    网络不可用时**不抛错**：降级成「只用本地状态」并把 `error` 交回前端展示——
    用户点开面板看到的是"上次同步于…"，而不是一个红叉。
    """
    state = load_state()
    try:
        info = list_sources(refresh=refresh)
    except SyncError as e:
        errs.record("banksync.status", "拉取远端题库清单失败", kind=e.kind, detail=e.message)
        local = []
        for path, rec in (state.get("sources") or {}).items():
            if not isinstance(rec, dict):
                continue
            local.append({"path": path, "batch": rec.get("batch") or "",
                          "subject": rec.get("subject") or "", "sha": rec.get("sha") or "",
                          "size": 0, "est_count": int(rec.get("count") or 0),
                          "state": "offline", "bank_id": rec.get("bank_id") or "",
                          "synced_at": rec.get("synced_at") or "",
                          "count": int(rec.get("count") or 0)})
        local.sort(key=lambda s: (s["batch"] or "~", s["path"]))
        return {"ok": False, "error": e.message, "error_kind": e.kind,
                "repo": REPO, "url": HTML_URL, "fetched_at": "",
                "remote_total": 0, "remote_updated_at": "",
                "sources": local, "local_only": True}

    out: list[dict[str, Any]] = []
    for src in info["sources"]:
        prev = state.get("sources", {}).get(src["path"]) or {}
        if not isinstance(prev, dict):
            prev = {}
        same = bool(prev) and prev.get("sha") == src["sha"] and _bank_exists(prev.get("bank_id", ""))
        if not prev:
            verdict = "new"
        elif same:
            verdict = "synced"
        else:
            verdict = "changed"
        out.append({**src, "state": verdict,
                    "bank_id": prev.get("bank_id") or "",
                    "synced_at": prev.get("synced_at") or "",
                    "count": int(prev.get("count") or 0)})
    return {"ok": True, "error": "", "error_kind": "",
            "repo": info["repo"], "url": info["url"], "fetched_at": info["fetched_at"],
            "remote_total": info["remote_total"],
            "remote_updated_at": info["remote_updated_at"],
            "sources": out, "local_only": False}


# ---------------------------------------------------------------- 题目适配
# 上游题型 → 题库域题型。`X`/`A1..A4`/`B1` 同名直通；判断/案例是上游的自定义名。
_TYPE_MAP = {"A1": "A1", "A2": "A2", "A3": "A3", "A4": "A4", "B1": "B1", "X": "X",
             "判断": "A1", "案例": "A4", "A3/A4": "A3", "B": "B1"}

# 上游 Bloom 有中英两套（`registry_meta.by_bloom` 实测：理解 2196 / comprehension 219）
_BLOOM_MAP = {"memory": "记忆", "comprehension": "理解", "understanding": "理解",
              "application": "应用", "analysis": "分析", "evaluation": "评价",
              "creation": "创造", "创造": "创造", "评价": "评价"}


def _letters(n: int) -> str:
    return "".join(chr(ord("A") + i) for i in range(n))


def _options(raw_opts: Any) -> tuple[list[str], bool]:
    """选项归一 → (文本列表, 是否"字母不规整")。

    - v2：`[{label:"A", text:"甲"}…]`，字母规整（`A,B,C…`）时只取文本（渲染层会补字母）；
    - v1：`["A. 甲", …]`，原样返回（`bank_iq.normalize_item` 会统一剥前缀）；
    - 字母**不规整**（如只给了 B/C/D 或乱序）时保留 `（B）甲` 形式：
      ⚠️ 不能只取文本——那会把「B」变成「A」，答案直接对不上号。
    """
    if isinstance(raw_opts, str):
        parts = [x.strip() for x in re.split(r"[|；;\n]+", raw_opts) if x.strip()]
        return parts, False
    if not isinstance(raw_opts, list):
        return [], False
    items = [o for o in raw_opts if o is not None]
    if items and all(isinstance(o, dict) for o in items):
        labels = [str(o.get("label") or "").strip().upper() for o in items]
        texts = [str(o.get("text") or "").strip() for o in items]
        if labels == list(_letters(len(items))):
            return [t for t in texts if t], False
        merged = [f"（{lb}）{tx}" if lb else tx for lb, tx in zip(labels, texts, strict=False)]
        return [m for m in merged if m], True
    return [str(o).strip() for o in items if str(o).strip()], False


def adapt_item(raw: dict[str, Any], *, batch: str = "", subject: str = "") -> Optional[dict[str, Any]]:
    """上游一道题 → 题库域条目（`bank.add_items` 的入参形状）。无题干 → None。

    两代结构（v1 `question_text` / v2 `stem`）与「押题卷」的第三种写法
    （`qid` / `system`）在此收敛，见模块 docstring 的对照表。
    """
    if not isinstance(raw, dict):
        return None
    stem = str(raw.get("stem") or raw.get("question_text") or raw.get("question") or "").strip()
    if not stem:
        return None
    opts, irregular = _options(raw.get("options"))
    answer = str(raw.get("answer") or raw.get("correct_answer") or "").strip().upper()
    analysis = str(raw.get("explanation") or raw.get("analysis")
                   or raw.get("legacy_explanation") or "").strip()
    qtype = str(raw.get("type") or raw.get("question_type") or "").strip()
    bloom = str(raw.get("bloom") or raw.get("bloom_level") or "").strip()
    bloom = _BLOOM_MAP.get(bloom.lower(), bloom) if bloom else ""
    topic = str(raw.get("topic") or raw.get("module_name") or raw.get("module")
                or raw.get("system") or "").strip()
    page = str(raw.get("source_page") or raw.get("source_anchor") or "").strip()
    anchor = str(raw.get("source") or "").strip()      # 押题卷的考点锚（`复习重点·一、…`）

    ref: dict[str, Any] = {"source_id": str(raw.get("id") or raw.get("qid") or "").strip(),
                           "batch": batch, "repo": REPO}
    if page:
        ref["page"] = page
    if anchor:
        ref["anchor"] = anchor

    rec: dict[str, Any] = {
        "question": stem,
        "options": opts,
        "answer": answer,
        "analysis": analysis,
        "subtopic": topic,
        "bloom": bloom,
        "type": _TYPE_MAP.get(qtype, qtype if qtype in _TYPE_MAP.values() else ""),
        "origin_ref": {k: v for k, v in ref.items() if v},
    }
    ky = raw.get("kaoyan_origin")
    if isinstance(ky, dict) and ky.get("year"):
        # `mode` 两种写法并存：`"原题"`（batch034）与 `"原题（真题原样，未改编）"`（batch042）
        # ⇒ 必须按**包含**判，等值判会把 batch042 的 100 道原题全标成「改编」。
        mode = str(ky.get("mode") or raw.get("mode") or "")
        rec["source_type"] = "考研真题·改编" if "改编" in mode and "原题" not in mode else "考研真题"
        rec["source_year"] = str(ky.get("year") or "")
    elif "真题" in subject:
        # 批次文件名自身标明是真题专项（`耳鼻咽喉头颈外科学·真题专项题库.md`）
        rec["source_type"] = "真题"
        rec["source_year"] = ""
    if irregular:
        rec["_warn"] = "选项字母不规整，已按原字母保留"
    return rec


# ---------------------------------------------------------------- 对外：执行同步
_SYNC_LOCK = threading.Lock()


def _bank_name(src: dict[str, Any]) -> str:
    """题库名 = `科目（MedAgentWork · 批次）`。

    **必须带批次**：上游同一科目常有多个批次（实测 batch040/041/042 + 根目录押题卷
    的科目都叫「耳鼻咽喉头颈外科学」），只按科目命名会在题库列表里出现四个同名项，
    用户无法分辨该练哪个、同步又写进了哪个。
    """
    subject = str(src.get("subject") or "").strip()
    batch = str(src.get("batch") or "").strip()
    tag = f"MedAgentWork · {batch}" if batch else "MedAgentWork"
    return f"{subject}（{tag}）" if subject else tag


def sync(paths: Optional[list[str]] = None, *, dedupe: str = "update", force: bool = False,
         on_progress: Optional[Callable[[int, int, str], None]] = None) -> dict[str, Any]:
    """把远端题库同步进本地（逐源独立成败，**一个源失败不拖垮其余**）。

    返回 `{"results": [...], "added"/"updated"/"skipped"/"failed"/"unchanged": n, "banks": [...]}`。
    单个源的失败信息在 `results[i]["error"]`，不抛异常——用户要看到「哪几批成功、哪批失败」，
    而不是一个「整批失败」的红条（11 个源里坏 1 个仍应入库另外 10 个）。
    """
    if not _SYNC_LOCK.acquire(blocking=False):
        raise SyncError("busy", "已有一个同步任务在进行中，请等它跑完")
    try:
        info = list_sources()
        by_path = {s["path"]: s for s in info["sources"]}
        wanted = [p for p in (paths or []) if str(p or "").strip()]
        if not wanted:
            wanted = [s["path"] for s in info["sources"]]
        if not wanted:
            raise SyncError("empty", "远端没有可同步的题库文件")

        state = load_state()
        state.setdefault("sources", {})
        results: list[dict[str, Any]] = []
        totals = {"added": 0, "updated": 0, "skipped": 0, "failed": 0, "unchanged": 0}
        banks: list[dict[str, Any]] = []
        blocked: Optional[SyncError] = None   # 限流后**不再逐份重试**（详见下方注释）

        for i, path in enumerate(wanted, 1):
            if on_progress:
                on_progress(i, len(wanted), path)
            src = by_path.get(path)
            if src is None:
                totals["failed"] += 1
                results.append({"path": path, "status": "missing",
                                "error": "远端已没有这个文件（可能已改名或删除）"})
                continue
            if blocked is not None:
                # 实测：匿名额度用尽后，剩下 8 份仍然**各打一次请求**（全 403），
                # 既拖慢同步（逐份 1s 超时）又毫无意义——额度不会在循环里恢复。
                totals["failed"] += 1
                results.append({"path": path, "batch": src.get("batch", ""),
                                "subject": src.get("subject", ""), "status": "failed",
                                "error": blocked.message})
                continue
            prev = state["sources"].get(path) or {}
            if not isinstance(prev, dict):
                prev = {}
            if (not force and prev.get("sha") == src["sha"]
                    and _bank_exists(str(prev.get("bank_id") or ""))):
                totals["unchanged"] += 1
                results.append({"path": path, "batch": src["batch"], "subject": src["subject"],
                                "status": "unchanged", "bank_id": prev.get("bank_id") or "",
                                "count": int(prev.get("count") or 0),
                                "synced_at": prev.get("synced_at") or ""})
                continue
            try:
                rows = _blob_json(src["sha"], f"{src['batch'] or '押题卷'}题库")
                if isinstance(rows, dict):
                    for k in ("questions", "items", "data"):
                        if isinstance(rows.get(k), list):
                            rows = rows[k]
                            break
                if not isinstance(rows, list):
                    raise SyncError("format", "文件不是题目数组")
                items: list[dict[str, Any]] = []
                bad = 0
                for r in rows:
                    it = adapt_item(r, batch=src["batch"], subject=src["subject"])
                    if it is None:
                        bad += 1
                        continue
                    items.append(it)
                if not items:
                    raise SyncError("format", "文件里没有可用题目（全部缺题干）")

                bid = str(prev.get("bank_id") or "")
                if not _bank_exists(bid):
                    bank = banklib.create_bank(_bank_name(src), src["subject"],
                                               source=f"medagentwork:{src['batch'] or 'paper'}")
                    bid = str(bank.get("id") or "")
                stats = banklib.add_items(bid, items, origin="medagentwork",
                                          dedupe=dedupe if dedupe in ("update", "skip", "new") else "update")
                rec = {"sha": src["sha"], "bank_id": bid, "batch": src["batch"],
                       "subject": src["subject"], "count": len(items),
                       "synced_at": _now(), "added": stats["added"],
                       "updated": stats["updated"], "skipped": stats["skipped"]}
                state["sources"][path] = rec
                save_state(state)     # 逐源落盘：中途失败不回滚已完成的部分
                totals["added"] += stats["added"]
                totals["updated"] += stats["updated"]
                totals["skipped"] += stats["skipped"]
                if bad:
                    totals["skipped"] += bad
                results.append({"path": path, "batch": src["batch"], "subject": src["subject"],
                                "status": "synced", "bank_id": bid, "count": len(items),
                                "added": stats["added"], "updated": stats["updated"],
                                "skipped": stats["skipped"] + bad,
                                "errors": list(stats.get("errors") or [])[:5]})
                banks.append({"bank_id": bid, "name": _bank_name(src),
                              "subject": src["subject"], "count": len(items)})
            except SyncError as e:
                totals["failed"] += 1
                if e.kind == "ratelimit":
                    blocked = e
                errs.record("banksync.sync", "题库同步失败", path=path, kind=e.kind, detail=e.message)
                results.append({"path": path, "batch": src.get("batch", ""),
                                "subject": src.get("subject", ""), "status": "failed",
                                "error": e.message})
            except Exception as e:  # noqa: BLE001  单源异常不阻断整批
                totals["failed"] += 1
                errs.record("banksync.sync", "题库同步异常", path=path, e=e)
                results.append({"path": path, "batch": src.get("batch", ""),
                                "subject": src.get("subject", ""), "status": "failed",
                                "error": f"{type(e).__name__}: {e}"})
        return {"results": results, "banks": banks, **totals,
                "ok": totals["failed"] == 0, "remote_total": info.get("remote_total", 0)}
    finally:
        _SYNC_LOCK.release()
