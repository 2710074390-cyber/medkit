"""本地错题 vault —— L1 文件层（**唯一真身**）。

## 这个模块解决什么

把「错题」从数据库里搬回用户自己的文件夹：一道错题一个 markdown 文件，
front matter 管查询、正文管阅读。SQLite 在本域里降级为**可弃索引**
（见 `core/vault_index.py`），删掉它不丢任何数据。

## 三条硬约定

1. **字段可空**——subject/chapter/答案/我的理解/我的疑问全可不填，
   不填就是空串，不做校验拦截（EP-01 的录入闸门就是这么劝退用户的）。
2. **只读优先**——解析失败只记一条告警，**绝不自动改写用户文件**。
   用户用任何编辑器手改，重建索引后即时生效。
3. **写入必原子**——先写临时文件再 `os.replace`（同步盘锁冲突时安全失败）。

格式由 `FORMAT_VERSION` 标识；旧版本文件仍可读，由索引层兜底，不回写。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from . import config as _cfg

VAULT_MARKER = ".medkit-vault.json"
FORMAT_VERSION = 2

# 正文小节标题（`## xxx`）。**顺序即渲染顺序**，解析与序列化共用这一份常量——
# 两处各写一份字面量必然漂移（R9 教训）。
SECTION_QUESTION = "question"
SECTION_ANALYSIS = "analysis"
SECTION_UNDERSTANDING = "understanding"
SECTION_DOUBTS = "doubts"
SECTION_AGENT_REPLY = "agent_reply"

SECTION_TITLES: dict[str, str] = {
    SECTION_ANALYSIS: "解析",
    SECTION_UNDERSTANDING: "我的理解",
    SECTION_DOUBTS: "我的疑问",
    SECTION_AGENT_REPLY: "Agent 的答复",
}
FM_ORDER = ("id", "subject", "chapter", "topic", "tags", "status", "created",
            "source", "source_type", "source_year", "bloom", "miss_count",
            "last_tried", "due", "state", "ease", "interval", "reps", "lapses")

README_TEXT = """# MedKit 错题本

这个文件夹是你的**错题资产**——一道错题一个 `.md` 文件，front matter 管查询、正文管阅读。

## 约定

- **随便改**：用任何编辑器（VS Code / Typora / 手机 Markdown App）改完，
  MedKit 重建索引后立刻跟着变。改坏了最多那一条读不出来，不影响其它文件。
- **`我的理解` / `我的疑问` 由你写**，可空。MedKit 的 Agent 会引用它们来回答你的疑问。
- **`Agent 的答复` 由你确认后才写入**，可整段删掉——删掉不影响任何功能。
- 字段能空就空，不必凑整齐。空字段不会让任何功能失效。

## 目录

- `mistakes/年/月/*.md` —— 一道错题一个文件
- `notes/` —— 你自己的知识笔记（Agent 检索时也会读）
- `attachments/` —— 题目原图（文件里用相对路径引用）

## 同步

整个文件夹可以直接丢进坚果云 / OneDrive / Git，换机器拷走即可继续用。
"""


class VaultError(RuntimeError):
    """vault 不可用（不存在 / 不是 vault / 不可写）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _as_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ 路径解析
def default_root() -> Path:
    """默认目录 = 用户的「文档」目录（用户会同步它，这正是"资产"的隐含诉求）。"""
    home = Path.home()
    for cand in (home / "Documents", home / "文档", home):
        if cand.is_dir():
            return cand / "MedKit错题本"
    return home / "MedKit错题本"


def vault_root() -> Path:
    """当前生效的 vault 根目录：配置优先，未配置走默认。"""
    cfg = _cfg.load()
    raw = str(cfg.get("vault_dir") or "").strip()
    return Path(raw).expanduser() if raw else default_root()

def set_vault_dir(path: str) -> Path:
    """把 vault 目录写进配置（空串 = 回默认）。返回生效路径。"""
    p = str(path or "").strip()
    cfg = _cfg.load()
    cfg["vault_dir"] = p
    _cfg.save(cfg)
    return Path(p).expanduser() if p else default_root()


def is_vault(path: Path) -> bool:
    return (Path(path) / VAULT_MARKER).is_file()


