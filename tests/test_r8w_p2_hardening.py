"""B10 回归（R8+W）：P2 收尾批——提示词占位符 / 出题硬规则 / 密钥安全提示 / 上传内容闸。

- **S3-9**：声明了 `{占位符}` 的提示词必须以 `render_prompt` 渲染（原 `syllabus_extract.md`
  走 `load_prompt` 原样加载，占位符永不替换）。
- **S3-8**：`medgen.md` 必须有「答案无法由素材确定则**不出该题**」的硬规则。
- **S3-10 / S3-12**：DPAPI 回退明文要**常驻可见**；解密失败**不得回吐密文**。
- **M2-04**：上传要有魔数闸 + 压缩炸弹闸；PDF 要有页数闸。
"""

import ast
import io
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.agents import render_prompt  # noqa: E402
from medkit.core import config as cfgmod  # noqa: E402
from medkit.routers import _common as cmn  # noqa: E402

PROMPTS = ROOT / "medkit" / "prompts"


# ---------------------------------------------------------------- S3-9

def test_syllabus_extract_placeholder_is_rendered():
    """S3-9：渲染后不得残留字面量占位符，且注入内容必须出现。"""
    out = render_prompt("syllabus_extract.md", subject_text="生理学\n第一章 绪论")
    assert "{subject_text}" not in out, "占位符未被替换"
    assert "生理学" in out and "第一章 绪论" in out


def _prompt_files() -> list[Path]:
    """S3-9 守卫的**唯一**提示词扫描面（单一来源）。

    元守卫 `test_prompt_scan_face_is_not_empty` 与主守卫
    `test_all_prompts_with_placeholders_use_render_prompt` **都**必须经由本函数取
    扫描面。为什么单一来源（R26 踩过的坑）：我第一版把元守卫写成自己重算一遍
    `sorted(PROMPTS.glob("*.md"))`，于是**把主守卫的循环改窄、元守卫照样绿**——
    因为两者遍历的**不是同一个**集合。共用本函数后，任何一处改窄都会同时命中元守卫。
    """
    return sorted(PROMPTS.glob("*.md"))


def _medkit_py_files() -> list[Path]:
    """`_prompt_callsites()` 的**唯一**源码扫描面（单一来源，理由同上）。"""
    return list((ROOT / "medkit").rglob("*.py"))


