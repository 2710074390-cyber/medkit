"""复习手册 HTML：Markdown → 自包含页面（markdown 库，零 CDN）。

A4（2026-08 审计）：markdown 库默认放行原始 HTML（<img onerror> 等）——
渲染后用极小化 HTML 消毒器（标准库 HTMLParser，无第三方依赖）过滤，
仅保留子集安全标签与受控属性，其余标签剥除、文本转义。
"""

import html as html_mod
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

try:
    import markdown as md_lib
except ImportError:  # pragma: no cover
    md_lib = None

ALLOWED_TAGS = {
    "p", "br", "hr", "b", "strong", "i", "em", "u", "s", "del", "sub", "sup",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "caption",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "code", "pre",
    "span", "a", "div", "img",
}
ALLOWED_ATTRS = {"a": {"href", "title"}, "code": {"class"}, "pre": {"class"},
                 "span": {"class"}, "th": {"align"}, "td": {"align"},
                 "img": {"src", "alt", "title"}}

# A4（2026-08 补）：href scheme 白名单 — 仅 http/https + 页内锚点/同源相对链接；其余（javascript: 等）剥成纯文本。
# 先剥控制字符：HTML5 URL 解析会去除内嵌空白，'java\nscript:' 也可能被浏览器当作 javascript: 执行。
_HREF_SCHEMES = ("http", "https")
_HREF_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _safe_href(value: str | None) -> str | None:
    """返回白名单内的 href；否则返回 None（调用方剥成纯文本）。

    D10：放行页内锚点（#…）与同源相对路径，仍拒绝 javascript:/data: 与
    协议相对 URL（//evil.com 会跳外部，scheme 为 "" 但带 network location）。
    """
    if not value:
        return None
    href = _HREF_CTRL_RE.sub("", value).strip()
    if not href:
        return None
    scheme = urlsplit(href).scheme.lower()
    if scheme in _HREF_SCHEMES:
        return href
    if scheme == "" and not href.startswith("//"):
        return href
    return None


def _safe_img_src(value: str | None, out_dir: Path | None) -> str | None:
    """img src 白名单（C-17）：http(s) 与 data: 原样保留；相对路径仅在
    输出目录下存在同名文件时保留，否则返回 None（调用方给占位文字）。"""
    if not value:
        return None
    src = _HREF_CTRL_RE.sub("", value).strip()
    if not src:
        return None
    scheme = urlsplit(src).scheme.lower()
    if scheme in _HREF_SCHEMES or scheme == "data":
        return src
    if scheme == "" and not src.startswith("//"):
        if out_dir is not None and (Path(out_dir) / src).is_file():
            return src
    return None


