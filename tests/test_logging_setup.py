"""U-24（R6-18）：日志脱敏 RedactingFilter 独立单测。

防线：Logger 一旦把含 Key 的异常串写日志，Key 即随 ~/.medkit/logs 落盘/上屏。
本测试验证 filter 在格式化前掩码 `sk-***` 与 `authorization/api_key: ***`，
并确认无害文本原样通过（不误伤）。
"""

import logging
import os
from pathlib import Path

from medkit.logging_setup import RedactingFilter


def _capture(record: logging.LogRecord) -> str:
    """模拟 handler 格式化：先过 filter，再跑 Formatter。"""
    assert RedactingFilter().filter(record) is True
    return logging.Formatter("%(message)s").format(record)


def test_redacts_sk_key():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "LLM 调用失败：bad request sk-abc123XYZ09", None, None)
    assert "sk-***" in _capture(rec)
    assert "sk-abc123XYZ09" not in _capture(rec)


def test_redacts_authorization_value():
    """Authorization 头必须整体掩码（含 `Bearer ` 之后的值）。

    W8（2026-10-02）：原断言写死 `== "请求头 Authorization: Bearer ***"`，**绑定了旧实现
    的书写格式**——旧正则只吃 `Bearer` 一个词，把真 token 漏在后面（`Bearer *** tok-secret`）。
    这正属「文本子串断言绑书写格式」：格式一改就假红，且**漏掩码时未必红**（只要掩码串对得上）。
    现改为行为断言：**真值必须消失**；掩码细节不再钉死。
    """
    out = _capture(logging.LogRecord(
        "t", logging.INFO, __file__, 1, "请求头 Authorization: Bearer tok-secret-123", None, None))
    assert "tok-secret-123" not in out, f"Bearer 后的 token 未被掩码：{out!r}"
    assert "Authorization" in out          # 字段名保留（便于排障）
    assert "***" in out


def test_redacts_api_key_equals():
    """`api_key=` / `api_key:` 两种分隔符都要掩码，且不得漏掉值。"""
    out = _capture(logging.LogRecord(
        "t", logging.INFO, __file__, 1, "config api_key=sk-zzz999888", None, None))
    assert "sk-zzz999888" not in out, f"api_key 的值未被掩码：{out!r}"
    assert "***" in out


def test_keeps_plain_text():
    out = _capture(logging.LogRecord(
        "t", logging.INFO, __file__, 1, "正常日志：保存题库 42 道", None, None))
    assert out == "正常日志：保存题库 42 道"


def test_redacts_after_variable_interpolation():
    # %-style args 在 getMessage() 展开后再掩码，值内 Key 也要被盖住
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "上游错误：%s 携带 %s", ("LLM 调用", "sk-0x0x0x111222"), None)
    out = _capture(rec)
    assert "sk-***" in out
    assert "sk-0x0x0x111222" not in out


# ---------------------------------------------------------------- R5-01 日志隔离
# 2026-09-27：本组守卫的**设计要点**（踩过坑，别改回去）——
#   ① 最初我写成「在当前进程里调 setup_logging() 然后看 handler 路径」，注入缺陷后**照样绿**：
#      因为 `setup_logging()` 的幂等判据是「根 logger 上是否已有 `_medkit` handler」（进程级全局），
#      同进程里前面任何用例装过一次之后，我这次调用**根本不会再装 handler**，断言自然通过
#      —— 这是「只测函数本身 ≠ 测了防线」的又一例。
#      正确做法：把「app 进 lifespan」这个真实场景放到**干净的独立子进程**里跑。
#   ② 但子进程「干净」得彻底 —— 第一版只钉了 `MEDKIT_LOG_DIR`，结果子进程里
#      `with TestClient(app)` 会跑 `main._lifespan` 的 `dbs.migrate()`，
#      把**用户真实 `~/.medkit/library/medkit.db` 从 v7 迁到 v8**（还留了个 .bak）。
#      pytest 的 fixture 隔离（monkeypatch）**不会传进子进程**，故必须在子进程 env 里
#      把家目录整体指向 tmp：Windows 下 `expanduser("~")` 读 `USERPROFILE`。
#  ⇒ 子进程 env 必须同时给：`MEDKIT_LOG_DIR`（覆盖日志）+ `USERPROFILE`/`HOME`（覆盖数据目录）。
_PROBE_ISOLATION_DOC = "子进程 env 必须同时隔离 MEDKIT_LOG_DIR 与 USERPROFILE/HOME"

