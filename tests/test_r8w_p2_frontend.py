"""B9 回归（R8+W）：P2 第二批——前端转义三条 + 押题卷标注 + 删科目孤儿行 + 更新开关 + regen 去重。

- **S3-1** `learnChip()` 标签体未转义（未知 state 时 `txt` 等于入参本身）。
- **S3-2** 仪表盘「最近项目」`p.target` 未转义。
- **S3-3** Anki 导出文件名未取 basename（服务端 content-disposition 原样用）。
- **S3-7** 押题卷无「AI 生成、非官方真题」标注。
- **S3-11** 删科目漏清 `syllabus_items` / `realexam_freq`（同名重建「复活」）。
- **S3-14** 启动更新检查无关闭开关。
- **M2-03** `regen` 无在飞去重（连点两次串行各掷一次 = 双份计费）。
- **S3-16** CI 里 `npm ci` 失败静默回退 `npm install`（弱化 lock 约束）。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import config as cfgmod  # noqa: E402
from medkit.core import db  # noqa: E402
from medkit.core import library as lib
from medkit.core import update as upd  # noqa: E402
from medkit.render import qbank_html  # noqa: E402

WEB = ROOT / "medkit" / "web" / "js"


def _js(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _base_name_body(src: str) -> str:
    """从 learn-review.js 源码里切出 `_baseName` 函数体。

    R24：用正则容忍 `function  _baseName (` 这类等价书写（旧版 `src.index(
    "function _baseName(")` 精确匹配 ⇒ 仅多一个空格就 IndexError ⇒ 假红）。
    """
    m = re.search(r"function\s+_baseName\s*\([^)]*\)\s*\{", src)
    assert m, "learn-review.js 里找不到 _baseName 函数定义"
    body = src[m.start():]                 # 从 `function` 起（含签名），供 node 整体执行
    return body[:body.index("\n}") + 2]


def _eval_base_name_body(body: str, names: list[str]) -> dict[str, str]:
    """用 node 执行给定的 `_baseName` 函数体，返回 {输入: 输出}。

    ## 为什么真的跑 node（2026-09-29 R12-2）
    手抄一份 Python 复刻来「核对语义」是**自证式假绿**：
    真身被改坏时副本不受影响，用例照样绿（实测删掉 `.pop()` 仍绿）。
    只有执行真身，断言才落在被测对象上。
    """
    if shutil.which("node") is None:
        pytest.fail("需要 node 执行 learn-review.js 的 _baseName（不允许静默跳过）")
    code = (
        "const body=process.argv[1]; const names=JSON.parse(process.argv[2]);"
        "const fn=new Function(body+'; return _baseName;')();"
        "console.log(JSON.stringify(names.map(n=>fn(n))));"
    )
    r = subprocess.run(["node", "-e", code, body, json.dumps(names)],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, "node 执行 _baseName 失败：%s" % r.stderr
    return dict(zip(names, json.loads(r.stdout.strip()), strict=True))


def _run_base_name(names: list[str]) -> dict[str, str]:
    """跑**真身文件**里的 `_baseName`。"""
    return _eval_base_name_body(_base_name_body(_js("learn-review.js")), names)


# ---------------------------------------------------------------- S3-1 / S3-2 / S3-3

_XSS_PAYLOAD = "<img src=x onerror=alert(1)>"


def _run_learn_chip(state: str) -> str:
    """在 node 里跑**真身** `learn.js` 的 `learnChip(state)`，返回渲染结果。

    R24（2026-09-29）：把源码子串断言升级为「跑真身 JS」。
    `esc` 按 `app.js:7` 的同一实现注入 —— 这正是要**验证真身用了它**。
    """
    if shutil.which("node") is None:
        pytest.fail("需要 node 执行真身 learnChip（不允许静默跳过）")
    code = (
        "const fs=require('fs');"
        "const src=fs.readFileSync(process.argv[1],'utf8');"
        "const decl=(src.match(/const LEARN_STATE = \\{[^}]*\\};/)||[])[0];"
        "if(!decl) throw new Error('LEARN_STATE not found');"
        "const a=src.search(/function\\s+learnChip\\s*\\([^)]*\\)\\s*\\{/);"
        "if(a<0) throw new Error('learnChip body not found');"
        "const b=src.indexOf('\\n}', a);"
        "if(b<0) throw new Error('learnChip body end not found');"
        "const body=src.slice(a,b)+'\\n}';"
        "const esc=s=>String(s??'').replace(/[&<>\"']/g,"
        "c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
        "const fn=new Function('esc', decl+body+'; return learnChip;')(esc);"
        "process.stdout.write(String(fn(JSON.parse(process.argv[2]))));"
    )
    r = subprocess.run(["node", "-e", code, str(WEB / "learn.js"), json.dumps(state)],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, "node 执行 learnChip 失败：%s" % r.stderr
    return r.stdout


def _strip_js_comments(src: str) -> str:
    """剥掉 JS 注释（块 + 行），避免「注释里写了目标串」造成的假绿/假红。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", src)


