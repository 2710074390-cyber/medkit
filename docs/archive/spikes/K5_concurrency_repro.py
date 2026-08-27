# -*- coding: utf-8 -*-
"""K5 SPIKE：双窗口并发写复现（JSON 丢失更新 → SQLite 事务消失）。

按《结构化执行方案》§2.2 K5：并发 grade + record_quiz 各 50 次，
验证 JSON 读-改-写整文件互相覆盖（丢失更新），SQLite(WAL+事务) 则无此问题。

演示（均在真实 medkit.core.library 上运行）：
  A. 确定性互撞：两个写入者基于同一旧快照各写一次 → 必丢 1 次更新；
  B. 洪泛·record_quiz：两线程对同一已存在 kp 各记 50 次 → attempts 应 100；
  C. 洪泛·add_mistake：两线程各入 50 条错题 → mistakes 应 100；
  D. SQLite：同强度原子自增 → 100 不丢（对照）。

用法：python docs/spikes/K5_concurrency_repro.py（仓库根目录运行）。
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path

import medkit.core.library as lib_mod

TMP = Path(tempfile.mkdtemp(prefix="medkit_k5_"))
LIB = TMP / "library"
LIB.mkdir()
DB = TMP / "t.db"
KP = "kp_k5"

SEED = dict(question="K5 并发测试：患儿男 3 岁发热？", options=["A．是", "B．否"],
            answer="A．是", subject="儿科", chapter="呼吸系统", topic="肺炎",
            know_tags=[KP])


def point_at_tmp() -> None:
    lib_mod.LIBRARY_DIR = LIB
    lib_mod.MISTAKES_FILE = LIB / "mistakes.json"
    lib_mod.KNOWLEDGE_FILE = LIB / "knowledge.json"
    # 覆盖为干净起点
    _write_json(LIB / "mistakes.json", [])
    _write_json(LIB / "knowledge.json", [])


def _write_json(path: Path, data) -> None:
    import json
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def attempts_now() -> int:
    for k in lib_mod.list_knowledge():
        if k.get("name") == KP:
            return int(k.get("attempts", 0))
    return 0


def demo_a_deterministic() -> int:
    """读-改-写互撞：两窗口都基于旧快照，各自 record_quiz → 必有一方覆盖。"""
    point_at_tmp()
    lib_mod.add_mistake(dict(SEED))  # kp_k5 存在，attempts=1
    snap = []

    barrier = threading.Barrier(2)

    def writer(tag: int) -> None:
        lib_mod.list_knowledge()      # ① 读（与对方同窗）
        barrier.wait()                # ② 同步：两边都拿到旧快照
        lib_mod.record_quiz(KP, 1)    # ③ 基于旧快照写
        snap.append(tag)

    ts = [threading.Thread(target=writer, args=(t,)) for t in (0, 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    final = attempts_now()
    return 3 - final                  # 期望 1(seed)+2 = 3


def demo_b_flood_quiz() -> tuple[int, int]:
    """同 kp 并发各 50 次；期望 attempts = 100（seed 后清零再洪泛）。"""
    point_at_tmp()
    lib_mod.add_mistake(dict(SEED))
    lib_mod.record_quiz(KP, 1)        # attempts=1
    barrier = threading.Barrier(2)
    errs: list[str] = []

    def flood(tag: int) -> None:
        barrier.wait()
        for _ in range(50):
            try:
                lib_mod.record_quiz(KP, 1)
            except Exception as e:  # noqa: BLE001
                errs.append(type(e).__name__)

    ts = [threading.Thread(target=flood, args=(t,)) for t in (0, 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    return 101 - attempts_now(), len(errs)


def demo_c_flood_mistakes() -> tuple[int, int]:
    """并发 add_mistake 各 50 条（不同 id）；期望 100 条。"""
    point_at_tmp()
    barrier = threading.Barrier(2)
    errs: list[str] = []

    def flood(tag: int) -> None:
        barrier.wait()
        for j in range(50):
            try:
                lib_mod.add_mistake(dict(SEED, id=f"m_{tag}_{j}",
                                         question=f"Q{tag}_{j}：测试题？"))
            except Exception as e:  # noqa: BLE001
                errs.append(type(e).__name__)

    ts = [threading.Thread(target=flood, args=(t,)) for t in (0, 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    return 100 - len(lib_mod.list_mistakes()), len(errs)


def demo_d_sqlite() -> tuple[int, str]:
    """WAL + 两连接事务内原子自增 ×100 → 不丢。"""
    conn = sqlite3.connect(str(DB), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("create table if not exists k(name text primary key, ev int not null)")
    conn.commit()
    conn.close()

    barrier = threading.Barrier(2)
    errs: list[str] = []

    def increment(tag: int) -> None:
        barrier.wait()
        c = sqlite3.connect(str(DB), timeout=30)
        try:
            c.execute("insert or ignore into k values ('kp_seed', 0)")
            c.commit()
            for _ in range(50):
                with c:
                    c.execute("update k set ev = ev + 1 where name = 'kp_seed'")
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)
        finally:
            c.close()

    ts = [threading.Thread(target=increment, args=(t,)) for t in (0, 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    c = sqlite3.connect(str(DB), timeout=30)
    ev = c.execute("select ev from k where name='kp_seed'").fetchone()[0]
    c.close()
    return ev - 100, "+".join(sorted(set(errs))) or "无异常"


if __name__ == "__main__":
    print("=" * 64)
    miss_a = demo_a_deterministic()
    print(f"[A 确定性互撞] 期望 attempts 3 → 丢失 {miss_a} 次")
    miss_b, errs_b = demo_b_flood_quiz()
    print(f"[B record_quiz×100] 期望 attempts 101 → 丢失 {miss_b} 次（异常 {errs_b}）")
    miss_c, errs_c = demo_c_flood_mistakes()
    print(f"[C add_mistake×100] 期望 100 条 → 丢失 {miss_c} 条（异常 {errs_c}）")
    diff_d, errs_d = demo_d_sqlite()
    print(f"[D SQLite] 100 次原子自增 → 偏差 {diff_d}（{errs_d}）")
    print("=" * 64)
    ok = miss_a > 0 and miss_b > 0 and miss_c > 0 and diff_d == 0
    print(f"K5 判定: {'PASS —— JSON 复现丢失更新，SQLite 事务下无丢失' if ok else '需复查'}")