class _Sanitizer(HTMLParser):
    """极小化白名单消毒：剥除脚本/事件属性/未知标签；文本转义。"""

    def __init__(self, out_dir: Path | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._out_dir = out_dir  # C-17：img 相对路径白名单的校验根目录
        self._block_until = ""  # 遇到 script/style 时置位，跳过全部内容
        self._no_link_depth = 0  # href 不合法 → 剥成纯文本（保留文本，不出 <a> 标签）

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style", "iframe", "object", "embed", "form", "input", "button", "link", "meta"):
            self._block_until = tag
            return
        if self._block_until:
            return
        if tag == "a":
            good: list[tuple[str, str]] = []
            for k, v in attrs:
                k = k.lower()
                if v is None:
                    continue
                if k == "href":
                    safe = _safe_href(v)
                    if safe is not None:
                        good.append(("href", safe))
                elif k in ("title",):
                    good.append((k, v))
            if good and any(k == "href" for k, _ in good):
                attr = "".join(f' {k}="{html_mod.escape(v, quote=True)}"' for k, v in good)
                self._out.append(f"<a{attr}>")
            else:  # 非白名单 scheme → 剥成纯文本
                self._no_link_depth += 1
            return
        if tag == "img":
            # D11：医学示意图（http/https/相对 src）放行；强制 alt；其余属性剥除。
            # C-17：相对路径 src 仅当输出目录下存在同名文件时保留，否则输出占位文字。
            src, alt, title, rel_missing = "", "", "", ""
            for k, v in attrs:
                k = k.lower()
                if v is None:
                    continue
                if k == "src":
                    safe = _safe_img_src(v, self._out_dir)
                    if safe is not None:
                        src = safe
                    else:
                        cand = _HREF_CTRL_RE.sub("", v).strip()
                        if cand and urlsplit(cand).scheme == "" and not cand.startswith("//"):
                            rel_missing = cand
                elif k == "alt":
                    alt = v
                elif k == "title":
                    title = v
            if src:
                attr = f' src="{html_mod.escape(src, quote=True)}" alt="{html_mod.escape(alt or "医学示意图", quote=True)}"'
                if title:
                    attr += f' title="{html_mod.escape(title, quote=True)}"'
                self._out.append(f"<img{attr}>")
            elif rel_missing:
                self._out.append("（图片相对路径未随附已省略：" + html_mod.escape(rel_missing) + "）")
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
        if tag == "a" and self._no_link_depth:
            self._no_link_depth -= 1
            return
        if tag in ALLOWED_TAGS:
            self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._block_until:      # D22：脚本/样式块内不输出任何自闭合标签
            return
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


def sanitize_html(raw: str, out_dir: Path | None = None) -> str:
    """白名单消毒：保留表格/列表/标题/粗体等结构与文本，剥除可执行内容。

    C-17：out_dir 用于校验 img 相对路径 src 是否随附（存在才保留）。
    """
    s = _Sanitizer(out_dir)
    try:
        s.feed(raw or "")
        s.close()
    except Exception:  # noqa: BLE001  任何异常 → 极端兜底：全转义
        return html_mod.escape(raw or "")
    return s.text()


_TABLE_RE = re.compile(r"<table>.*?</table>", re.S)
_HEADING_RE = re.compile(r"<h([23])>(.*?)</h\1>", re.S)
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;|&#x[0-9a-fA-F]+;")
_TAG_RE = re.compile(r"<[^>]+>")


def _anchor_slug(text_html: str) -> str:
    """从标题 HTML 提取纯文本，生成可用作 id 的 slug。"""
    plain = _TAG_RE.sub("", text_html)
    plain = _ENTITY_RE.sub("-", plain).strip()
    slug = re.sub(r"\W+", "-", plain).strip("-").lower()
    return slug or "section"


def _augment(body: str) -> str:
    """渲染后增强：表格包 .tw 滚动容器；标题加锚点 id 并生成目录(<details.toc>)。"""
    toc: list[tuple[str, str, str]] = []
    seen: dict[str, int] = {}

    def _uniq(hid: str) -> str:
        n = seen.get(hid, 0) + 1
        seen[hid] = n
        return f"{hid}-{n}" if n > 1 else hid

    def _replace_heading(m: re.Match) -> str:
        level = int(m.group(1))
        # IMP-08：页面外壳提供唯一 h1（标题）；正文 md 的 h1 降为 h2，避免跳级/重复 h1
        if level == 1:
            level = 2
        inner = m.group(2)
        hid = _uniq(_anchor_slug(inner))
        toc.append((str(level), hid, inner))
        return f'<h{level} id="{hid}">{inner}</h{level}>'

    if _HEADING_RE.search(body):
        body = _HEADING_RE.sub(_replace_heading, body)
        items = "".join(
            f'<li style="margin-left:{0 if lvl == "2" else 14}px"><a href="#{hid}">{txt}</a></li>'
            for lvl, hid, txt in toc
        )
        toc_block = (f'<details class="toc" open><summary>目录（{len(toc)}）</summary>'
                     f'<nav aria-label="目录"><ul>{items}</ul></nav></details>')
        body = toc_block + body

    return _TABLE_RE.sub(lambda m: '<div class="tw">' + m.group(0) + "</div>", body)


def review_to_html(md_text: str, title: str = "复习手册",
                   out_dir: Path | None = None) -> str:
    if md_lib is None:
        body = "<pre style='white-space:pre-wrap'>" + html_mod.escape(md_text) + "</pre>"
    else:
        raw = md_lib.markdown(md_text, extensions=["tables", "fenced_code", "nl2br"])
        body = _augment(sanitize_html(raw, out_dir))
    from .pagechrome import BASE_CSS, THEME_BTN, THEME_SCRIPT, THEME_VARS

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{html_mod.escape(title)} · MedKit</title>
<style>
{THEME_VARS}
{BASE_CSS}
main{{max-width:820px;margin:0 auto;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px 34px;font-size:var(--fs,14px)}}
h1{{font-size:22px;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:18px}}
h2,h3,h4{{scroll-margin-top:64px}}
h2{{font-size:1.25em;color:var(--acc);margin:22px 0 10px}}
h3{{font-size:1.08em;margin:16px 0 8px}}
p,li{{font-size:1em;line-height:1.9}}
ul,ol{{padding-left:24px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:.96em}}
th,td{{border:1px solid var(--line);padding:7px 10px;text-align:left}}
th{{background:rgba(56,189,248,.12);color:var(--acc)}}
.tw{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:10px 0}}
.tw table{{margin:0}}
details.toc{{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:20px;background:rgba(56,189,248,.05);position:sticky;top:8px;z-index:5;backdrop-filter:blur(4px)}}
details.toc nav ul{{list-style:none;padding-left:0;margin:6px 0 2px}}
details.toc li{{margin:3px 0}}
details.toc a{{color:var(--dim);text-decoration:none;font-size:.93em}}
details.toc a:hover{{color:var(--acc)}}
code{{background:rgba(125,211,252,.12);padding:1px 6px;border-radius:5px;font-size:.92em}}
blockquote{{border-left:3px solid var(--acc);padding:6px 14px;margin:10px 0;color:var(--dim)}}
/* v0.7 阅读体验：进度条 / 字号调节 / 回顶部 */
.rfprog{{position:fixed;top:0;left:0;height:3px;background:var(--acc);width:0;z-index:20;transition:width .15s linear}}
.rfbar{{position:fixed;top:12px;right:64px;z-index:9;display:flex;gap:5px;align-items:center}}
.rfbar button{{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:4px 10px;font-size:12.5px;cursor:pointer;font-family:inherit}}
.rfbar button:hover{{border-color:var(--acc);color:var(--acc)}}
.rfup{{position:fixed;bottom:18px;right:18px;z-index:9;width:38px;height:38px;border-radius:50%;border:1px solid var(--line);
  background:var(--card);color:var(--txt);font-size:16px;cursor:pointer;opacity:0;pointer-events:none;transition:opacity .2s}}