def test_learnchip_body_is_escaped():
    """S3-1：标签体必须转义（未知 state 时 txt = 入参）。

    ## 为什么改成「跑真身」（2026-09-29 R24）

    旧版是源码子串断言：

        assert '${esc(txt)}' in src      # 绑书写格式
        assert "${txt}" not in src

    **双向注入实测**：`esc( txt )`（仅多两个空格，语义不变）就让断言失败 ——
    判据绑的是「当时的书写格式」而非「转义这一行为」。拆行、中间变量、
    `esc(txt) !== '' ? …` 等等价写法都会误红，而**假红会逼人删守卫**。

    现在直接跑真身 `learnChip("<img src=x onerror=alert(1)>")`：
    渲染结果里不得出现未转义的 `<`。已用注入验证「去掉 esc ⇒ 红」。
    """
    out = _run_learn_chip(_XSS_PAYLOAD)
    assert "<img" not in out, f"learnChip 未转义标签体（XSS 面）：{out!r}"
    assert "&lt;img" in out, f"learnChip 未转义标签体（XSS 面）：{out!r}"


def test_dashboard_target_is_escaped():
    """S3-2：仪表盘 p.target 必须转义。

    ## 为什么改成「剥注释后的结构判据」（2026-09-29 R24）

    旧版 `"esc(p.target)" in src` 双向注入实测：`esc( p.target )`（多空格）
    即假红。但这段是真身**内联模板字面量**（`app.js:509`），行为化需要构造
    整个 `recent.map(...)` 闭包 —— 成本过高。⇒ 用**容错的调用形态判据**：
    剥掉注释后，正则匹配 `esc` **调用**且实参是 `p.target`（容忍任意空白）。
    """
    code = _strip_js_comments(_js("app.js"))
    # 正面：必须存在 esc(p.target) 调用（容忍空白/换行）
    assert re.search(r"\besc\s*\(\s*p\.target\s*\)", code), \
        "仪表盘 p.target 未过 esc（转义缺失）"
    # 反面：不得出现**未转义**的 ${p.target} 插值
    assert not re.search(r"\$\{\s*p\.target\s*\}", code), \
        "仍存在未转义的 ${p.target} 插值"