_LOG_PROBE = r"""
import json, logging, os, re
from pathlib import Path
from fastapi.testclient import TestClient
from medkit.main import app

with TestClient(app):          # 进出 lifespan → 触发 main._lifespan 的 setup_logging()
    pass

fhs = [h.baseFilename for h in logging.getLogger().handlers
       if hasattr(h, "baseFilename")]
# 真实家目录 = 由 USERPROFILE 推导（Windows expanduser 读它）
real = os.path.join(os.path.expanduser("~"), ".medkit")
norm = lambda s: str(s).replace("\\", "/").lower()
print("__PROBE__" + json.dumps({
    "handlers": fhs,
    "home": os.path.expanduser("~"),
    "real_prefix": real,
    "leaked": [f for f in fhs if norm(real) in norm(f)],
}))
"""


def _probe_env(home, *extra, unset=(), **overrides):
    """构造子进程 env：把「家」整体指到 `home`，并允许显式 overrides/unset。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["USERPROFILE"] = str(home)      # Windows：expanduser("~") 读这个
    env["HOME"] = str(home)             # 兼容 POSIX
    for k in unset:
        env.pop(k, None)
    for k, v in overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    for k, v in extra:
        env[k] = str(v)
    return env


def _run_log_probe(env) -> dict:
    """跑一次「app 进 lifespan」子进程探针，回报根 logger 的 handler 落点。"""
    import json
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "-c", _LOG_PROBE],
                          capture_output=True, text=True, env=env, timeout=180)
    for line in (proc.stdout or "").splitlines():
        if line.startswith("__PROBE__"):
            return json.loads(line[len("__PROBE__"):])
    raise AssertionError(
        f"子进程探针未产出结果（stdout={proc.stdout[-500:]!r} stderr={proc.stderr[-800:]!r})")


def _real_user_medkit_prefix() -> str:
    """本机**真实** `~/.medkit` 前缀（归一为 `/` 小写）—— 泄漏判据的唯一来源。

    独立成函数是为了让「这个前缀真的能从 handler 路径里认出来」可被单独证伪
    （见 `test_real_user_prefix_actually_matches_a_known_path`）。
    若某次改动把这个前缀拼错（例如漏了 `.medkit`、大小写/斜杠没归一），
    `truly_leaked` 会恒为空 ⇒ 守卫静默失效。这是 2026-09-30 R27 实测的缺口。
    """
    return str(Path(os.path.expanduser("~")) / ".medkit").replace("\\", "/").lower()


def test_real_user_prefix_actually_matches_a_known_path():
    """元守卫：真实家目录前缀必须**真的能**匹配一条已知落在该目录下的路径。

    ## 为什么（2026-09-30 R27 实测）

    `test_lifespan_does_not_bind_real_home_log` 的判据是
    `real_user in f.lower()`，其中 `real_user` 由 `_real_user_medkit_prefix()` 算出。
    实测：把该前缀换成 `"不可能出现的前缀____"` 后，**整个文件 9 条用例仍全绿**
    ——因为正常环境下本来就没有泄漏，判据恒真（空集恒真）。
    原来兜底的 `assert info["handlers"]` 只保证探针**产出了** handler，
    保证不了**判据本身还有效**。

    ## 判据必须**独立于被测函数**（第一版我写错了）

    第一版写成 `assert prefix in (prefix + "/logs/x.log")` —— 这是**自证式假绿**：
    任何非空前缀都能匹配它自己拼出来的路径。实测对"前缀写坏成
    `"不可能出现的前缀____"`"**照样绿**（`rc=0`）。
    ⇒ 必须用**独立推导**的路径：直接用 `expanduser("~")` 与 `.medkit` 重新拼一遍，
       且断言 `_real_user_medkit_prefix()` 与之相等（**等值**，不是包含）。
    """
    prefix = _real_user_medkit_prefix()
    assert prefix, "真实家目录前缀为空——泄漏判据会恒真"

    # 独立推导（不经过被测函数）：真实家目录 + .medkit，归一为 / 小写
    independent = (
        str(Path(os.path.expanduser("~")) / ".medkit").replace("\\", "/").lower()
    )
    assert prefix == independent, (
        "泄漏判据的前缀与本机真实 `~/.medkit` 不一致：\n"
        f"  判据用   = {prefix!r}\n"
        f"  独立推导 = {independent!r}\n"
        "⇒ 守卫可能永远是绿的（前缀写坏后 `truly_leaked` 恒空）。"
    )

    # 再证「判据能认出真泄漏」：把一个确实位于该目录下的路径喂进去，必须命中
    leaked_like = (Path(os.path.expanduser("~")) / ".medkit" / "logs" / "medkit.log")
    norm = str(leaked_like).replace("\\", "/").lower()
    assert prefix in norm, (
        "泄漏判据失效：真实家目录前缀匹配不上位于该目录下的路径"
        f"（prefix={prefix!r}, path={norm!r}）"
    )

    # 反向：无关路径不得被判为泄漏（防判据过宽 → 假红 → 逼人删守卫）
    assert prefix not in "c:/somewhere/else/logs/medkit.log", (
        "泄漏判据过宽：无关路径也被判为泄漏（会变成假红）"
    )


def test_lifespan_does_not_bind_real_home_log(tmp_path):
    """app 进 lifespan 后，根 logger 的 handler **不得**指向用户真实家目录。

    这是哨兵缺陷（2026-09-27）的行为级守卫：`main._lifespan` 裸调 `setup_logging()`，
    其回落是 `cfg.CONFIG_DIR/logs`；而 logger handler 一旦装上就是**进程级全局**且幂等，
    会把整轮测试的日志都导进用户真实日志文件，哨兵结束时按 sha1 比对必然报差异。

    子进程隔离：家目录整体指到 `tmp_path`（`USERPROFILE`/`HOME`），使 lifespan 里的
    `dbs.migrate()` 也只作用于 tmp —— 否则会真的迁移用户真实 `medkit.db`（见顶部设计说明 ②）。
    断言用**真实用户目录**前缀比对，与子进程内看到的"家"无关，故隔离不削弱检定力。

    判据本身的有效性由 `test_real_user_prefix_actually_matches_a_known_path` 把守
    （正常环境下无泄漏 ⇒ 本用例恒绿，不能自证判据还有效）。
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = _probe_env(fake_home, unset=("MEDKIT_LOG_DIR",))
    info = _run_log_probe(env)
    assert info["handlers"], "探针没看到任何文件 handler——setup_logging 根本没生效"
    real_user = _real_user_medkit_prefix()
    truly_leaked = [f for f in info["handlers"] if real_user in f.replace("\\", "/").lower()]
    assert not truly_leaked, (
        "app 进 lifespan 后根 logger 绑定了**用户真实**家目录日志：\n  "
        + "\n  ".join(truly_leaked))


