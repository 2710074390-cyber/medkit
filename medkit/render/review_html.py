"""复习手册 HTML：Markdown → 自包含页面（markdown 库，零 CDN）。

A4（2026-08 审计）：markdown 库默认放行原始 HTML（<img onerror> 等）——
渲染后用极小化 HTML 消毒器（标准库 HTMLParser，无第三方依赖）过滤，
仅保留子集安全标签与受控属性，其余标签剥除、文本转义。
"""

import html as html_mod
from html.parser import HTMLParser

try:
    import markdown as md_lib
except ImportError:  # pragma: no cover
    md_lib = None

ALLOWED_TAGS = {
    "p", "br", "hr", "b", "strong", "i", "em", "u", "s", "del", "sub", "sup",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "caption",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "code", "pre",
    "span", "a", "div",
}
ALLOWED_ATTRS = {"a": {"href", "title"}, "code": {"class"}, "pre": {"class"},
                 "span": {"class"}, "th": {"align"}, "td": {"align"}}


class _Sanitizer(HTMLParser):
    """极小化白名单消毒：剥除脚本/事件属性/未知标签；文本转义。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._block_until = ""  # 遇到 script/style 时置位，跳过全部内容

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style", "iframe", "object", "embed", "form", "input", "button", "link", "meta"):
            self._block_until = tag
            return
        if self._block_until:
            return
        if tag in ALLOWED_TAGS:
            good = [(k, v) for k, v in attrs
                    if k.lower() in ALLOWED_ATTRS.get(tag, set())
                    and v is not None and k.lower() not in ("onerror", "onclick", "onload")]
            attr = "".join(f' {k}="{html_mod.escape(v, quote=True)}"' for k, v in good)
            self._out.append(f"<{tag}{attr}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._block_until:
            if tag == self._block_until:
                self._block_until = ""
            return
        if tag in ALLOWED_TAGS:
            self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("br", "hr"):
            self._out.append(f"<{tag}>")
        else:
            self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if not self._block_until:
            self._out.append(html_mod.escape(data))

    def handle_entityref(self, name: str) -> None:
        if not self._block_until:
            self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._block_until:
            self._out.append(f"&#{name};")

    def text(self) -> str:
        return "".join(self._out)


def sanitize_html(raw: str) -> str:
    """白名单消毒：保留表格/列表/标题/粗体等结构与文本，剥除可执行内容。"""
    s = _Sanitizer()
    try:
        s.feed(raw or "")
        s.close()
    except Exception:  # noqa: BLE001  任何异常 → 极端兜底：全转义
        return html_mod.escape(raw or "")
    return s.text()


def review_to_html(md_text: str, title: str = "复习手册") -> str:
    if md_lib is None:
        body = "<pre style='white-space:pre-wrap'>" + html_mod.escape(md_text) + "</pre>"
    else:
        raw = md_lib.markdown(md_text, extensions=["tables", "fenced_code", "nl2br"])
        body = sanitize_html(raw)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{html_mod.escape(title)} · MedKit</title>
<style>
:root{{--bg:#0a1226;--card:rgba(18,32,62,.85);--line:rgba(120,180,255,.18);--txt:#dbeafe;--dim:#8aa4cc;--acc:#38bdf8;--bgc1:#12305e;--bgc2:#060d1e}}
:root[data-theme=light]{{--bg:#f2f6fb;--card:#ffffff;--line:rgba(30,80,140,.22);--txt:#12233d;--dim:#5a6b85;--acc:#0e6fb8;--bgc1:#dbe9f8;--bgc2:#eef4fb}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:radial-gradient(900px 500px at 20% -10%,var(--bgc1),transparent 55%),linear-gradient(160deg,var(--bg),var(--bgc2));color:var(--txt);padding:28px}}
main{{max-width:820px;margin:0 auto;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px 34px}}
h1{{font-size:22px;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:18px}}
h2{{font-size:17px;color:var(--acc);margin:22px 0 10px}}
h3{{font-size:15px;margin:16px 0 8px}}
p,li{{font-size:14px;line-height:1.9}}
ul,ol{{padding-left:24px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px}}
th,td{{border:1px solid var(--line);padding:7px 10px;text-align:left}}
th{{background:rgba(56,189,248,.12);color:var(--acc)}}
code{{background:rgba(125,211,252,.12);padding:1px 6px;border-radius:5px;font-size:13px}}
blockquote{{border-left:3px solid var(--acc);padding:6px 14px;margin:10px 0;color:var(--dim)}}
@media print{{body{{background:#fff;color:#111}} main{{background:none;border:none}}}}
</style></head><body><main>{body}</main>
<button class="mini" style="position:fixed;top:12px;right:12px;z-index:9;font-size:14px;padding:4px 12px;background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;cursor:pointer" onclick="toggleTheme()" title="切换亮/暗主题">🌓</button>
<script>
if(localStorage.getItem("medkit-theme")==="light")document.documentElement.dataset.theme="light";
function toggleTheme(){{const cur=document.documentElement.dataset.theme==="light"?"dark":"light";
document.documentElement.dataset.theme=cur;localStorage.setItem("medkit-theme",cur);}}
</script>
</body></html>"""