def test_anki_download_name_is_basenamed():
    """S3-3：content-disposition 文件名必须过 _baseName。

    ## 为什么改成「跑真身 + 结构判据」（2026-09-29 R24）

    旧版三条子串断言（`"function _baseName(" in src` 等）双向注入实测全假红：
    函数签名加空格 `function  _baseName( ` 即误红。
    ⇒ 拆成：**结构**（剥注释后判「下载赋值真的用了 `_baseName(...)` 调用」）
    + **行为**（跑真身验证清洗真的生效，与 `test_base_name_strips_path_and_control` 同源）。
    """
    # ① 结构：剥注释后，`a.download` 赋值必须经过 `_baseName(...)` 调用
    code = _strip_js_comments(_js("learn-review.js"))
    m = re.search(r"a\.download\s*=\s*([^;]+);", code)
    assert m, "未找到 a.download 赋值点"
    assert "_baseName" in m.group(1), \
        f"下载文件名未过 _baseName 清洗：{m.group(1)!r}"
    # ② 反面：不得有「直接用 decodeURIComponent 结果当文件名」的写法
    assert not re.search(r"a\.download\s*=\s*m\s*\?\s*decodeURIComponent", code), \
        "下载文件名仍有未清洗分支"
    # ③ 行为：真身 _baseName 必须真的剥路径（跑 node）
    got = _run_base_name(["../../etc/x.apkg", "a\\b\\c.apkg"])
    assert got["../../etc/x.apkg"] == "x.apkg", got
    assert got["a\\b\\c.apkg"] == "c.apkg", got


def test_base_name_strips_path_and_control():
    """S3-3：清洗规则本身——**执行真身 JS**，不再用手抄的 Python 复刻。

    ## 为什么重写（2026-09-29 R12-2）
    旧版把 `_baseName` 的清洗规则**用手抄的 Python `re.sub` 复刻了一遍**，
    然后断言那个 Python 版的行为。实测：把真身 JS 的 `.pop()`（整条
    「只取 basename」的安全性质！）删掉，用例**仍然绿** —— 只有「把 `replace`
    这个词整个删掉」这种无关紧要的改动才会红。
    ⇒ 这是**自证式假绿**：测的是副本，不是真身。

    现改为用 node 加载真身后的 `_baseName` 直接跑，输入输出都来自真实实现。
    """
    cases = {
        "../../etc/x.apkg": "x.apkg",
        "a\\b\\c.apkg": "c.apkg",
        "bad\x00name.apkg": "badname.apkg",
        "": "MedKit记忆卡.apkg",
        "/abs/path.pkg": "path.pkg",   # 绝对路径也必须只留 basename
        "ok.apkg": "ok.apkg",
    }
    got = _run_base_name(list(cases))
    for name, want in cases.items():
        assert got[name] == want, (
            "_baseName(%r) = %r，应为 %r —— 真身清洗规则被改坏"
            % (name, got[name], want))


def test_base_name_guard_is_not_vacuous():
    """元守卫：证明上一条真的在测**真身**（改坏 JS ⇒ 必红）。"""
    src = _js("learn-review.js")
    broken = src.replace('.split("/").pop() || ""', '|| ""')
    assert broken != src, "注入锚点未命中（探针失效）"
    # 用改坏后的函数体跑同一组输入：basename 清洗已失效 ⇒ 结果必然不同
    body = broken[broken.index("function _baseName("):]
    body = body[:body.index("\n}") + 2]
    got = _eval_base_name_body(body, ["../../etc/x.apkg", "/abs/path.pkg"])
    assert got["../../etc/x.apkg"] != "x.apkg", (
        "删掉 .pop() 后 _baseName 仍返回 x.apkg —— 说明用例没在测真身（假绿）"
    )


# ---------------------------------------------------------------- S3-7

def test_paper_has_ai_disclaimer():
    """S3-7：押题卷页头必须声明「AI 生成、非官方真题」。"""
    q = {"id": "Q001", "type": "A1", "bloom": "记忆", "question": "题？",
         "options": ["甲", "乙", "丙", "丁", "戊"], "answer": "A", "analysis": "解析【源:切片S001】"}
    html = qbank_html.export_paper_html([q], "押题卷")
    assert "非官方真题" in html
    assert "AI" in html


# ---------------------------------------------------------------- S3-11

@pytest.fixture
def iso_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "medkit.db")
    db.reset_conn()
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    # ⚠️ library.DB_FILE 是**导入期固化**的常量（= dbs.DB_PATH 的快照），
    # 只 patch db.DB_PATH 不会改变它 → 会走 JSON 分支。这里显式重定向。
    monkeypatch.setattr(lib, "DB_FILE", tmp_path / "medkit.db")
    return tmp_path


