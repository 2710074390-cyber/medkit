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


def test_all_prompts_with_placeholders_use_render_prompt():
    """S3-9 全目录守卫（R3-16 教训：不要只盯一个文件）。

    规则：提示词里出现 `{var}` 占位符 → 调用点必须用 `render_prompt`；
    只有**没有**占位符的提示词才允许 `load_prompt`。
    """
    py_files = list((ROOT / "medkit").rglob("*.py"))
    src_all = "\n".join(p.read_text(encoding="utf-8") for p in py_files)
    bad: list[str] = []
    for md in sorted(PROMPTS.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        # 只看「像变量」的占位符（排除 JSON 示例里的普通花括号）
        vars_ = {m for m in re.findall(r"\{([a-z][a-z0-9_]{2,})\}", text)}
        if not vars_:
            continue
        if f'load_prompt("{md.name}")' in src_all:
            bad.append(f"{md.name} 有占位符 {sorted(vars_)}，却存在裸 load_prompt 调用")
    assert not bad, "提示词占位符与加载方式不一致：\n" + "\n".join(bad)


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