.rfup.show{{opacity:1;pointer-events:auto}}
@media (max-width:640px){{body{{padding:14px}} main{{padding:20px 18px}} .rfbar{{right:56px}}}}
@media print{{body{{background:#fff;color:#111}} main{{background:none;border:none}} .mini{{display:none}} .tw{{overflow:visible}} .tw table{{width:100%}} .rfprog{{display:none}} .rfbar{{display:none}} .rfup{{display:none}}}}
</style></head><body>
<div class="rfprog" id="rfprog"></div>
<div class="rfbar">
  <button onclick="rfFont(-1)" title="减小字号">A−</button>
  <button onclick="rfFont(1)" title="增大字号">A＋</button>
  <button onclick="rfFont(0)" title="恢复默认字号">默认</button>
</div>
<main><h1>{html_mod.escape(title)}</h1>{body}</main>
<button class="rfup" id="rfup" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="回到顶部">↑</button>
{THEME_BTN}
{THEME_SCRIPT}
<script>
/* 阅读进度 + 回顶部显隐 */
const prog=document.getElementById('rfprog'), up=document.getElementById('rfup');
window.addEventListener('scroll',()=>{{
  const h=document.documentElement;
  const pct=(h.scrollTop)/(h.scrollHeight-h.clientHeight||1)*100;
  if(prog) prog.style.width=pct+'%';
  if(up) up.classList.toggle('show', h.scrollTop>300);
}},{{passive:true}});
/* 字号调节：12~20px，本地记忆 */
function rfFont(d){{
  let fs=12;
  try{{fs=parseInt(localStorage.getItem('medkit-rf-font')||'14')||14;}}catch(e){{}}
  fs=Math.min(20,Math.max(12, fs+(d===0?14-fs:d)));
  try{{localStorage.setItem('medkit-rf-font',String(fs));}}catch(e){{}}
  document.querySelector('main').style.setProperty('--fs',fs+'px');
}}
try{{const f=parseInt(localStorage.getItem('medkit-rf-font')||'')||0;
  if(f) document.querySelector('main').style.setProperty('--fs',f+'px');}}catch(e){{}}
</script>
</body></html>"""