def test_delete_subject_clears_syllabus_and_realexam(iso_db):
    """S3-11：删科目必须同时清掉 syllabus_items / realexam_freq 的同科目行。"""
    db.migrate()
    with db.tx(write=True) as cur:
        db.put_row(cur, "syllabus_items", {"id": "s1", "subject": "儿科", "chapter": "第一章"},
                   ("subject", "chapter", "kind", "item"))
        db.put_row(cur, "realexam_freq", {"id": "r1", "subject": "儿科", "item": "考点"},
                   ("subject", "chapter", "item"))
        db.put_row(cur, "syllabus_items", {"id": "s2", "subject": "内科", "chapter": "第二章"},
                   ("subject", "chapter", "kind", "item"))
    res = lib.delete_subject_with_backup("儿科")
    assert res["deleted"]["syllabus_items"] == 1, res["deleted"]
    assert res["deleted"]["realexam_freq"] == 1, res["deleted"]
    conn = db.get_conn()
    left = {r[0] for r in conn.execute("SELECT subject FROM syllabus_items")}
    assert left == {"内科"}, "儿科的大纲条目应被清掉，内科的必须保留"


# ---------------------------------------------------------------- S3-14

def test_update_check_switch_disables_network(iso_db, monkeypatch):
    """S3-14：开关关闭时**不得发起任何网络请求**（用会抛异常的 httpx.get 守住）。"""
    (iso_db / "config.json").write_text(json.dumps({"update_check": False}), encoding="utf-8")

    def _boom(*a, **kw):
        raise AssertionError("开关关闭却仍发起了网络请求（S3-14 回归）")

    monkeypatch.setattr(upd.httpx, "get", _boom)
    res = upd.check()
    assert res["skipped"] is True
    assert res["has_update"] is False


def test_update_check_default_still_checks(iso_db, monkeypatch):
    """对照组：未配置时保持原行为（会尝试请求）。"""
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tag_name": "v0.0.1", "body": ""}

    def _fake_get(*a, **kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(upd.httpx, "get", _fake_get)
    upd.check()
    assert calls["n"] == 1


# ---------------------------------------------------------------- M2-03

def test_regen_is_deduplicated(iso_db, monkeypatch):
    """M2-03：同一题在飞时第二次请求必须 409（否则串行各掷一次 = 双份计费）。"""
    from medkit.core import dedupe
    from medkit.routers import review as rev

    key = "regen:p1:Q001"
    dedupe.end(key)
    assert dedupe.begin(key) is False        # 模拟第一次请求已在飞
    try:
        with pytest.raises(HTTPException) as ei:
            rev._regen_question_sync("p1", "Q001")
        assert ei.value.status_code == 409
    finally:
        dedupe.end(key)


def test_regen_releases_lock_on_success_and_failure(iso_db, monkeypatch):
    """M2-03：无论成功/失败都要释放登记（否则该题永久 409）。"""
    from medkit.core import dedupe
    from medkit.routers import review as rev

    monkeypatch.setattr(rev, "_regen_question_locked",
                        lambda pid, qid: {"ok": True})
    rev._regen_question_sync("p2", "Q002")
    assert dedupe.is_active("regen:p2:Q002") is False

    def _boom(pid, qid):
        raise RuntimeError("模拟重掷失败")

    monkeypatch.setattr(rev, "_regen_question_locked", _boom)
    with pytest.raises(RuntimeError):
        rev._regen_question_sync("p3", "Q003")
    assert dedupe.is_active("regen:p3:Q003") is False, "失败路径未释放登记"


# ---------------------------------------------------------------- S3-16

def test_ci_has_no_npm_install_fallback():
    """S3-16：CI 不得再用 `npm install` 静默回退（弱化 lock 约束）。"""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "|| npm install" not in ci, "仍存在 npm install 回退"
    assert "npm ci" in ci
    assert "npm audit" in ci, "缺少 npm 侧审计信号"
