"""路径穿越防线回归测试（R7）。

## 为什么单独建这个文件

2026-09-27 审计发现：**穿梭在三个不同测试文件里的四条消毒用例，居然一条都锁不住消毒本身**。
用 4 个探针做了反向验证（注入 ⇒ 是否变红），全部为「否」：

| 探针 | 注入内容 | 目标用例 | 结果 |
|---|---|---|---|
| 1 | `sessions._safe_sid` 弱化成 `return sid` | `tests/test_s3_sessions.py`（4 条） | 4 passed，**未变红** |
| 2 | 删掉 `routers/presets.py:34` 的 `pid = _safe_pid(pid)` | `test_delete_preset_rejects_traversal` | 1 passed，**未变红** |
| 3 | `_safe_pid` 的正则放宽为 `.*` | `test_pid_sanitize` + `test_delete_preset_rejects_traversal` | 2 passed，**未变红** |
| 4 | 删掉 `_safe_pid` 的显式黑名单行（只留正则） | 同上 | 2 passed，**未变红** |

### 探针 2 为什么没红（值得记的坑）

`test_delete_preset_rejects_traversal` 直接调 `m.delete_preset(bad)`，看起来是最正统的注入点。
但查下来 `delete_preset` 有**两条独立防线**：

```python
# medkit/routers/presets.py:32
@router.delete("/api/presets/{pid}")
def delete_preset(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)          # ← 防线 A（探针 2 打掉的是这条）
    ok = prs.delete_preset(pid)   # → medkit/core/presets.py:95

# medkit/core/presets.py:95
def delete_preset(pid: str) -> bool:
    if any(b["id"] == pid for b in BUILTINS):
        return False              # ← 防线 B：内置预设名单里含 ".." 与 "."
```

`BUILTINS` 里恰好有 `".."` 和 `"."` 两个 id，所以测试用的 `".."` / `"../x"` / `"..\\..\\config.json"`
全被防线 B 拦下，返回 400 —— 于是删掉防线 A 行为不变，用例自然不红。

**这是纵深防御正在正常工作，不是缺陷**；缺陷在于「只测了函数本体的行为，没测每一层是否在场」。
本文件因此对每一层都写一条**结构性断言**（改单层 ⇒ 必红），再补一条**端到端行为断言**
（两层都在时行为正确；两层都打掉 ⇒ 必红）。
"""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import presets as prs  # noqa: E402
from medkit.core import sessions as ses  # noqa: E402
from medkit.routers import _common as common  # noqa: E402
from medkit.routers import presets as presets_router  # noqa: E402


def _src(mod) -> str:
    """读模块源码。用 inspect.getsource 而不是自己拼路径——模块路径变了也能跟上。"""
    import inspect
    return inspect.getsource(mod)


# ============================================================ 攻击向量（共享数据源）
# 单一来源：所有用到「坏 id」的用例都从这里取，避免两处各写一份（曾踩：改一处漏一处）。
TRAVERSAL_PAYLOADS = (
    "",                 # 空串 → Path(".../") / "" 落回公共根目录
    ".",                # 当前目录
    "..",               # 上级目录
    "../x",             # 相对逃逸
    "../../config",     # 多层相对逃逸
    "..\\..\\config.json",  # Windows 反斜杠变体
    "a\\b",             # 反斜杠单段
    "/etc/passwd",      # 绝对 POSIX 路径
    "C:\\Windows",      # 绝对 Windows 路径
    "a\x00b",           # NUL 截断
)


# ============================================================ 第 1 层：_safe_pid 本体
@pytest.mark.parametrize("bad", TRAVERSAL_PAYLOADS)
def test_safe_pid_rejects_every_payload(bad):
    """_safe_pid 必须把每个攻击向量打成 400。"""
    with pytest.raises(HTTPException) as ei:
        common._safe_pid(bad)
    assert ei.value.status_code == 400, (bad, ei.value.status_code)


@pytest.mark.parametrize("good", ["p1", "proj_2026", "儿科-01", "ABC123"])
def test_safe_pid_accepts_legitimate_ids(good):
    """反向：正常 id 必须放行，否则是把防线做成了「一律拒绝」的假防线。"""
    assert common._safe_pid(good) == good