def test_probe_detects_leak_when_log_dir_unset(tmp_path):
    """反向验证：**不**钉 `MEDKIT_LOG_DIR` 时，探针必须报出泄漏。

    没有这一条，上面的守卫可能是「永远绿」（例如探针根本没跑 lifespan）。
    这里复现缺陷条件（不设 `MEDKIT_LOG_DIR` → 回落 `<home>/.medkit/logs`），
    但把「家」指到 tmp —— 断言探针**确实探测到了**该回落路径上的泄漏。

    为什么用「假家目录」而不是不设任何隔离：后者会让子进程真的写用户真实日志
    并触发 R5-01 哨兵（本用例的第一版就踩了这个坑 —— 反向验证本身成了污染源）。
    """
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    env = _probe_env(fake_home, unset=("MEDKIT_LOG_DIR",))
    info = _run_log_probe(env)
    assert info["handlers"], "探针未看到任何 handler"
    assert info["leaked"], (
        "不钉 MEDKIT_LOG_DIR 时探针竟未发现泄漏 —— 说明探针本身失灵，"
        "test_lifespan_does_not_bind_real_home_log 是假绿")


def test_probe_subprocess_does_not_touch_real_medkit(tmp_path):
    """探针子进程本身**不得**改动真实 `~/.medkit`（尤其不能迁移真实 `medkit.db`）。

    这是 2026-09-27 我犯错的直接产物：探针第一版只隔离了 `MEDKIT_LOG_DIR`，
    子进程进 lifespan 时 `dbs.migrate()` 把用户真实库 v7→v8（并留下 `.bak`）。
    本用例用「跑前/跑后快照」把这条钉死。
    """
    import hashlib

    real = Path(os.path.expanduser("~")) / ".medkit"

    def snap() -> dict:
        out = {}
        if real.exists():
            for q in sorted(real.rglob("*")):
                if q.is_file():
                    try:
                        out[str(q)] = hashlib.sha1(q.read_bytes()).hexdigest()
                    except OSError:
                        out[str(q)] = "<unreadable>"
        return out

    before = snap()
    fake_home = tmp_path / "home2"
    fake_home.mkdir()
    _run_log_probe(_probe_env(fake_home, unset=("MEDKIT_LOG_DIR",)))
    after = snap()
    changed = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
               if before.get(k) != after.get(k)}
    assert not changed, (
        "探针子进程改动了真实 ~/.medkit：\n"
        + "\n".join(f"  {k}: {o} -> {n}" for k, (o, n) in sorted(changed.items())))