def init_vault(path: Optional[str] = None) -> dict[str, Any]:
    """创建（或确认）一个 vault。返回 {root, created, mistakes, notes}。"""
    raw = str(path or "").strip()
    root = Path(raw).expanduser() if raw else vault_root()
    if root.exists() and not root.is_dir():
        raise VaultError(f"路径已存在且不是目录：{root}")
    created = not root.exists()
    (root / "mistakes").mkdir(parents=True, exist_ok=True)
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "attachments").mkdir(parents=True, exist_ok=True)
    marker = root / VAULT_MARKER
    if not marker.is_file():
        _write_atomic(marker, yaml.safe_dump(
            {"format_version": FORMAT_VERSION, "app": "MedKit", "created": _utcnow()},
            allow_unicode=True, sort_keys=False))
    readme = root / "README.md"
    if not readme.is_file():
        _write_atomic(readme, README_TEXT)
    return {"root": str(root), "created": created,
            "mistakes": str(root / "mistakes"), "notes": str(root / "notes")}


# ------------------------------------------------------------------ 原子写 / 寻址
def _write_atomic(path: Path, text: str) -> None:
    """先写同目录临时文件再 `os.replace`——同步盘锁冲突时安全失败，不留半个文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".mk-tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def new_id() -> str:
    """生成一个错题 id（`mk-<yyyyMMdd>-<4hex>`）——仅用于**尚无 id 的新建错题**。"""
    return f"mk-{datetime.now().strftime('%Y%m%d')}-{os.urandom(2).hex()}"


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_record(rec: dict[str, Any], root: Optional[Path] = None) -> Path:
    """把一条记录写进它的文件（原子）。返回写入路径。"""
    path = mistake_path(rec, root)
    _write_atomic(path, serialize(rec))
    return path


# ------------------------------------------------------------------ 序列化
def _slug(text: Any, limit: int = 24) -> str:
    """题干 → 文件名安全片段（中文保留，只剥路径/Shell 危险字符）。"""
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "", str(text or "")).strip()
    s = re.sub(r"\s+", "-", s)
    return (s[:limit] or "mistake").strip("-")


def mistake_path(rec: dict[str, Any], root: Optional[Path] = None) -> Path:
    """一条错题的文件路径（按 created 的年/月归档，避免单目录过万文件）。"""
    created = str(rec.get("created_at") or rec.get("created") or "")
    m = re.match(r"(\d{4})-(\d{2})", created)
    y, mo = (m.group(1), m.group(2)) if m else ("0000", "00")
    mid = str(rec.get("id") or "")
    day = created[:10] or "undated"
    name = f"{day}-{mid}-{_slug(rec.get('question'))}.md"
    base = Path(root).expanduser() if root else vault_root()
    return base / "mistakes" / y / mo / name


def serialize(rec: dict[str, Any]) -> str:
    """错题记录 → markdown 文本。**所有文本字段可空**，空则不写那一行/那一段。"""
    raw: dict[str, Any] = {
        "id": str(rec.get("id") or ""),
        "subject": str(rec.get("subject") or ""),
        "chapter": str(rec.get("chapter") or ""),
        "topic": str(rec.get("topic") or ""),
    }
    tags = [str(t).strip() for t in (rec.get("know_tags") or rec.get("tags") or []) if str(t).strip()]
    if tags:
        raw["tags"] = tags
    status = str(rec.get("status") or "").strip()
    if not status:
        status = "learned" if rec.get("learned") else "unlearned"
    raw["status"] = status
    if rec.get("created_at") or rec.get("created"):
        raw["created"] = str(rec.get("created_at") or rec.get("created"))
    # 反复错次数：>1 才写（免得每个文件都多一行噪声）。**不进这一条，薄弱项的
    # 「错题密度」会恒为 1**——旧实现漏写，迁移后强度信息静默丢失（实测缺陷）。
    try:
        _mc = int(rec.get("miss_count") or 0)
    except (TypeError, ValueError):
        _mc = 0
    if _mc > 1:
        raw["miss_count"] = _mc
    # 复习排程字段（SM-2，v2 新增）：`last_tried` 让薄弱项的「最近一次」= 上次复习时间而非入库时间；
    # `due/state/ease/interval/reps/lapses` 是「错题→复习→掌握」闭环的状态，评分时写回。
    for _k in ("last_tried", "due", "state"):
        if rec.get(_k):
            raw[_k] = str(rec.get(_k))
    for _k in ("reps", "interval", "lapses"):
        _iv = _as_int(rec.get(_k))
        if _iv:
            raw[_k] = _iv
    _ev = _as_float(rec.get("ease"))
    if _ev:
        raw["ease"] = _ev
    for k in ("source", "source_type", "source_year", "bloom"):
        if rec.get(k):
            raw[k] = str(rec.get(k))
    fm = {k: raw[k] for k in FM_ORDER if k in raw and raw[k] not in ("", None, [])}

    body: list[str] = []
    q = str(rec.get("question") or "").strip()
    if q:
        body.append(f"# {q}")
    opts = rec.get("options") or []
    if opts:
        body.append("")
        body.extend(f"- {str(o).strip()}" for o in opts if str(o).strip())
    ans = str(rec.get("answer") or "").strip()
    if ans:
        body.append("")
        body.append(f"**答案**：{ans}")
    for key, value in ((SECTION_ANALYSIS, rec.get("analysis")),
                       (SECTION_UNDERSTANDING, rec.get("my_reasoning") or rec.get("understanding")),
                       (SECTION_DOUBTS, rec.get("doubts")),
                       (SECTION_AGENT_REPLY, rec.get("agent_reply"))):
        text = str(value or "").strip()
        if text:
            body.append("")
            body.append(f"## {SECTION_TITLES[key]}")
            body.append("")
            body.append(text)

    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=100).rstrip("\n")
    return f"---\n{head}\n---\n\n" + "\n".join(body).strip("\n") + "\n"


# ------------------------------------------------------------------ 解析
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
_SEC_RE = re.compile(r"^##\s+(.*?)\s*$", re.M)


def parse(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """markdown → (front matter, 各小节正文)。

    **容错优先**：front matter 解析失败 → 返回空 dict + 一条 `_warn`
    （调用方记告警，不抛错、不改文件）；正文没有对应小节 → 空串。
    """
    fm: dict[str, Any] = {}
    text = text or ""
    m = _FM_RE.match(text)
    rest = text
    if m:
        try:
            loaded = yaml.safe_load(m.group(1))
            if isinstance(loaded, dict):
                fm = loaded
        except yaml.YAMLError as e:
            fm = {"_warn": f"front matter 解析失败：{e}"}
        rest = text[m.end():]

    sections: dict[str, str] = {}
    marks = list(_SEC_RE.finditer(rest))
    for i, sm in enumerate(marks):
        title = sm.group(1).strip()
        key = next((k for k, v in SECTION_TITLES.items() if v == title), None)
        if key is None:
            continue      # 用户自己加的小节：原样保留，但不进结构化字段
        end = marks[i + 1].start() if i + 1 < len(marks) else len(rest)
        sections[key] = rest[sm.end():end].strip("\n").strip()

    sections[SECTION_QUESTION] = (rest[: marks[0].start()] if marks else rest).strip("\n")
    return fm, sections


def to_record(fm: dict[str, Any], sec: dict[str, str]) -> dict[str, Any]:
    """front matter + 小节 → 统一记录形状（与 `library` 的错题同构，便于复用统计）。"""
    q = sec.get(SECTION_QUESTION, "")
    title, options, answer = "", [], ""
    for line in q.splitlines():
        s = line.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
            continue
        if s.startswith("- "):
            options.append(s[2:].strip())
    mm = re.search(r"\*\*答案\*\*：\s*(.+)$", q, re.M)
    if mm:
        answer = mm.group(1).strip()
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "id": str(fm.get("id") or ""),
        "subject": str(fm.get("subject") or ""),
        "chapter": str(fm.get("chapter") or ""),
        "topic": str(fm.get("topic") or ""),
        "know_tags": [str(t) for t in tags],
        "status": str(fm.get("status") or "unlearned"),
        "created": str(fm.get("created") or ""),
        "source": str(fm.get("source") or ""),
        "source_type": str(fm.get("source_type") or ""),
        "source_year": str(fm.get("source_year") or ""),
        "bloom": str(fm.get("bloom") or ""),
        "miss_count": fm.get("miss_count") or 1,
        "last_tried": str(fm.get("last_tried") or ""),
        "due": str(fm.get("due") or ""),
        "state": str(fm.get("state") or ""),
        "ease": _as_float(fm.get("ease")),
        "interval": _as_int(fm.get("interval")),
        "reps": _as_int(fm.get("reps")),
        "lapses": _as_int(fm.get("lapses")),
        "question": title,
        "options": options,
        "answer": answer,
        "analysis": sec.get(SECTION_ANALYSIS, ""),
        "my_reasoning": sec.get(SECTION_UNDERSTANDING, ""),
        "doubts": sec.get(SECTION_DOUBTS, ""),
        "agent_reply": sec.get(SECTION_AGENT_REPLY, ""),
        "_warn": str(fm.get("_warn") or ""),
    }


def read_record(path: Path) -> dict[str, Any]:
    fm, sec = parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    rec = to_record(fm, sec)
    rec["path"] = str(Path(path))
    return rec


def iter_mistake_files(root: Optional[Path] = None) -> Iterable[Path]:
    """遍历 vault 内全部错题 md（`mistakes/**`）。只读，无副作用。"""
    base = Path(root) if root else vault_root()
    mdir = base / "mistakes"
    if not mdir.is_dir():
        return
    for p in sorted(mdir.rglob("*.md")):
        if p.is_file():
            yield p
