"""B10 回归（R8+W）：P2 收尾批——提示词占位符 / 出题硬规则 / 密钥安全提示 / 上传内容闸。

- **S3-9**：声明了 `{占位符}` 的提示词必须以 `render_prompt` 渲染（原 `syllabus_extract.md`
  走 `load_prompt` 原样加载，占位符永不替换）。
- **S3-8**：`medgen.md` 必须有「答案无法由素材确定则**不出该题**」的硬规则。
- **S3-10 / S3-12**：DPAPI 回退明文要**常驻可见**；解密失败**不得回吐密文**。
- **M2-04**：上传要有魔数闸 + 压缩炸弹闸；PDF 要有页数闸。
"""

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
    """
    src = (ROOT / "medkit" / "core" / "syllabus.py").read_text(encoding="utf-8")
    # 只看**调用形式**，不看注释（注释里提到 load_prompt 是允许的）
    assert 'load_prompt("syllabus_extract.md")' not in src, "仍在裸 load_prompt 加载该提示词"
    assert "subject_text=" in src, "未把 subject_text 传给 render_prompt"
    assert 'render_prompt(\n' in src, "未用 render_prompt 渲染占位符"


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


def test_pdf_page_limit_exists():
    """M2-04：PDF 必须有页数闸（源码级守卫 + 常量存在）。"""
    src = (ROOT / "medkit" / "core" / "extract.py").read_text(encoding="utf-8")
    assert "MAX_PDF_PAGES" in src
    assert "doc.page_count > MAX_PDF_PAGES" in src, "页数闸未接到 fitz 打开处"
    from medkit.core import extract as ex
    assert ex.MAX_PDF_PAGES >= 100
