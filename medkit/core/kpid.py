"""知识点稳定 ID 对齐（错题归因流水线 EP-01 · 阶段 P2）。

问题：跨轮次追踪要求「同一考点」在三轮数据里能被认出是同一个。
现状 `mistakes` 只有 `subject/chapter/topic` 三个**自由文本**字段——用户这轮写
「心血管-心输出量」、下轮写「心排出量」、再下轮写「CO」，三个字面量互不相等，
纵向追踪直接断链。

解法（借鉴 Levenshtein/FuzzyWuzzy 生态与 Wikidata 的「canonical + alias」做法）：
1. 把 topic 归一化成 canonical 形态（去标点、去连接符、全角转半角、中英同义收缩）；
2. canonical 命中别名表 → 复用既有 kp_id；
3. 未命中 → 生成新 kp_id 并登记别名，供后续轮次复用。

设计取舍（**有意不做**自动语义聚类）：
- 不引入 embedding 做考点自动归并——归并一旦出错会**污染不可逆的纵向档案**
  （《总纲》§2.4「绝不把机构讲义整批灌进向量库」的同一理由：宁可让用户确认，
  不可让模型静默改写历史）。故本模块只做**确定性归一 + 人工可查的别名表**，
  `resolve()` 永不修改已有数据，只追加别名登记。

单源约束：`kp_id` 的生成规则一旦上线就**不可更改**（改了＝历史数据全部断链）。
本模块的 `KP_ID_VERSION` 常量随规则变更递增，变更时必须写迁移脚本。
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Optional

from . import db as dbs

# kp_id 生成规则版本。**只增不改**（改＝历史数据断链，须配迁移）。
KP_ID_VERSION = 1

_TABLE = ("kp_alias", ("subject", "chapter", "topic", "canonical", "kp_id", "created_at"))

# 中英/同义收缩表：左 = 待压缩的写法，右 = 规范写法。
# 只放**医学考试语境下确定等价**的写法；有歧义的（如"休克"vs"感染性休克"）绝不并入。
# 只保留粗粒度、跨章节也成立的缩写（如 "co" 指心输出量）；章节性同义（如"心排出量"）
# 交由别名表在**整串层面**归并，不塞进这里——否则会误伤其它章节里的同名词。
_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("co", "心输出量"),        # 循环章节里的常用缩写
    ("gi", "胃肠"),
)

# 需要从 canonical 里剔除的噪声字符（标点/空白/分隔符/全角空格）
_NOISE_RE = re.compile(r"[\s\u3000·、,，。/／\\（）()\[\]【】\-—－_:：;；'\"“”‘’]")

# 全角 → 半角（ASCII 可见区）
_FULLWIDTH_RE = re.compile(r"[\uff01-\uff5e]")


def _to_halfwidth(s: str) -> str:
    return _FULLWIDTH_RE.sub(lambda m: chr(ord(m.group(0)) - 0xFEE0), s)


def _norm_seg(raw: str) -> str:
    """单段归一：半角化 → 小写 → 剥括号注释 → 去噪声字符。

    注意：**不去词内连接符**。``"心血管-心输出量"`` 里的 ``-`` 是「章-节」分隔语义，
    保留它才能与 ``chapter="心血管", topic="心输出量"`` 区分开——两者本就不是同一粒度。
    早先版本把 ``-`` 当噪声一起删掉，导致 ``"心血管心输出量"`` 在整串层面无法与
    ``"心血管|心输出量"`` 对齐，同义表也永远命中不了（见本函数下方的单元测试）。
    """
    s = _to_halfwidth(str(raw or "")).lower()
    s = re.sub(r"[（(][^）)]*[）)]", "", s)     # 剥括号注释
    return _NOISE_RE.sub("", s)


def _canonical(subject: str, chapter: str, topic: str) -> str:
    """把 (subject, chapter, topic) 压成规范名。确定性纯函数——同输入必同输出。

    规则：逐段归一 → 段内同义收缩（**仅当该段恰好等于同义键**）→ 用 ``|`` 连接。

    段内收缩而非整串收缩：整串收缩会被 ``"|"`` 连接破坏相等性（``"a|b"`` 永远不等于
    裸键 ``"b"``），这是本模块第一次实现时踩的坑。长度守卫 ``<= 8`` 与整段相等判据共同
    保证 ``"co"`` 不会误伤 ``"co2"`` / ``"co中毒"``。
    """
    parts = []
    for raw in (subject, chapter, topic):
        seg = _norm_seg(raw)
        if not seg:
            continue
        if len(seg) <= 8:
            for alias, canon in _SYNONYMS:
                if seg == alias:
                    seg = canon
                    break
        parts.append(seg)
    return "|".join(parts)


# db 路径判定：与 errorpipe 同一策略（见该模块说明）——项目内两种 patch 写法都要兼容。
_DB_SNAPSHOT = dbs.DB_PATH


def _db_ready() -> bool:
    """库是否已建。dbs.DB_PATH 被改过（测试隔离）就以它为准，否则用导入时快照。"""
    return dbs.DB_PATH.exists() if dbs.DB_PATH != _DB_SNAPSHOT else _DB_SNAPSHOT.exists()


def _new_kp_id(subject: str, chapter: str, topic: str) -> str:
    """按 KP_ID_VERSION 生成稳定 kp_id（前缀 kp+版本，便于识别与将来迁移）。"""
    canon = _canonical(subject, chapter, topic)
    raw = f"{KP_ID_VERSION}|{canon}"
    return f"kp{KP_ID_VERSION}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def list_aliases() -> list[dict[str, Any]]:
    """别名表全量（SQL 轨优先；无库时退化为空——别名表只增，丢失可重建）。"""
    if not _db_ready():
        return []
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        return dbs.list_rows(cur, _TABLE[0])
    finally:
        cur.close()


def _find_alias(canonical: str) -> Optional[dict[str, Any]]:
    if not canonical or not _db_ready():
        return None
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        return dbs.find_row(cur, _TABLE[0], "canonical = ?", (canonical,))
    finally:
        cur.close()


def resolve(subject: str, chapter: str, topic: str,
            *, auto_register: bool = True) -> str:
    """把自由文本定位为稳定 kp_id（**写入路径**：同进程内串行，避免并发录入同一新考点时互相覆盖）。

    命中别名 → 复用；未命中 → 生成新 ID 并（默认）登记别名。

    `auto_register=False` 用于**只读探测**场景（如预览"这个考点会归到哪里"），
    不产生写副作用。这一点很重要：统计接口不应因为被调用一次就改写别名表。

    任何异常都**不向上抛**——kp_id 是纵向追踪的加分项，不该让录入主流程失败。
    失败时返回空串，调用方按「未归类」处理（下游统计会把这些行单列，不静默丢失）。
    """
    try:
        canon = _canonical(subject, chapter, topic)
        if not canon:
            return ""
        hit = _find_alias(canon)
        if hit:
            return str(hit.get("kp_id") or "")
        kp_id = _new_kp_id(subject, chapter, topic)
        if auto_register:
            with _RESOLVE_LOCK:
                register(subject, chapter, topic, canonical=canon, kp_id=kp_id)
        return kp_id
    except Exception:  # noqa: BLE001  归类失败不阻断录入
        return ""


def register(subject: str, chapter: str, topic: str,
             canonical: str = "", kp_id: str = "") -> dict[str, Any]:
    """登记一条别名（幂等：同 canonical 已存在则直接返回既有行，不覆盖 kp_id）。

    **绝不覆盖既有 kp_id**——否则用户重新导入一次错题就可能把历史数据指到新 ID 上，
    造成静默断链。要改归属必须显式调 `rebind()`。
    """
    canon = canonical or _canonical(subject, chapter, topic)
    if not canon:
        raise ValueError("canonical 为空（subject/chapter/topic 全空）")
    existing = _find_alias(canon)
    if existing:
        return existing
    dbs.migrate()   # 首次调用即建库（对齐 ADR-006 的退役方向；不再回落 JSON）
    rec = {
        "id": canon,                 # canonical 天然唯一，直接用做主键 → INSERT OR REPLACE 幂等
        "canonical": canon,
        "kp_id": kp_id or _new_kp_id(subject, chapter, topic),
        "subject": str(subject or "").strip(),
        "chapter": str(chapter or "").strip(),
        "topic": str(topic or "").strip(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with dbs.tx(write=True) as cur:
        dbs.put_row(cur, _TABLE[0], rec, _TABLE[1])
    return rec


def rebind(canonical: str, kp_id: str) -> bool:
    """把某个 canonical 改指到另一个 kp_id（合并重复归类的**唯一**显式入口）。

    与 `register` 的「不覆盖」形成互补：日常路径安全，纠错路径必须显式。
    返回是否命中并更新。
    """
    existing = _find_alias(canonical)
    if not existing:
        return False
    existing["kp_id"] = kp_id
    with dbs.tx(write=True) as cur:
        dbs.put_row(cur, _TABLE[0], existing, _TABLE[1])
    return True


def merge_into(src_kp_id: str, dst_kp_id: str) -> int:
    """把 src_kp_id 的全部别名改指到 dst_kp_id（人工纠错用）。返回改动条数。

    **不改动已落库的 mistakes.kp_id / error_events.kp_id**——历史流水是不可变的，
    改它们等于篡改档案。合并只影响今后的归类；已有数据如需重挂，由调用方显式决定。
    """
    rows = [r for r in list_aliases() if str(r.get("kp_id")) == str(src_kp_id)]
    if not rows:
        return 0
    with dbs.tx(write=True) as cur:
        for r in rows:
            r["kp_id"] = dst_kp_id
            dbs.put_row(cur, _TABLE[0], r, _TABLE[1])
    return len(rows)


# 并发保护：同进程内多线程同时录入同一新考点时，避免各自生成 ID 后互相覆盖。
# 加在 `resolve` 的登记分支内（读路径无需加锁）。
_RESOLVE_LOCK = threading.Lock()


def stats() -> dict[str, Any]:
    """别名表概览（供诊断/统计端点展示）。"""
    rows = list_aliases()
    by_kp: dict[str, int] = {}
    for r in rows:
        k = str(r.get("kp_id") or "")
        by_kp[k] = by_kp.get(k, 0) + 1
    return {
        "aliases": len(rows),
        "kp_ids": len(by_kp),
        # 一个 kp_id 挂多条别名 = 同义写法已被归并，是正常的；这个值用于观察归并效果
        "most_aliased": sorted(by_kp.items(), key=lambda kv: -kv[1])[:5],
    }
