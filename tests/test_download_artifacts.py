"""产物「下载」契约回归（2026-09-27）。

背景（用户反馈「网站不能正常下载文档」）：
`/api/projects/{pid}/files/{name}` 在审查文档里被定义为「产物下载」端点，但实现上
**不下发 Content-Disposition**，前端产物卡片又对 md/txt/html 一律 `target="_blank"`
且不加 `download` → 点「题库 MD / 复习手册 MD / Anki 文本」只会开一个纯文本标签页，
用户拿不到文件。本文件把修复后的契约钉死：

1. `?dl=1` → `Content-Disposition: attachment`（含中文名 filename*）；缺省 → inline
   （「在线打开」仍要能同源渲染押题卷/题库 HTML，不能被 attachment 顶掉）。
2. 内部文件（`_INTERNAL_ARTIFACT_NAMES`，含曾造成死链的 `progress.json`）**既不出现在
   产物列表、也不允许经产物路由下发**——列表过滤与下载黑名单必须是同一份常量。
3. 前端产物卡片必须提供下载形态：凡是构造产物 `/files/` 链接的分片，都必须同时给出
   `?dl=1`（扫全目录，不硬编码单个文件名——R3-16 曾因只扫一片而漏检）。

运行：`pytest tests/test_download_artifacts.py -q`（零 LLM、零网络）
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import config as cfgmod
from medkit.routers import projects as proj

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "medkit/web/js"

# 项目目录里会出现的全部内部文件名（与 proj._INTERNAL_ARTIFACT_NAMES 同源，用于播种探测）
_SEEDED_INTERNALS = ("meta.json", "slices.json", "stage.json", "progress.json",
                     "questions_raw.json", "questions_gate1.json", "checkpoint.json",
                     "paper_ids.json")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """隔离 projects_dir 的 TestClient（不碰真实 ~/.medkit）。"""
    saved = dict(cfgmod.DEFAULTS)
    saved["projects_dir"] = str(tmp_path / "projects")
    saved["api_key"] = "sk-test-key"
    monkeypatch.setattr(cfgmod, "load", lambda: dict(saved))
    monkeypatch.setattr(cfgmod, "save", lambda c: saved.update(c))
    # base_url 用 127.0.0.1：Host 校验放行（testserver 会被中间件 403）
    return TestClient(m.app, base_url="http://127.0.0.1")


def _seed(tmp_path: Path, pid: str = "dl_test") -> Path:
    """播种一个含正常产物 + 全部内部文件的项目目录。"""
    base = tmp_path / "projects" / pid
    out = base / "最终产物"
    out.mkdir(parents=True)
    (base / "meta.json").write_text(json.dumps(
        {"pid": pid, "subject": "下载契约测试", "exam": "期末", "stage": "done",
         "toggles": {}, "target": 10, "quota": [], "created": "2026-09-27T00:00:00"},
        ensure_ascii=False), encoding="utf-8")
    (out / "qbank.md").write_text("# 题库\n", encoding="utf-8")
    (out / "押题卷.html").write_text("<!doctype html><h1>卷</h1>", encoding="utf-8")
    (out / "anki_export.txt").write_text("正面\t反面\n", encoding="utf-8")
    # 内部文件全部落在项目根（真实布局：progress.json/stage.json 就在根目录）
    for n in _SEEDED_INTERNALS:
        (base / n).write_text("{}", encoding="utf-8")
    return base


# ---------------------------------------------------------------- 1) dl=1 / inline
def test_dl_flag_switches_to_attachment(client, tmp_path):
    """`?dl=1` 必须下发 attachment；不带 dl 必须保持 inline（在线打开依赖同源渲染）。"""
    _seed(tmp_path)
    r = client.get("/api/projects/dl_test/files/qbank.md?dl=1")
    assert r.status_code == 200, r.text
    cd = r.headers.get("content-disposition", "")
    assert cd.startswith("attachment"), f"dl=1 未下发 attachment：{cd!r}"
    assert "qbank.md" in cd
    # 内容照常下发（不比对换行：write_text 在 Windows 上会把 \n 翻成 os.linesep）
    assert "题库" in r.text

    # 缺省（在线预览/在线打开）：不能带 attachment，否则浏览器会下载而不是渲染
    r2 = client.get("/api/projects/dl_test/files/qbank.md")
    assert r2.status_code == 200
    assert "content-disposition" not in r2.headers, \
        f"无 dl 时不应下发 Content-Disposition：{r2.headers.get('content-disposition')!r}"


def test_dl_flag_handles_non_ascii_filename(client, tmp_path):
    """中文产物名走 filename*（RFC 5987），不得炸编码。"""
    _seed(tmp_path)
    r = client.get("/api/projects/dl_test/files/押题卷.html?dl=1")
    assert r.status_code == 200, r.text
    cd = r.headers.get("content-disposition", "")
    assert cd.startswith("attachment")
    assert "filename*=utf-8''" in cd, f"中文名未走 filename*：{cd!r}"
    assert "%E6%8A%BC%E9%A2%98%E5%8D%B7.html" in cd


# ---------------------------------------------------------------- 2) 内部文件黑名单（单源）
def test_internal_artifacts_are_neither_listed_nor_served(client, tmp_path):
    """内部文件既不出现在产物列表，也不能经产物路由下发。

    回归锚点：`progress.json` 曾只被路由侧拦下、没进列表排除表 → 管线运行期间它以
    产物卡片形式出现在「我的项目」，点开 404「文件不存在」（死链）。
    """
    _seed(tmp_path)
    status = client.get("/api/projects/dl_test/status")
    assert status.status_code == 200, status.text
    listed = set(status.json()["artifacts"])

    # 正常产物必须在列表里（否则本用例会因「列表为空」而假绿）
    assert {"qbank.md", "押题卷.html", "anki_export.txt"} <= listed, listed

    leaked = sorted(listed & set(_SEEDED_INTERNALS))
    assert not leaked, f"内部文件泄漏进产物列表：{leaked}"

    for name in _SEEDED_INTERNALS:
        r = client.get(f"/api/projects/dl_test/files/{name}")
        assert r.status_code == 404, f"内部文件 {name} 可被下发（HTTP {r.status_code}）"
        # 带 dl=1 同样不能绕过
        r2 = client.get(f"/api/projects/dl_test/files/{name}?dl=1")
        assert r2.status_code == 404, f"内部文件 {name} 可经 dl=1 下发"


def test_progress_json_is_a_regression_anchor():
    """`progress.json` 必须留在单源黑名单里——它正是「死链」缺陷的回归锚点。"""
    assert "progress.json" in proj._INTERNAL_ARTIFACT_NAMES, \
        "progress.json 被移出内部文件黑名单：产物列表会出现点不开的死链卡片"


def test_list_filter_and_route_blacklist_are_the_same_constant(client, tmp_path):
    """列表过滤与下载黑名单必须同源——用行为断言：列表排除集 ⊇ 路由 404 集。

    做法：把全部内部名播种进项目，断言「列表里一个都没有」且「路由对每一个都 404」。
    任一侧漏掉某个名字，两边的差集就会让本用例变红。

    ## 补「循环非空」前提 + 与播种清单对齐（2026-09-29 R22 修）

    下面那条 `for name in proj._INTERNAL_ARTIFACT_NAMES:` 是**循环体内断言**形态：
    常量被掏空（或大幅缩减）时**循环体一次都不跑、用例恒绿**——
    哪怕路由侧一个内部名都没拦下，也判绿（纯粹因为没跑，已用 `diag_r22.py` 实证）。
    另：本文件的 `_SEEDED_INTERNALS` 是**手抄的**产品常量副本，两边可能各自漂移。
    ⇒ 开循环前先钉三条前提：① 常量非空；② 与播种清单**逐元素相等**；
    ③ 播种清单里每个名字确实被播种成了文件（防「清单里有、盘上无」）。
    """
    internal = set(proj._INTERNAL_ARTIFACT_NAMES)
    assert internal, (
        "`_INTERNAL_ARTIFACT_NAMES` 为空——下面的循环会空转成绿。"
        "至少要有 progress.json（死链回归锚点）。")
    assert internal == set(_SEEDED_INTERNALS), (
        "产品黑名单与本文件播种清单不一致（手抄副本漂移了）："
        f"产品独有 = {sorted(internal - set(_SEEDED_INTERNALS))}；"
        f"播种独有 = {sorted(set(_SEEDED_INTERNALS) - internal)}。"
        "两边都要显式改，别只改一处。")

    base = _seed(tmp_path)
    missing_on_disk = [n for n in _SEEDED_INTERNALS if not (base / n).is_file()]
    assert not missing_on_disk, f"播种清单里的文件没落到盘上：{missing_on_disk}"

    listed = set(client.get("/api/projects/dl_test/status").json()["artifacts"])
    for name in internal:
        assert name not in listed, f"{name} 未被列表侧排除"
        assert client.get(f"/api/projects/dl_test/files/{name}").status_code == 404, \
            f"{name} 未被路由侧拦下"


# ---------------------------------------------------------------- 3) 前端下载形态（扫全目录）
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT_RE = re.compile(r"(?<!:)//[^\n]*")


def _js_code_only(path: Path) -> str:
    """剥离注释后的 JS 源码。

    ⚠️ 必须剥注释：本文件的说明性注释里就写着 `?dl=1`，直接扫原文会让「把下载逻辑
    删掉、只留注释」的注入照样变绿（实测过：注入回旧形态时源码守卫仍然通过）。
    只扫代码才能让守卫绑定行为。
    """
    src = _BLOCK_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
    return _LINE_COMMENT_RE.sub("", src)


def test_every_artifact_link_site_offers_download():
    """凡是构造产物 `/files/` 链接的分片，都必须同时给出 `?dl=1` 下载形态。

    扫**全目录**：R3-16 的教训是守卫只扫单个文件名，导致同目录另一片的同类违规长期漏检。
    """
    sites: list[str] = []
    for f in sorted(JS_DIR.glob("*.js")):
        code = _js_code_only(f)
        if "/files/" in code:
            sites.append(f.name)
            assert "?dl=1" in code, (
                f"{f.name} 构造了产物 /files/ 链接却没有 ?dl=1 下载形态——"
                "产物会退回「只能开新标签页预览、无法下载」")
    assert sites, "未扫描到任何 /files/ 链接构造点——扫描口径可能失效，需人工确认"


def _run_artifact_links(pid: str, names: list[str]) -> str:
    """在 node 里跑**真身** `artifactLinks(pid, names)`，返回渲染出的 HTML。

    R25（2026-09-29）：把源码子串断言升级为**行为断言**。
    `artifactLinks` / `ART_LABEL` / `esc` 全在同一 JS 文件内，可独立加载。
    """
    js = JS_DIR / "review-desk-project.js"
    if shutil.which("node") is None:
        pytest.fail("需要 node 执行真身 artifactLinks（不允许静默跳过）")
    code = (
        "const fs=require('fs');"
        "const src=fs.readFileSync(process.argv[1],'utf8');"
        "const i=src.indexOf('const ART_LABEL = [');"
        "const j=src.indexOf('];', i);"
        "if(i<0||j<0) throw new Error('ART_LABEL not found');"
        "const artLabel=src.slice(i,j)+'];';"
        "const a=src.search(/function\\s+artifactLinks\\s*\\([^)]*\\)\\s*\\{/);"
        "if(a<0) throw new Error('artifactLinks not found');"
        "const b=src.indexOf('\\n}', a);"
        "const fnBody=src.slice(a,b)+'\\n}';"
        "const esc=s=>String(s??'').replace(/[&<>\"']/g,"
        "c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
        "const f=new Function('esc', artLabel+fnBody+'; return artifactLinks;')(esc);"
        "process.stdout.write(f(process.argv[2], JSON.parse(process.argv[3])));"
    )
    r = subprocess.run(["node", "-e", code, str(js), pid, json.dumps(names)],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, "node 执行 artifactLinks 失败：%s" % r.stderr
    return r.stdout


_ARTMAIN_RE = re.compile(r'<a class="artmain"([^>]*)>')


def test_artifact_chips_do_not_preview_only_documents():
    """文档产物（md/txt/json）主链接必须**直接下载**；HTML 产物主链接才「在线打开」。

    ## 为什么改成「跑真身」（2026-09-29 R25）

    旧版是三条源码子串/正则断言，双向注入实测**两个方向都坏**：

    ① **假绿**：`assert 'target="${n.endsWith' not in src` 是**恒真**（该字面拼法
       在真身里已不存在）。它的**意图**是「不许给文档产物加 `target="_blank"`」，
       但只堵了「旧写法的一种字面拼法」。实测注入
       `<a class="artmain" href="${dlUrl}" download target="_blank" …>`（注释明令
       禁止的形态，文档链接同时 download + _blank）⇒ 守卫**全绿**。
    ② **假红**：`assert re.search(r'class="artmain" href="${dlUrl}" download')` 绑死
       「三个属性的书写顺序与相对位置」；`"artchip" in src` 绑类名字面量。

    现在跑真身 `artifactLinks`，按**渲染结果**断言（与书写无关）：
    文档产物的主链接（`class="artmain"`）必须带 `?dl=1` 且 `download`、**不含** `target`；
    HTML 产物的主链接必须含 `target="_blank"`（在线打开，同源渲染才能答题/判分/打印）。
    """
    doc_html = _run_artifact_links("p1", ["qbank.md"])
    main = _ARTMAIN_RE.search(doc_html)
    assert main, f"文档产物未渲染出主链接：{doc_html!r}"
    attrs = main.group(1)
    assert "?dl=1" in attrs, f"文档产物主链接未带 dl=1（拿不到文件）：{attrs!r}"
    assert "download" in attrs, f"文档产物主链接无 download 属性：{attrs!r}"
    assert "target=" not in attrs, \
        f"文档产物主链接带 target（会把下载变成预览，正是原缺陷）：{attrs!r}"

    # HTML 产物：主链接必须保留「在线打开」（同源渲染依赖它）
    html_out = _run_artifact_links("p1", ["押题卷.html"])
    hmain = _ARTMAIN_RE.search(html_out)
    assert hmain, f"HTML 产物未渲染出主链接：{html_out!r}"
    assert 'target="_blank"' in hmain.group(1), \
        f"HTML 产物的「在线打开」入口被误删：{hmain.group(1)!r}"
    # 且 HTML 产物仍要给出下载形态（⇩ 次链接带 download）
    assert 'download' in html_out, "HTML 产物缺少下载入口"

    # .apkg 走 /export/apkg 直下（服务端已带 attachment）
    apkg_out = _run_artifact_links("p1", ["deck.apkg"])
    assert "/export/apkg" in apkg_out, f"apkg 未走导出端点：{apkg_out!r}"
    assert "download" in apkg_out, "apkg 主链接缺少 download"