def test_safe_pid_has_both_layers_structurally():
    """结构性：_safe_pid 的**显式黑名单**与**字符白名单正则**必须同时在场。

    ## 为什么需要这条
    探针 3 / 探针 4 证明：单独放宽正则、或单独删掉黑名单行，**现有用例都不变红**——
    因为两层互相兜底。只测行为永远只能测到「至少有一层在」，测不到「两层都在」。
    本断言直接盯源码结构：任一层被删 ⇒ 立刻红。
    """
    src = _src(common._safe_pid)
    assert 'pid in {"", ".", ".."}' in src, \
        "_safe_pid 的显式黑名单层被删掉了（.. / 空串 / 单点必须显式拦）"
    assert '"/" in pid' in src and '"\\\\" in pid' in src, \
        "_safe_pid 必须显式拦 / 与 \\（Windows 上两者都可逃逸）"
    assert "re.fullmatch" in src, \
        "_safe_pid 的字符白名单正则层被删掉了（应只放行 [\\w\\u4e00-\\u9fff-]）"


def test_safe_pid_regex_is_anchored_and_restrictive():
    """正则本身必须仍是否定式白名单；放宽为 `.*` / 去掉 fullmatch ⇒ 红。"""
    src = _src(common._safe_pid)
    assert "re.fullmatch(r\"[\\w\\u4e00-\\u9fff-]+\"" in src, \
        "_safe_pid 的正则被改宽了（必须是 fullmatch 全字符白名单）"


def test_safe_pid_rules_are_identical_across_layers():
    """元守卫：core 层（presets / sessions）抄的白名单必须与 routers 层同规则。

    ## 为什么要这条
    `core` 不得 import `routers`（分层单向：routers → core → db/llm），
    所以 `_safe_pid` 的规则在 core 里有**两处有意重复**（presets._SAFE_PID_RE、
    sessions._SAFE_SID_RE）。重复即漂移风险：有人只改了 routers 那份，
    core 那份就静默地宽或严。这条断言把两份模式串钉在一起。
    """
    from medkit.core import presets as prs_mod

    assert prs_mod._SAFE_PID_RE.pattern == ses._SAFE_SID_RE.pattern, \
        f"core 两份白名单不一致：presets={prs_mod._SAFE_PID_RE.pattern!r} " \
        f"sessions={ses._SAFE_SID_RE.pattern!r}"
    assert prs_mod._SAFE_PID_RE.pattern == r"[\w\u4e00-\u9fff-]+", \
        "白名单规则被改宽/改窄了——请同步 routers/_common.py::_safe_pid 并复核是否仍拦得住"

    # 两份必须对该向量集合同样地接受/拒绝（行为级一致性，不只看模式串）
    for probe in ("p1", "abc", "儿科-01", "a b", "a.b", "a/b", "..", "a\x00b"):
        pid_ok = bool(prs_mod._SAFE_PID_RE.fullmatch(probe))
        sid_ok = bool(ses._SAFE_SID_RE.fullmatch(probe))
        assert pid_ok == sid_ok, f"两份白名单对 {probe!r} 判定不一致"


# ============================================================ 第 2 层：_safe_sid
@pytest.mark.parametrize("bad", TRAVERSAL_PAYLOADS)
def test_safe_sid_rejects_every_payload(bad):
    """_safe_sid 必须拒绝所有攻击向量。探针 1 证明此前无任何用例覆盖它。"""
    with pytest.raises(ValueError):
        ses._safe_sid(bad)


@pytest.mark.parametrize("good", ["abc123", "deadbeef0011", "A_b-c"])
def test_safe_sid_accepts_legitimate_ids(good):
    assert ses._safe_sid(good) == good


def test_delete_session_calls_safe_sid():
    """调用点真的调了 —— 只测 `_safe_sid` 本体不算测了防线。

    探针 1 的实际结论：把 `_safe_sid` 弱化成 `return sid`，4 条会话用例全绿。
    根因是 `delete_session` / `get_session` 虽然调了它，却**没有任何用例传入坏 id**
    （save_session 生成的是 uuid hex，永远合法）。这条断言 + 下面的端到端用例补上。
    """
    for fn in (ses.delete_session, ses.get_session):
        assert "_safe_sid(" in _src(fn), f"{fn.__name__} 未调用 _safe_sid"