def _prompt_callsites():
    """扫全 `medkit/`，返回 (裸 load_prompt 的文件名集合, render_prompt 的文件名集合)。

    判据走 AST：只看**调用的第一个实参是不是常量文件名**，与书写格式无关。
    扫描面经 `_medkit_py_files()` 取（与元守卫同源）。
    """
    bare: set[str] = set()
    rendered: set[str] = set()
    for p in _medkit_py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
                continue
            if n.func.id not in ("load_prompt", "render_prompt"):
                continue
            if not (n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                continue
            name = n.args[0].value
            (bare if n.func.id == "load_prompt" else rendered).add(name)
    return bare, rendered


def test_prompt_scan_face_is_not_empty():
    """元守卫：S3-9 的两侧扫描面都不许塌缩（R26 修的假绿）。

    ## 为什么（2026-09-29 R26 实测的真缺陷）

    `test_all_prompts_with_placeholders_use_render_prompt` 的结构是
    「遍历 + 聚合 (`problems`) + `assert not problems`」。**上界塌缩时聚合恒空**
    ⇒ 断言恒真。实测注入 `for md in []:` 后该文件 **17 passed**（假绿）。

    这是「循环非空前提缺失」（R22）的**姊妹形态**：R22 是循环**体**里的断言被空转，
    本条是循环**外**的聚合断言在空集上恒真。同属「守卫依赖一个没被断言的前提」。

    两侧都要钉（缺一不可）：
      1. `PROMPTS/*.md` 非空 + 数量下限 —— 左界；
      2. `_prompt_callsites()` 的**源码扫描面**非空 —— 右界。
         右界塌缩（如 `(ROOT / "medkit").rglob("*.py")` 写窄）会让 `rendered` 全空，
         此时**每个**有占位符的提示词都被报「没有 render 调用点」——那是**假红**，
         同样会逼人删守卫，也必须钉住。
      3. 与 **git 索引**核对 prompts 目录 —— 独立来源，照出「文件被改名/移出」时
         glob 一起变小的恒真盲区。
    """
    mds = _prompt_files()
    assert mds, (
        "`medkit/prompts/*.md` 扫描面为空——S3-9 守卫会退化成空循环 + 恒真断言。"
    )
    assert len(mds) >= 5, (
        f"prompts 只有 {len(mds)} 个（下限 5）——扫描面疑似整体塌缩：{[p.name for p in mds]}"
    )

    py_files = _medkit_py_files()
    assert py_files, (
        "`medkit/**/*.py` 扫描面为空——`_prompt_callsites()` 会返回两个空集合，"
        "于是每个提示词都被误报「没有 render 调用点」（假红）。"
    )

    tracked = _tracked_prompts()
    if tracked is not None:
        gone = tracked - {p.name for p in mds}
        assert not gone, (
            "以下提示词在 git 索引里存在，但磁盘 glob 扫不到（改名/移出？）：\n  "
            + "\n  ".join(sorted(gone))
        )


def _tracked_prompts() -> set[str] | None:
    """从 **git 索引**取 `medkit/prompts/*.md` 的文件名集合。

    返回 None 表示拿不到 git（源码包脱离仓库）——此时跳过该条，但保留上面的
    非空与数量下限（那两条不依赖 git）。
    """
    try:
        r = subprocess.run(
            ["git", "ls-files", "medkit/prompts/"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return {Path(x).name for x in r.stdout.split() if x.endswith(".md")}


def test_all_prompts_with_placeholders_use_render_prompt():
    """S3-9 全目录守卫（R3-16 教训：不要只盯一个文件）。

    规则：提示词里出现 `{var}` 占位符 → 调用点**必须**用 `render_prompt`；
    只有**没有**占位符的提示词才允许 `load_prompt`。

    ## 判据是**双向**的（2026-09-29 R16 改）

    旧版只做**否定式**检查：`f'load_prompt("{name}")' in src_all` ——
    即「有占位符的提示词**不许**被裸 load」。它**从不**正面要求
    「这个提示词**真的**被 render」。后果（真身注入实测）：
    把 `medgen.py` 的 `render_prompt("medgen.md", **parts)` 整行删掉
    （= 9 个占位符的生成提示词**从不加载**），守卫**仍然全绿** —— 假绿。
    这正是 R12「排除式判据」的老毛病：只禁一种坏写法，不证明好路径存在。

    现判据两条腿：
      ① **正面**：每个有占位符的提示词，必须在某处被 `render_prompt("<name>", …)` 调用；
      ② **反面**：该提示词不得出现在裸 `load_prompt("<name>")` 里（AST，非子串）。
    """
    bare, rendered = _prompt_callsites()
    problems: list[str] = []
    for md in _prompt_files():   # 与元守卫同源：改窄这里，元守卫必红
        text = md.read_text(encoding="utf-8")
        # 只看「像变量」的占位符（排除 JSON 示例里的普通花括号）
        vars_ = {m for m in re.findall(r"\{([a-z][a-z0-9_]{2,})\}", text)}
        if not vars_:
            continue
        if md.name not in rendered:
            problems.append(
                f"{md.name} 有占位符 {sorted(vars_)}，但**没有任何 render_prompt 调用点**"
                "——提示词从不加载，占位符永不替换")
        if md.name in bare:
            problems.append(f"{md.name} 有占位符 {sorted(vars_)}，却存在裸 load_prompt 调用")
    assert not problems, "提示词占位符与加载方式不一致：\n" + "\n".join(problems)


def test_prompt_load_guard_is_not_vacuous():
    """元守卫：证明「正面要求 render 调用点」这一条真能抓到「删掉调用」。

    自带内存样本，不读真身（真身是注入靶子）。
    """
    def _scan(files: dict[str, str]):
        bare: set[str] = set()
        rendered: set[str] = set()
        for src in files.values():
            tree = ast.parse(src)
            for n in ast.walk(tree):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
                    continue
                if n.func.id not in ("load_prompt", "render_prompt"):
                    continue
                if not (n.args and isinstance(n.args[0], ast.Constant)
                        and isinstance(n.args[0].value, str)):
                    continue
                (bare if n.func.id == "load_prompt" else rendered).add(n.args[0].value)
        return bare, rendered

    # ① 正常：render_prompt 在场 ⇒ rendered 命中
    bare, rendered = _scan({"a.py": 'x = render_prompt("medgen.md", **p)\n'})
    assert "medgen.md" in rendered and "medgen.md" not in bare

    # ② 削真行为：删掉 render 调用（提示词从不加载）⇒ rendered 为空（可抓）
    bare, rendered = _scan({"a.py": 'x = "__DEAD__"\n'})
    assert "medgen.md" not in rendered, "[元守卫] 删掉 render 调用后仍算「已渲染」→ 正面判据无效"

    # ③ 反面：退回裸 load_prompt ⇒ bare 命中
    bare, rendered = _scan({"a.py": 'x = load_prompt("medgen.md")\n'})
    assert "medgen.md" in bare, "[元守卫] 裸 load_prompt 未被识别"

    # ④ 等价改写不该被误判：换行 / 空格 / 单引号 / 关键字实参
    for code in ('x = render_prompt(\n    "medgen.md",\n    **p)\n',
                 "x = render_prompt('medgen.md', **p)\n",
                 'x = render_prompt( "medgen.md" , **p )\n'):
        _, rendered = _scan({"a.py": code})
        assert "medgen.md" in rendered, f"[元守卫] 等价改写被误判为「未渲染」：{code!r}"



def test_syllabus_call_site_renders_the_prompt():
    """S3-9 接线级：syllabus.py 的调用点必须真的把 subject_text 渲染进去。

    精确不变式：有占位符的提示词不得被裸 `load_prompt` 加载——只看「代码里出现过
    render_prompt」会漏判（某处 render、另一处 load 也算违规）。

    ## 判据走 AST 而非文本子串（2026-09-29 R15 改）

    旧版末条 `assert 'render_prompt(\\n' in src` 绑**书写格式**：
    真身写成 `render_prompt(\n    "syllabus_extract.md", …`（换行后接实参）恰好通过，
    但只要把实参挪上来一行、或在括号后加空格，就会**假红**。
    要守的性质是「**存在 render_prompt 调用，且它的实参里带 subject_text**」——
    与换行/空格无关。
    """
    src = (ROOT / "medkit" / "core" / "syllabus.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # ① 不得裸 load_prompt 加载该提示词（AST：调用的第一个实参是那个文件名）
    bare = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "load_prompt"
        and n.args and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "syllabus_extract.md"
    ]
    assert not bare, "仍在裸 load_prompt 加载该提示词（占位符不会被渲染）"

    # ② 必须存在 render_prompt 调用，且**实参里带 subject_text=（关键字或位置无关）**
    rendered = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "render_prompt"
        and any(getattr(a, "arg", None) == "subject_text" for a in n.keywords)
        and n.args and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "syllabus_extract.md"
    ]
    assert rendered, (
        "没有对 syllabus_extract.md 的 render_prompt(..., subject_text=...) 调用 ——"
        "占位符不会被渲染，系统提示里会留着字面量 {subject_text}")


def test_syllabus_render_guard_is_not_vacuous():
    """元守卫：证明 AST 判据抓得住「退回裸 load_prompt」，且不被换行/空格骗过。"""
    src = (ROOT / "medkit" / "core" / "syllabus.py").read_text(encoding="utf-8")

    def _rendered(text: str) -> int:
        tree = ast.parse(text)
        return len([
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "render_prompt"
            and any(getattr(a, "arg", None) == "subject_text" for a in n.keywords)
        ])

    assert _rendered(src) == 1, "真身里应恰好 1 处符合形态的 render_prompt 调用"

    # 证伪 ①：等价改写（括号内换行/加空格）必须**仍绿**
    compact = src.replace('render_prompt(\n                    "syllabus_extract.md"',
                          'render_prompt("syllabus_extract.md"', 1)
    assert compact != src, "证伪 ① 注入未生效"
    assert _rendered(compact) == 1, (
        "把实参挪到同一行后判据变红 —— 判据仍绑书写格式（旧版 'render_prompt(\\n' 正是如此）")

    # 证伪 ②：退回裸 load_prompt（语义变了，断言 ① 必须红）
    reverted = src.replace(
        'render_prompt(\n                    "syllabus_extract.md", subject_text=',
        'load_prompt(\n                    "syllabus_extract.md", subject_text=', 1)
    assert reverted != src, "证伪 ② 注入未生效"
    tree2 = ast.parse(reverted)
    still = [n for n in ast.walk(tree2)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "load_prompt" and n.args
             and isinstance(n.args[0], ast.Constant)
             and n.args[0].value == "syllabus_extract.md"]
    assert still, "退回裸 load_prompt 后断言 ① 抓不到 —— 守卫是假绿"


# ---------------------------------------------------------------- S3-8

def test_medgen_has_no_guess_hard_rule():
    """S3-8：medgen.md 必须有「素材不足则不出题」的硬规则（而非「避开细节」）。"""
    text = (PROMPTS / "medgen.md").read_text(encoding="utf-8")
    assert "不出这道题" in text or "不出该题" in text, "缺少「不确定即不出题」硬规则"
    assert "宁可" in text and "配额" in text, "缺少「宁可比配额少出」的取舍口径"


# ---------------------------------------------------------------- S3-10 / S3-12

def test_unprotect_never_returns_ciphertext(monkeypatch):
    """S3-12：密文解不开 → 返回空串（不得把 dpapi:xxx 当 Key 回吐）。"""
    monkeypatch.setattr(cfgmod, "_SECURITY_WARN", None)
    assert cfgmod._unprotect("dpapi:YWJj") == ""
    assert cfgmod.security_warning() == "decrypt_failed"


def test_unprotect_plaintext_passthrough(monkeypatch):
    """对照组：明文 Key 原样返回，且不产生告警。"""
    monkeypatch.setattr(cfgmod, "_SECURITY_WARN", None)
    assert cfgmod._unprotect("sk-plain") == "sk-plain"
    assert cfgmod.security_warning() is None


def test_security_warning_is_sticky_for_decrypt_failure(monkeypatch):
    """S3-10：告警常驻，且 decrypt_failed 不被后续 plaintext 覆盖。"""
    monkeypatch.setattr(cfgmod, "_SECURITY_WARN", None)
    cfgmod._set_security_warn("plaintext")
    assert cfgmod.security_warning() == "plaintext"
    cfgmod._set_security_warn("decrypt_failed")
    assert cfgmod.security_warning() == "decrypt_failed"
    cfgmod._set_security_warn("plaintext")
    assert cfgmod.security_warning() == "decrypt_failed", "解密失败不应被低优先级告警覆盖"


def test_protect_warns_when_encryption_unavailable(monkeypatch):
    """S3-10：加密不可用（非 Windows/缺依赖）时必须留下常驻告警，而不是静默明文。"""
    monkeypatch.setattr(cfgmod, "_SECURITY_WARN", None)
    monkeypatch.setattr(cfgmod, "_dpapi_available", lambda: False)
    assert cfgmod._protect("sk-secret") == "sk-secret"
    assert cfgmod.security_warning() == "plaintext"


# ---------------------------------------------------------------- M2-04

def test_magic_number_guard():
    assert cmn._content_guard(b"%PDF-1.7 ...", ".pdf") is None
    assert cmn._content_guard(b"\x89PNG\r\n\x1a\nrest", ".png") is None
    # 改名文件（MZ 开头却叫 .pdf）必须被拒
    err = cmn._content_guard(b"MZ\x90\x00\x03\x00\x00\x00", ".pdf")
    assert err and "扩展名不符" in err
    # 无魔数的文本类型跳过
    assert cmn._content_guard("# 标题".encode(), ".md") is None


def test_docx_must_be_valid_zip():
    # ① 连魔数都不对 → 走「扩展名不符」闸
    err = cmn._content_guard(b"not a zip at all", ".docx")
    assert err and "扩展名不符" in err
    # ② 魔数对但 ZIP 结构损坏 → 走结构闸
    err2 = cmn._content_guard(b"PK" + b"garbage" * 4, ".docx")
    assert err2 and "ZIP" in err2


def test_zip_bomb_is_rejected():
    """高压缩比 docx（30MB 零字节）必须被拒。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\x00" * (30 * 1024 * 1024))
    data = buf.getvalue()
    err = cmn._content_guard(data, ".docx")
    assert err and ("压缩" in err), f"压缩炸弹未被拦下：{err}"


def test_normal_docx_passes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document>小文件</w:document>")
    assert cmn._content_guard(buf.getvalue(), ".docx") is None


def test_content_guard_is_wired_into_parse_bytes():
    """M2-04 接线级：`_parse_bytes` 必须真的调用内容闸（只测守卫本体抓不到接线缺失）。"""
    res = cmn._parse_bytes("伪装.pdf", b"MZ\x90\x00\x03\x00\x00\x00" + b"x" * 64, ".pdf")
    assert "error" in res, f"改名文件竟被放行：{res}"
    assert "扩展名不符" in res["error"]
    # 对照：正常 PDF 魔数不会被内容闸拦（可能因无文本层等其它原因报错，但不该是「扩展名不符」）
    ok = cmn._parse_bytes("正常.pdf", b"%PDF-1.7\n" + b"x" * 64, ".pdf")
    assert "扩展名不符" not in (ok.get("error") or "")


def _page_gate_in_open_scope(tree: ast.AST) -> bool:
    """`with fitz.open(...) as doc:` 的作用域内是否存在「与 MAX_PDF_PAGES 的比较」。

    结构判据（与书写格式无关）：遍历 `ast.With`，若其 context 是对 `fitz.open`
    的调用，则在该 `with` 的 **body 子树**里找 `Name('MAX_PDF_PAGES')` 参与的比较。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        is_fitz_open = any(
            isinstance(it.context_expr, ast.Call)
            and isinstance(it.context_expr.func, ast.Attribute)
            and it.context_expr.func.attr == "open"
            and isinstance(it.context_expr.func.value, ast.Name)
            and it.context_expr.func.value.id == "fitz"
            for it in node.items)
        if not is_fitz_open:
            continue
        # 在 with body 内找「与 MAX_PDF_PAGES 的比较」
        for sub in node.body:
            for n in ast.walk(sub):
                if not isinstance(n, ast.Compare):
                    continue
                names = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
                if "MAX_PDF_PAGES" in names:
                    return True
    return False


def test_pdf_page_limit_exists():
    """M2-04：PDF 必须有页数闸（源码级守卫 + 常量存在）。

    ## 判据是 AST 而非「精确子串」（2026-09-29 R12-2 改）
    旧版 `"doc.page_count > MAX_PDF_PAGES" in src` 绑死书写：
    `doc.page_count>MAX_PDF_PAGES`（去空格）、换比较方向写法、拆行 **全部假红**；
    而它本该表达的结构是「**在 fitz 打开文档的作用域内，用 MAX_PDF_PAGES 做页数判断**」。
    现改为 AST：找 `with fitz.open(...) as doc:` 的**函数体内部**是否存在
    「与 MAX_PDF_PAGES 的比较」——与空格/换行/书写形式无关。
    """
    src = (ROOT / "medkit" / "core" / "extract.py").read_text(encoding="utf-8")
    assert "MAX_PDF_PAGES" in src

    tree = ast.parse(src)
    assert _page_gate_in_open_scope(tree), (
        "页数闸未接到 fitz 打开处（应在 `with fitz.open(...) as doc:` 作用域内"
        "出现与 MAX_PDF_PAGES 的比较）"
    )

    from medkit.core import extract as ex
    assert ex.MAX_PDF_PAGES >= 100


def test_pdf_page_gate_guard_is_not_vacuous():
    """元守卫：证明 `_page_gate_in_open_scope` 能抓「删闸 / 移出作用域」（否则恒绿）。"""
    src = (ROOT / "medkit" / "core" / "extract.py").read_text(encoding="utf-8")

    # 反假红：真身必须绿
    assert _page_gate_in_open_scope(ast.parse(src))

    # 证伪 1：整个闸被删
    no_gate = src.replace("if doc.page_count > MAX_PDF_PAGES:", "if False:")
    assert no_gate != src, "注入锚点未命中（探针失效）"
    assert not _page_gate_in_open_scope(ast.parse(no_gate)), "删掉页数闸却没红 —— 假绿"

    # 证伪 2：闸仍在，但比较对象换成**别的名字**（等于不再用该常量约束）
    other = src.replace("doc.page_count > MAX_PDF_PAGES",
                        "doc.page_count > SOME_OTHER_LIMIT")
    assert other != src, "注入锚点未命中（探针失效）"
    assert not _page_gate_in_open_scope(ast.parse(other)), \
        "页数闸不再引用 MAX_PDF_PAGES 却没红 —— 假绿"

    # 书写等价改写必须仍绿（反假红）
    for label, mutated in {
        "去空格": (src.replace("doc.page_count > MAX_PDF_PAGES",
                              "doc.page_count>MAX_PDF_PAGES")),
    }.items():
        assert mutated != src, "注入 %s 未命中（探针失效）" % label
        assert _page_gate_in_open_scope(ast.parse(mutated)), \
            "等价改写 %s 被误判为「闸缺失」—— 假红" % label