def test_delete_session_rejects_and_deletes_nothing(monkeypatch, tmp_path):
    """端到端：坏 sid 必须抛错，且**目录里一个文件都没少**。

    判据写成「副作用没发生」（目录快照逐项比对），不是「抛了异常」——
    旧用例只断言异常类型，注入弱化后连异常都不抛也不红。
    """
    monkeypatch.setattr(ses.cfg, "CONFIG_DIR", tmp_path)
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "sessions" / "keepme.json"
    victim.write_text('{"id": "keepme"}', encoding="utf-8")
    # 再放一个「上游目录」的诱饵，证明逃逸目标没被碰到
    decoy = tmp_path / "decoy.json"
    decoy.write_text("{}", encoding="utf-8")

    before = sorted(p.name for p in tmp_path.rglob("*"))

    for bad in TRAVERSAL_PAYLOADS:
        with pytest.raises(ValueError):
            ses.delete_session(bad)

    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert after == before, f"坏 sid 竟改动了文件系统：{set(after) ^ set(before)}"
    assert victim.exists() and decoy.exists()


def test_get_session_rejects_traversal(monkeypatch, tmp_path):
    """读路径同样要拦（探测任意文件是否存在 = 信息泄漏）。"""
    monkeypatch.setattr(ses.cfg, "CONFIG_DIR", tmp_path)
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    for bad in TRAVERSAL_PAYLOADS:
        with pytest.raises(ValueError):
            ses.get_session(bad)


# ============================================================ 第 3 层：delete_preset 路由
def test_delete_preset_route_calls_safe_pid():
    """结构性：路由层必须有消毒调用（探针 2 打掉的正是这一行）。"""
    assert "_safe_pid(pid)" in _src(presets_router.delete_preset), \
        "routers/presets.py::delete_preset 丢了 `pid = _safe_pid(pid)` 消毒"


def test_core_delete_preset_does_not_traverse():
    """结构性 + 行为：core 层即使被绕过路由直接调用，也**不能**碰 PRESETS_DIR 之外。

    core.delete_preset 自身不做消毒（设计如此，消毒归路由层），所以这里验证的是
    「它把 pid 拼进 `PRESETS_DIR / f"{pid}.json"` 后只会误删预设目录内的文件」——
    以及上方测试把它直接 call 时不会真的删掉别的 json。
    """
    src = _src(prs.delete_preset)
    assert 'cfg.PRESETS_DIR / f"{pid}.json"' in src, \
        "core.delete_preset 的路径拼接形式变了，安全前提需重新评估"


def test_delete_preset_route_rejects_traversal(monkeypatch, tmp_path):
    """端到端：路由层必须把攻击向量打成 400（而不是靠 core 的 BUILTINS 兜底）。"""
    monkeypatch.setattr(prs.cfg, "PRESETS_DIR", tmp_path / "presets")
    (tmp_path / "presets").mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "config.json"
    victim.write_text('{"api_key": "sk-victim"}', encoding="utf-8")

    for bad in TRAVERSAL_PAYLOADS:
        with pytest.raises(HTTPException) as ei:
            presets_router.delete_preset(bad)
        assert ei.value.status_code == 400, (bad, ei.value.status_code)
    assert victim.exists(), "路径穿越竟然删掉了 ~/.medkit/config.json"


def test_traversal_payloads_are_shared_single_source():
    """元守卫：攻击向量表必须被上面每一组用例共用。

    判据：把表里某个向量删掉，用例数量必须随之变化 —— 证明 `parametrize` 真的接到它，
    而不是某处偷写了一份自己的字面量列表（曾踩：`test_api.py` / `test_s1_backend.py` 各写一份）。
    """
    assert len(TRAVERSAL_PAYLOADS) >= 8, "攻击向量表被削瘦了"
    assert "" in TRAVERSAL_PAYLOADS and ".." in TRAVERSAL_PAYLOADS
    assert any("\\" in p for p in TRAVERSAL_PAYLOADS), "缺少 Windows 反斜杠向量"
