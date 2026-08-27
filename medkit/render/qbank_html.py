"""题库产物：qbank.md（分组导出） + qbank.html（自包含，可折叠+打印）
         + 押题卷.html（交互答题：checkbox 多选 / 集合判分 / 续答 / 答题卡 / 错题重练）
         + anki_export.txt（U8：Anki 文本导入）。

安全（A4，2026-08 审计）：押题卷 JS 所有插值经 esc() 转义；产物页自带明暗主题切换（V1）。
"""

import base64
import html as html_mod
import json
from pathlib import Path
from typing import Any, Optional

TYPE_LABELS = {"A1": "A1 型 · 单选", "A2": "A2 型 · 病例单选", "X": "X 型 · 多选",
               "B1": "B1 型 · 共用选项", "A3": "A3 型 · 案例单选", "A4": "A4 型 · 案例单选"}
LETTERS = "ABCDEFGHIJ"  # 渲染上限 10 个选项，超出部分由渲染前终检剔除（D2）


# ---------------------------------------------------------------- WP-04 图/表渲染
_MEDIA_CSS = """
.fig{margin:10px 0;text-align:center;break-inside:avoid}
.fig img{max-width:100%;max-height:420px;border-radius:10px;border:1px solid var(--line);background:#fff}
.fig figcaption{font-size:12px;color:var(--dim);margin-top:4px}
.qb table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px;break-inside:avoid;overflow-x:auto;display:block}
.qb table th,.qb table td{border:1px solid var(--line);padding:5px 9px;text-align:left}
.qb table th{background:var(--card2);color:var(--txt);font-weight:600}
@media print{.fig img{max-height:260px}.qb table{display:table;font-size:11.5px}}
"""


def render_media(q: dict[str, Any], image_index: Optional[dict[str, Any]] = None) -> str:
    """题目的图像（base64 内嵌，单文件可移动）+ 表格（markdown → <table>，安全白名单）。"""
    out: list[str] = []
    ref = str(q.get("image_ref") or "")
    if ref and image_index:
        info = image_index.get(ref)
        if info:
            p = info.get("path") if isinstance(info, dict) else info
            cap = ((info.get("caption") if isinstance(info, dict) else "") or "")
            try:
                data = Path(p).read_bytes()
                import mimetypes
                mime = mimetypes.guess_type(str(p))[0] or "image/png"
                b64 = base64.b64encode(data).decode("ascii")
                out.append(f'<figure class="fig"><img src="data:{mime};base64,{b64}" '
                           f'alt="{html_mod.escape(cap)}"><figcaption>图 {html_mod.escape(ref)}'
                           + (f" · {html_mod.escape(cap)}" if cap else "") + "</figcaption></figure>")
            except Exception:  # noqa: BLE001  文件缺失/读取失败 → 跳过图（题保留）
                pass
    tbl = str(q.get("data_table") or "")
    if tbl.strip():
        try:
            import markdown as _md

            from .review_html import sanitize_html

            out.append(sanitize_html(_md.markdown(tbl, extensions=["tables"])))
        except Exception:  # noqa: BLE001
            pass
    return "".join(out)


def _effective_options(q: dict[str, Any]) -> list[str]:
    """实际渲染选项：B1 组题共享选项在 group 字段（S3：自身 options 可为空）。"""
    opts = q.get("options") or []
    if not opts and q.get("group_kind") == "option_group":
        grp = q.get("group") or {}
        if isinstance(grp, dict):
            opts = grp.get("options") or []
    return [o for o in opts if isinstance(o, str)]


def _case_blocks(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按（案例组 / 选项组 / 单题）有序分组（S3）。返回 [{key, kind, stem, options, items}]。"""
    ordered = sorted(questions, key=lambda q: (q.get("type", "") or "", str(q.get("id", ""))))
    blocks: list[dict[str, Any]] = []
    index: dict[tuple, int] = {}
    for q in ordered:
        gk = q.get("group_kind")
        key = None
        if gk == "case" and q.get("case_id"):
            key = ("case", q.get("case_id"))
        elif gk == "option_group" and isinstance(q.get("group"), dict):
            key = ("og", tuple(str(o) for o in (q["group"].get("options") or [])))
        if key is None:
            blocks.append({"key": None, "kind": "single", "stem": "",
                           "options": [], "items": [q]})
            continue
        if key not in index:
            grp = q.get("group") if gk == "option_group" and isinstance(q.get("group"), dict) else {}
            blocks.append({"key": key, "kind": gk or "single", "stem": q.get("case_stem") or "",
                           "options": (grp or {}).get("options") or [],
                           "items": []})
            index[key] = len(blocks) - 1
        blocks[index[key]]["items"].append(q)
    return blocks


def _esc(s: Any) -> str:
    """JS 侧转义（押题卷内嵌脚本使用）。"""
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _esc_anki(s: Any) -> str:
    """Anki 字段转义：在 _esc 基础上把换行/制表符转成 Anki 可读形式（防 LLM 解析含换行损坏文件）。"""
    return _esc(s).replace("\n", "<br>").replace("\t", " ")


def _md_media(q: dict[str, Any]) -> list[str]:
    """MD 版图/表题：data_table 渲染为 Markdown 表格；图片标注（base64 不内嵌文本，提示见 HTML 版）。"""
    out: list[str] = []
    tbl = str(q.get("data_table") or "").strip()
    if tbl:
        out.append("📋 **附数据表格**：")
        out.append(tbl if "\n" in tbl else tbl.replace("|", " | "))
        out.append("")
    if q.get("image_ref"):
        out.append(f"🖼 **本题含图片**（{q.get('image_ref')}）：请查看 HTML 版（题库.html）中的图。")
        out.append("")
    return out


def _md_question(q: dict[str, Any], prefix: str = "###", show_options: bool = True) -> list[str]:
    out = [f"{prefix} {q.get('id')} · {TYPE_LABELS.get(q.get('type'), q.get('type', ''))} · {q.get('bloom', '')}"]
    out.append(f"**{q.get('subtopic', '')}**")
    out.append(q.get("question", ""))
    if show_options:
        for i, opt in enumerate(_effective_options(q)):
            out.append(f"- {LETTERS[i]}. {opt}")
    out += _md_media(q)
    out.append(f"**✅ 答案：{q.get('answer', '')}**")
    out.append(f"💡 {q.get('analysis', '')}")
    out.append("")
    return out


def export_md(questions: list[dict[str, Any]], title: str = "题库") -> str:
    """Markdown：案例/选项组按组折叠（案例题干只出现一次）；单题平铺。"""
    lines = [f"# {title}", ""]
    for b in _case_blocks(questions):
        if b["kind"] == "case":
            lines.append(f"## 📋 案例 {b['key'][1]}")
            lines.append(f"**案例题干**：{b['stem']}")
            lines.append(f"> 本案例 {len(b['items'])} 道子题")
            lines.append("")
            for q in b["items"]:
                lines += _md_question(q, prefix="###")
        elif b["kind"] == "option_group":
            lines.append("## 🧩 选项组（B1 共享选项）")
            for i, o in enumerate(b["options"]):
                lines.append(f"- {LETTERS[i]}. {o}")
            lines.append("")
            for q in b["items"]:
                lines += _md_question(q, prefix="###", show_options=False)
        else:
            lines += _md_question(b["items"][0], prefix="###")
    return "\n".join(lines)


def export_anki(questions: list[dict[str, Any]], title: str = "题库") -> str:
    """Anki 文本导入：正面=题干+选项，反面=答案+解析；字段间 Tab 分隔，行内换行用 <br>。

    S3：案例子题题干带「【案例】题干」前缀（扁平卡不丢组上下文）；B1 用共享选项。
    """
    lines = ["#separator:tab", "#html:true", ""]
    for q in sorted(questions, key=lambda x: str(x.get("id", ""))):
        stem = str(q.get("case_stem") or "")
        question = str(q.get("question") or "")
        front_question = (f"【案例】{stem}<br>" + question) if stem else question
        front = [f"<b>Q{q.get('id', '')}</b> · {_esc_anki(q.get('type', ''))}型 · {_esc_anki(q.get('bloom', ''))} · "
                 f"{_esc_anki(q.get('subtopic', ''))}",
                 _esc_anki(front_question)]
        for i, o in enumerate(_effective_options(q)):
            front.append(f"{LETTERS[i]}. {_esc_anki(o)}")
        back = [f"✅ 答案：<b>{_esc_anki(q.get('answer', ''))}</b>",
                f"💡 {_esc_anki(q.get('analysis', ''))}"]
        lines.append("\t".join(["<br>".join(front), "<br>".join(back)]))
    return "\n".join(lines) + "\n"


def _html_sub(q: dict[str, Any], show_options: bool = True) -> str:
    """案例/选项组内的子题（不折叠，逐题展示）。"""
    opts = ""
    if show_options:
        opts = "<ul>" + "".join(
            f"<li><b>{LETTERS[i]}</b> · {html_mod.escape(str(o))}</li>"
            for i, o in enumerate(_effective_options(q))) + "</ul>"
    return (f'<div class="qsub"><p><b>{html_mod.escape(q.get("id", ""))}</b> · '
            f'{html_mod.escape(str(q.get("type", "")))} · '
            f'{html_mod.escape(str(q.get("question", "")))}</p>{opts}'
            f'<p class="ans">✅ 答案：<b>{html_mod.escape(str(q.get("answer", "")))}</b></p>'
            f'<p class="ana">💡 {html_mod.escape(str(q.get("analysis", "")))}</p></div>')


def export_html(questions: list[dict[str, Any]], title: str = "题库",
                image_index: Optional[dict[str, Any]] = None) -> str:
    """题库 HTML：案例/选项组按组折叠（S3），单题保持原 <details class=q data-type> 结构。
    v0.7.1：搜索 + 全部题型过滤 + 计数 + 窄屏适配。WP-04：图像（base64）+ 表格渲染。
    """
    items = []
    for b in _case_blocks(questions):
        if b["kind"] == "case":
            first = b["items"][0]
            kw = (str(first.get("id", "")) + " " + str(b["stem"]) + " " +
                  " ".join(str(q.get("question", "")) + " " + str(q.get("subtopic", "")) for q in b["items"]))
            items.append(
                f'<details class="q case" data-type="{html_mod.escape(str(first.get("type", "")))}" '
                f'data-group="case" data-blm="{html_mod.escape(str(first.get("bloom", "")))}" '
                f'data-kw="{html_mod.escape(kw.lower())}">'
                f'<summary class="qs"><span class="tag">📋 案例 '
                f'{html_mod.escape(str(b["key"][1]))}</span> '
                f'{html_mod.escape(TYPE_LABELS.get(str(first.get("type", "")), ""))} · '
                f'{len(b["items"])} 道子题 · 点击展开案例题干</summary>'
                f'<div class="qb"><p><b>案例题干</b>：{html_mod.escape(str(b["stem"]))}</p>'
                + render_media(first, image_index)
                + "".join(_html_sub(q) for q in b["items"]) + '</div></details>')
        elif b["kind"] == "option_group":
            shared = "<ul>" + "".join(
                f"<li><b>{LETTERS[i]}</b> · {html_mod.escape(str(o))}</li>"
                for i, o in enumerate(b["options"])) + "</ul>"
            kw = " ".join(str(q.get("id", "")) + " " + str(q.get("question", "")) + " " + str(q.get("subtopic", ""))
                          for q in b["items"]) + " B1 选项组 共享选项"
            items.append(
                f'<details class="q case" data-type="B1" data-group="og" data-blm="{html_mod.escape(str(b["items"][0].get("bloom", "")))}" data-kw="{html_mod.escape(kw.lower())}">'
                f'<summary class="qs"><span class="tag">🧩 选项组（B1）</span>'
                f'（{len(b["items"])} 题共享下列选项）</summary>'
                f'<div class="qb">{shared}'
                + "".join(_html_sub(q, show_options=False) for q in b["items"]) + '</div></details>')
        else:
            q = b["items"][0]
            opts = "".join(
                f"<li><b>{LETTERS[i]}</b> · {html_mod.escape(str(o))}</li>"
                for i, o in enumerate(_effective_options(q)))
            kw = (str(q.get("id", "")) + " " + str(q.get("type", "")) + " " + str(q.get("bloom", ""))
                  + " " + str(q.get("subtopic", "")) + " " + str(q.get("question", "")))
            items.append(
                f'<details class="q" data-type="{html_mod.escape(str(q.get("type", "")))}" '
                f'data-group="single" data-blm="{html_mod.escape(str(q.get("bloom", "")))}" '
                f'data-kw="{html_mod.escape(kw.lower())}">'
                f'<summary class="qs">'
                f'<span class="tag">{html_mod.escape(str(q.get("type", "")))}</span> '
                f'<span class="tag b">{html_mod.escape(str(q.get("bloom", "")))}</span> '
                f'{html_mod.escape(str(q.get("question", ""))[:60])}…</summary>'
                f'<div class="qb">{render_media(q, image_index)}<p><b>{html_mod.escape(str(q.get("subtopic", "")))}</b> · '
                f'{html_mod.escape(str(q.get("question", "")))}</p><ul>{opts}</ul>'
                f'<p class="ans">✅ 答案：<b>{html_mod.escape(str(q.get("answer", "")))}</b></p>'
                f'<p class="ana">💡 {html_mod.escape(str(q.get("analysis", "")))}</p></div></details>')
    return _page(title, f"""
<h1>{html_mod.escape(title)}</h1>
<p class="meta">共 {len(questions)} 题 · 答案默认隐藏，点击题目展开查看 · <button class="mini" onclick="window.print()">🖨 打印</button> ·
<span id="qcount" role="status" aria-live="polite"></span></p>
<div class="filters" role="group" aria-label="筛选工具">
  <span role="group" aria-label="按题型过滤">
  <button data-t="" data-label="全部" class="on" onclick="ft('',this)">全部</button>
  <button data-t="A1" data-label="A1 单选" onclick="ft('A1',this)">A1 单选</button>
  <button data-t="A2" data-label="A2 病例" onclick="ft('A2',this)">A2 病例</button>
  <button data-t="og" data-label="B1 选项组" onclick="ft('og',this)">B1 选项组</button>
  <button data-t="case" data-label="A3·A4 案例" onclick="ft('case',this)">A3·A4 案例</button>
  <button data-t="X" data-label="X 多选" onclick="ft('X',this)">X 多选</button>
  </span>
  <select id="qbloom" aria-label="按认知层级过滤">
    <option value="">全部层级</option><option value="记忆">记忆</option><option value="理解">理解</option>
    <option value="应用">应用</option><option value="创造">创造</option>
  </select>
  <input id="qsearch" type="search" aria-label="搜索题干 / 考点 / 章节" placeholder="🔍 搜索题干 / 考点 / 章节…" oninput="ftq(this.value)">
  <button id="qreset" class="mini" title="清除全部筛选" aria-label="清除全部筛选" onclick="resetFilter()">重置</button>
</div>
<div class="qpager" id="qpager" style="margin:10px 0 2px;display:flex;align-items:center;gap:8px;font-size:13px">
  <button class="mini" onclick="pg(-1)" aria-label="上一页">‹ 上一页</button>
  <span id="pginfo" role="status"></span>
  <button class="mini" onclick="pg(1)" aria-label="下一页">下一页 ›</button>
</div>
{''.join(f'<div class="qpage" data-pg="{i}" style="display:{"block" if i == 0 else "none"}">' + "".join(items[i*50:(i+1)*50]) + '</div>' for i in range(0, (len(items)+49)//50))}
<script>
let FT_T='',FT_B='',FT_Q='';
const PS=50;
let PAGES=Math.max(1,Math.ceil(document.querySelectorAll('details.q').length/PS));
let PG=0;
function renderPg(){{
  document.querySelectorAll('.qpage').forEach(p=>{{p.style.display=(p.dataset.pg==String(PG))?'':'none';}});
  const info=document.getElementById('pginfo');
  if(info) info.textContent='第 '+(PG+1)+' / '+PAGES+' 页';
  const pr=document.getElementById('qpager');
  if(pr) pr.style.display=(FT_T||FT_B||FT_Q)?'none':'flex';
}}
function pg(d){{
  PG=Math.max(0,Math.min(PAGES-1,PG+d));
  renderPg();
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
function saveFilter(){{
  try{{localStorage.setItem('medkitQbFilter',JSON.stringify({{t:FT_T,b:FT_B,q:FT_Q}}));}}catch(e){{}}
}}
function ft(t,btn){{
  FT_T=t;
  document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('on'));
  if(btn){{
    document.querySelectorAll('.filters button').forEach(b=>{{
      if(b.getAttribute('data-t')===t) b.classList.add('on');
    }});
  }}
  saveFilter(); apply();
}}
function ftq(v){{FT_Q=(v||'').toLowerCase().trim();saveFilter();apply();}}
function ftb(v){{FT_B=(v||'');saveFilter();apply();}}
function resetFilter(){{
  FT_T='';FT_B='';FT_Q='';
  document.querySelectorAll('.filters button').forEach(b=>{{b.classList.remove('on');
    if(b.getAttribute('data-t')==='') b.classList.add('on');}});
  const bs=document.getElementById('qbloom'); if(bs) bs.value='';
  const qs=document.getElementById('qsearch'); if(qs) qs.value='';
  try{{localStorage.removeItem('medkitQbFilter');}}catch(e){{}}
  apply();
}}
function apply(){{
  let n=0;
  document.querySelectorAll('details.q').forEach(d=>{{
    const okT=!FT_T||(FT_T==='case'&&d.dataset.group==='case')||(FT_T==='og'&&d.dataset.group==='og')||(d.dataset.type||'')===FT_T;
    const okB=!FT_B||(d.dataset.blm||'')===FT_B;
    const okQ=!FT_Q||(d.dataset.kw||'').indexOf(FT_Q)>-1;
    d.style.display=(okT&&okB&&okQ)?'':'none';
    if(okT&&okB&&okQ) n++;
  }});
  // 过滤/搜索激活 → 跨页显示全部匹配（分页条隐藏）；未过滤 → 回到当前页
  const filtered=!!(FT_T||FT_B||FT_Q);
  document.querySelectorAll('.qpage').forEach(p=>{{p.style.display=filtered?'':'none';}});
  if(!filtered) renderPg();
  const c=document.getElementById('qcount');
  if(c) c.textContent=(FT_T||FT_B||FT_Q)?('显示 '+n+' / '+document.querySelectorAll('details.q').length+' 题'):'';
}}
function setCounts(){{
  const c={{}};
  document.querySelectorAll('details.q').forEach(d=>{{
    const k=d.dataset.group==='case'?'case':d.dataset.group==='og'?'og':(d.dataset.type||'');
    c[k]=(c[k]||0)+1;
  }});
  document.querySelectorAll('.filters button').forEach(b=>{{
    const k=b.getAttribute('data-t');
    b.textContent=b.getAttribute('data-label')+(c[k]?(' · '+c[k]):'');
  }});
}}
setCounts();
renderPg();
/* 记忆上次过滤状态（题型/Bloom/关键词），下次打开保持不变 */
try{{
  const saved=JSON.parse(localStorage.getItem('medkitQbFilter')||'null');
  if(saved&&(saved.t||saved.b||saved.q)){{
    FT_T=saved.t||'';FT_B=saved.b||'';FT_Q=saved.q||'';
    document.querySelectorAll('.filters button').forEach(b=>{{
      if(b.getAttribute('data-t')===FT_T) b.classList.add('on');
      else b.classList.remove('on');
    }});
    const bs=document.getElementById('qbloom'); if(bs) bs.value=FT_B||'';
    const qs=document.getElementById('qsearch'); if(qs) qs.value=FT_Q||'';
    apply();
  }}
}}catch(e){{}}
document.getElementById('qbloom').addEventListener('change',function(){{ftb(this.value);}});
</script>""", extras="qbank")


def _questions_json_for_page(questions: list[dict[str, Any]],
                             image_index: Optional[dict[str, Any]] = None) -> str:
    import json
    compact = []
    for q in questions:
        gk = q.get("group_kind")
        label = ""
        if gk == "case" and q.get("case_id"):
            label = f"📋 案例 {q.get('case_id')}　{q.get('case_stem', '')[:80]}"
        elif gk == "option_group":
            label = "🧩 选项组（B1 共享选项）"
        compact.append({"type": q.get("type"), "bloom": q.get("bloom"),
                        "subtopic": q.get("subtopic", ""), "question": q.get("question", ""),
                        "options": _effective_options(q), "answer": q.get("answer", ""),
                        "analysis": q.get("analysis", ""), "case_label": label,
                        "case_id": q.get("case_id", ""),
                        "case_stem": q.get("case_stem", ""),
                        "case_order": q.get("case_order", 0),
                        "media": render_media(q, image_index)})
    return json.dumps(compact, ensure_ascii=False).replace("</", "<\\/")


def export_paper_html(questions: list[dict[str, Any]], title: str = "押题卷", *,
                      pid: str = "", subject: str = "",
                      image_index: Optional[dict[str, Any]] = None) -> str:
    """交互押题卷（I3 练习化）：
    - X 型 checkbox + 集合判分（A1 修复）
    - localStorage 实时保存作答 + 重开续答 + 答题卡 + 计时器（练习计时；可开启限时模式→到点自动判分）
    - 判分后「错题重练」（localStorage 错题集，可返回全卷）
    - 判分后「同步错题到学习中心错题本」（v0.7，POST /api/library/mistakes/sync-paper）
    - 所有插值经 esc()（A4 修复）
    - 无选项题剔除（防御：不参与判分与计数，页面提示数量）
    """
    no_opt = [q for q in questions if not _effective_options(q)]
    questions = [q for q in questions if _effective_options(q)]
    dropped_n = len(no_opt)
    qs = _questions_json_for_page(questions, image_index)
    pid_json = json.dumps(pid or "")
    subj_json = json.dumps(subject or "")
    return _page(title, f"""
<h1>{html_mod.escape(title)}</h1>
<p class="meta">共 {len(questions)} 题 · 作答自动保存 · <button class="mini" onclick="window.print()">🖨 打印</button>
  <label style="margin-left:12px;font-size:12.5px"><input type="checkbox" id="ctMode" onchange="ctToggle()"> 限时模式</label>
  <input type="number" id="ctMin" value="60" min="5" max="240" style="width:56px;margin-left:4px;padding:1px 4px;font-size:12.5px" title="限时分钟数（到点自动判分）"> 分钟
  <span class="hint" style="font-size:11.5px;margin-left:6px">默认练习计时（不锁定），自行提交判分</span>
  <span id="timer" style="float:right"></span></p>
{f'<p class="hint" style="margin:4px 0 0">⚠️ {dropped_n} 题缺选项，已从本卷剔除（不参与判分）。</p>' if dropped_n else ''}
<div id="quiz"><span class="spin"></span>加载中…</div>
<script>
let QUESTIONS = {qs};
const ORIG = QUESTIONS.slice();
const LETTERS = "ABCDEFGHIJ";
const TL = {{A1:"A1 单选",A2:"A2 病例",X:"X 多选",B1:"B1 选项组",A3:"A3 案例",A4:"A4 案例"}};
const PAPER_PID = {pid_json};
const PAPER_SUBJECT = {subj_json};
const KEY = "medkit-paper-" + (PAPER_PID || location.pathname.split('/').pop());
const RETRY_KEY = KEY + "-retry";
const WRONG_POOL = {{}};   // v0.7：判分用错题集，供「同步到错题本」
let secs = 0;
let judged = false;   // 判分防重入：提交一次后再次点击不重复计分/铺解析
let showCt = false;   // 限时模式开关（当前页面生命周期内）

function esc(s){{return String(s??"").replace(/[&<>"']/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));}}
function loadState(){{try{{return JSON.parse(localStorage.getItem(KEY)||"null")}}catch(e){{return null}}}}
function saveState(s){{try{{localStorage.setItem(KEY,JSON.stringify(s));}}catch(e){{}}}}
function clearState(){{try{{localStorage.removeItem(KEY);}}catch(e){{}}}}

function readAnswer(i){{
  if(QUESTIONS[i].type==="X"){{
    return [...document.querySelectorAll('input[name="q'+i+'"]:checked')]
      .map(e=>e.value).sort().join("");
  }}
  const sel=document.querySelector('input[name="q'+i+'"]:checked');
  return sel?sel.value:"";
}}
function writeAnswer(i,val){{
  const set=new Set((val||"").split(""));
  document.querySelectorAll('input[name="q'+i+'"]').forEach(e=>e.checked=set.has(e.value));
}}
function answersEqual(a,b){{
  if(typeof a!=="string"||typeof b!=="string") return false;
  // 空白归一化：答案键可能带空格（如"B D "/"B, D"），归一后再排序比较
  const sa=a.replace(/[\s,，、]+/g,"").split("").sort().join("");
  const sb=b.replace(/[\s,，、]+/g,"").split("").sort().join("");
  return sa.length>0 && sa===sb;
}}

function render(){{
  const box=document.getElementById('quiz');
  let h='';
  h+='<div class="hint" id="hisline" style="margin-bottom:8px"></div>'
     +'<div class="sheet"><div class="grid" id="grids"></div>'
     +'<span class="asw" id="asw"></span><div class="sheetactions"></div></div>';
  let lastCase='';
  QUESTIONS.forEach((q,i)=>{{
    if(!q.options||!q.options.length) return;
    if(q.case_label && q.case_label!==lastCase){{
      h+='<div class="casebar" data-case="'+esc(q.case_id)+'">'+esc(q.case_label)+'</div>';
      lastCase=q.case_label;
    }}
    if(q.media) h+=q.media;
    h+='<div class="q" id="q'+i+'"><p class="qs"><span class="tag">'+esc(TL[q.type]||q.type)+'</span>'
      +'<span class="tag b">'+esc(q.bloom)+'</span> <b>'+(i+1)+'.</b> '+esc(q.question)+'</p>';
    h+='<fieldset class="optfs"><legend class="sr">第 '+(i+1)+' 题选项</legend>';
    q.options.forEach((o,j)=>{{
      const t=q.type==="X"?"checkbox":"radio";
      const oid='oid'+i+'_'+LETTERS[j];
      h+='<label class="opt" for="'+oid+'"><input type="'+t+'" id="'+oid+'" name="q'+i+'" value="'+LETTERS[j]+'"> '
        +LETTERS[j]+' · '+esc(o)+'</label>';
    }});
    h+='</fieldset>';
    h+='<button class="mark gray mini" onclick="mark('+i+')">旗</button></div>';
  }});
  h+='<div class="btns"><button class="act" onclick="grade()">提交判分</button>'
     +'<button class="gray" onclick="resetAll()">清空重做</button></div><div id="res" role="status" aria-live="polite" tabindex="-1"></div>';
  box.innerHTML=h;
  paintAnswers();
  buildGrid();
  updateAnswered();
  showHistory();
  // 续答归还提示：检测到上次作答（跨会话恢复）→ 提示已恢复 + 可清空重来
  const st=loadState()||{{}};
  const savedN=(st.answers&&Object.keys(st.answers).length)||0;
  if(savedN>0 && !judged){{
    const note=document.createElement('div');
    note.id='resume_note';
    note.className='banner good';
    note.innerHTML='🔄 已恢复上次作答（'+savedN+' / '+QUESTIONS.length+' 题）· 计时延续 · 想要重来？'
      +'<button class="mini" style="margin-left:8px" onclick="resetAll()">清空重做</button>';
    box.insertBefore(note, box.firstChild);
  }}
}}

function mark(i){{
  const st=loadState()||{{answers:{{}},marked:null,t0:null}};
  st.marked=(st.marked===i?null:i);
  saveState(st);
  document.querySelectorAll('.q').forEach((d,j)=>d.classList.toggle('flagged', j===st.marked));
  buildGrid();
}}
function paintAnswers(){{
  const st=loadState()||{{answers:{{}},marked:null,t0:null}};
  QUESTIONS.forEach((q,i)=>{{
    if(st.answers && st.answers[i]!=null) writeAnswer(i,st.answers[i]);
    const el=document.getElementById('q'+i);
    if(el&&st.marked===i) el.classList.add('flagged');
  }});
}}
function collectAnswers(){{
  const st=loadState()||{{answers:{{}},marked:null,t0:null}};
  QUESTIONS.forEach((q,i)=>{{ const v=readAnswer(i); if(v) st.answers[i]=v; }});
  if(!st.t0) st.t0=Date.now();
  saveState(st); buildGrid(); updateAnswered();
}}
function updateAnswered(){{
  const el=document.getElementById('asw'); if(!el) return;
  const st=loadState()||{{answers:{{}}}};
  let n=0; QUESTIONS.forEach((q,i)=>{{ if(st.answers&&st.answers[i]) n++; }});
  el.textContent='已答 '+n+' / '+QUESTIONS.length;
}}
function showHistory(){{
  const el=document.getElementById('hisline'); if(!el) return;
  try{{
    const H=JSON.parse(localStorage.getItem(KEY+'-his')||'[]');
    if(!H.length||judged){{ el.textContent=''; return; }}
    const pct=h=>Math.round(h.score*100/(h.total||1));
    let best=H[0]; H.forEach(h=>{{ if(pct(h)>pct(best)) best=h; }});
    const last=H[0];
    el.innerHTML='📈 上次 '+last.score+'/'+last.total+'（'+pct(last)+' 分）· 用时 '+fmtT(last.secs||0)
      +' · 最佳 '+best.score+'/'+best.total+'（'+pct(best)+' 分）· '+esc(last.ts||'');
  }}catch(e){{ el.textContent=''; }}
}}
function buildGrid(){{
  const g=document.getElementById('grids'); if(!g) return;
  const st=loadState()||{{answers:{{}},marked:null}};
  let h='';
  QUESTIONS.forEach((q,i)=>{{
    const a=st.answers&&st.answers[i];
    const tip='第 '+(i+1)+' 题 · '+(a?'已答':'未答')+(st.marked===i?' · 已标记':'');
    h+='<span class="cell'+(a?' done':'')+(st.marked===i?' mk':'')+'" title="'+tip+'" aria-label="'+tip+'" role="button" tabindex="0" onclick="jump('+i+')">'+(i+1)+'</span>';
  }});
  g.innerHTML=h;
}}
function jump(i){{document.getElementById('q'+i)?.scrollIntoView({{behavior:'smooth',block:'center'}});}}

function grade(){{
  if(judged) return;   // 判分防重入
  // 未答提醒：防漏答（有未答时确认后再判）
  const st0=loadState()||{{answers:{{}}}};
  let unanswered=0;
  QUESTIONS.forEach((q,i)=>{{ if(!(st0.answers&&st0.answers[i])) unanswered++; }});
  if(unanswered>0 && !confirm('还有 '+unanswered+' 题未作答，确认提交判分？')) return;
  judged = true;
  collectAnswers();
  let score=0, wrong=[];
  const st=loadState()||{{answers:{{}}}};
  const caseScore={{}};
  QUESTIONS.forEach((q,i)=>{{
    const a=(st.answers&&st.answers[i])||"";
    const right=answersEqual(a,q.answer);
    if(right) score++;
    else {{
      wrong.push(i+1);
      // 去重键改用题目 id（案例组子题题干共享前 40 字会被误并；id 唯一稳定）
      WRONG_POOL[String(q.id||"")||String(q.question||"").slice(0,40)] = {{
        id: q.id||"", subject: PAPER_SUBJECT, source:"paper",
        sid: q.sid||"", question: q.question||"", options: q.options||[],
        answer: q.answer||"", analysis: q.analysis||"",
        subtopic: q.subtopic||"", bloom: q.bloom||"", user_answer: a
      }};
    }}
    const ck=q.case_id||"";
    if(ck){{ const cs=caseScore[ck]||={{n:0,t:0}}; cs.n++; if(right) cs.t++; caseScore[ck]=cs; }}
  }});
  const total=QUESTIONS.length;
  try{{localStorage.setItem(RETRY_KEY,JSON.stringify(
    {{title:"错题重练",questions:wrong.map(i=>QUESTIONS[i-1]).filter(Boolean)}}));}}catch(e){{}}
  // 成绩留存：最近 10 次（跨会话），供下次打开展示「上次/最佳」
  try{{
    const H=JSON.parse(localStorage.getItem(KEY+'-his')||'[]');
    H.unshift({{score:score,total:total,secs:secs,
      ts:new Date().toLocaleString('zh-CN',{{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}})}});
    localStorage.setItem(KEY+'-his',JSON.stringify(H.slice(0,10)));
  }}catch(e){{}}
  const caseLines=Object.keys(caseScore)
    .filter(k=>k && caseScore[k].n>1)
    .map(k=>'案例 '+k+'：'+caseScore[k].t+'/'+caseScore[k].n)
    .join(' · ');
  document.getElementById('res').innerHTML=
    '<div class="score">得分 '+score+'/'+total+'（'+(total?Math.round(score*100/total):0)+' 分）· 用时 '+fmtT(secs)+'</div>'+
    (caseLines?'<div class="hint">分组判分：'+esc(caseLines)+'</div>':'')+
    (wrong.length?'<div class="hint bad">错题回顾：'+wrong.join('、')+'（答案见下方解析区）</div>'
                 :'<div class="hint good">全对！</div>')+
    (wrong.length?'<button class="gray" onclick="retryWrong()">只练错题 →</button>':'')+
    (wrong.length?'<button class="gray" onclick="syncWrong()">同步错题到错题本</button>':'')+
    '<button class="gray" onclick="resetAll()">重新作答</button>'+
    '<div class="hint">作答已锁定（防止判分后误改）；如需重做请点「重新作答」或「清空重做」。</div>';
  // IMP-08：判分结果对读屏可达（role=status 已声明）+ 滚动并聚焦到结果
  const resEl=document.getElementById('res');
  if(resEl){{
    resEl.scrollIntoView({{behavior:'smooth',block:'nearest'}});
    try{{resEl.focus({{preventScroll:true}});}}catch(e){{}}
  }}
  // 锁定作答：判分后禁用输入，避免改答案与判分结果不一致
  document.querySelectorAll('#quiz input').forEach(x=>x.disabled=true);
  document.querySelectorAll('#quiz .q').forEach(d=>d.classList.add('judged'));
  QUESTIONS.forEach((q,i)=>{{
    const a=(st.answers&&st.answers[i])||"";
    const right=answersEqual(a,q.answer);
    const d=document.getElementById('q'+i); if(!d) return;
    if(!right) d.classList.add('wrongq');
    const qs=d.querySelector('.qs');
    if(qs&&!qs.querySelector('.chip')){{
      qs.insertAdjacentHTML('beforeend',
        right?' <span class="chip yes">✓ 对</span>':' <span class="chip no">✗ 错</span>');
    }}
    d.querySelectorAll('.opt').forEach(l=>{{
      const v=l.querySelector('input').value;
      const isAns=q.answer.includes(v);
      const isChos=a.includes(v);
      if(q.type==="X"){{
        if(isAns&&isChos) l.classList.add('right');
        else if(isAns) l.classList.add('miss');
        else if(isChos) l.classList.add('wrong');
      }}else{{
        if(isAns) l.classList.add('right');
        else if(isChos) l.classList.add('wrong');
      }}
    }});
    d.insertAdjacentHTML('beforeend',
      '<p class="ans">✅ 答案 '+esc(q.answer)+' · 💡 '+esc(q.analysis)+'</p>');
  }});
  if(wrong.length) syncWrong();   // 判分后自动回流错题本（幂等；按钮仅作手动兜底/重试）
}}
function retryWrong(){{
  let r=null; try{{r=JSON.parse(localStorage.getItem(RETRY_KEY)||"null");}}catch(e){{r=null;}}
  if(!r||!r.questions||!r.questions.length){{banner("暂无错题数据：先提交判分后再练错题",false);return;}}
  QUESTIONS=r.questions.slice();
  clearState(); judged=false; document.getElementById('res').innerHTML='';
  render();
  document.getElementById('res').innerHTML=
    '<div class="hint good">错题重练：'+QUESTIONS.length+' 题 · '+
    '<button class="mini" onclick="backToAll()">返回全卷</button></div>';
}}
function backToAll(){{QUESTIONS=ORIG.slice(); clearState(); judged=false; render();}}
function resetAll(){{clearState(); judged=false; document.getElementById('res').innerHTML=''; render();}}

/* 内联提示条（取代原生 alert，风格与页面一致） */
function banner(text,ok){{
  const box=document.getElementById('quiz');
  if(!box) return;
  let b=document.getElementById('banner');
  if(!b){{
    b=document.createElement('div'); b.id='banner';
    box.insertBefore(b, box.firstChild);
  }}
  b.className='banner '+(ok?'good':'bad');
  b.textContent=text;
  clearTimeout(b._t);
  b._t=setTimeout(()=>{{ if(b) b.remove(); }}, 6000);
}}

/* IMP-12④：同步失败提示条附「重试」按钮（syncWrong 幂等，防误以为错题已回流） */
function bannerRetry(text){{
  const box=document.getElementById('quiz');
  if(!box) return;
  let b=document.getElementById('banner');
  if(!b){{ b=document.createElement('div'); b.id='banner'; box.insertBefore(b, box.firstChild); }}
  b.className='banner bad';
  b.innerHTML=esc(text)+' <button class="mini" style="margin-left:8px;background:var(--card)" onclick="syncWrong()">重试 ↻</button>';
  clearTimeout(b._t);
  b._t=setTimeout(()=>{{ if(b) b.remove(); }}, 8000);
}}

async function syncWrong(){{
  const items=Object.values(WRONG_POOL);
  if(!items.length){{ banner("没有可同步的错题，请先「提交判分」",false); return; }}
  try{{
    const r=await fetch("/api/library/mistakes/sync-paper",{{
      method:"POST", headers:{{"Content-Type":"application/json"}},
      body:JSON.stringify({{pid:PAPER_PID, questions:items}})
    }});
    const j=await r.json().catch(()=>({{}}));
    if(!r.ok){{ bannerRetry("同步失败："+(j.detail||r.status)); return; }}
    banner("已同步 "+j.added+" 道错题到「学习中心 → 错题本」（重复题自动去重）",true);
  }}catch(e){{ bannerRetry("同步失败："+e.message); }}
}}

const T0=(loadState()||{{}}).t0||Date.now();
function fmtT(s){{const m=Math.floor(s/60),x=s%60;return m+':'+(x<10?'0':'')+x;}}
function ctLimit(){{
  const m=Math.max(5,Math.min(240,parseInt((document.getElementById('ctMin')||{{}}).value||'60',10)||60));
  return m*60;
}}
function ctToggle(){{
  const el=document.getElementById('ctMode');
  showCt=!!(el&&el.checked);
  if(showCt && secs>=ctLimit()){{ grade(); return; }}
  tick();
}}
function tick(){{
  secs=Math.floor((Date.now()-T0)/1000);
  const el=document.getElementById('timer');
  if(!el) return;
  if(showCt){{
    const left=Math.max(0,ctLimit()-secs);
    el.textContent='⏳ 剩 '+Math.floor(left/60)+':'+(left%60<10?'0':'')+(left%60);
    if(left<=0 && !judged){{ grade(); return; }}
  }} else el.textContent='⏱ '+fmtT(secs)+'（练习计时）';
}}
setInterval(tick,1000); tick();
document.addEventListener('input',e=>{{if(e.target.matches('.opt input'))collectAnswers();}});
/* IMP-08：答题卡格子可键盘激活（Enter/Space） */
function gridKeys(e){{const c=e.target;
  if((e.key==='Enter'||e.key===' ')&&c.classList&&c.classList.contains('cell')){{
    e.preventDefault(); jump(parseInt(c.textContent,10)-1);
  }}}}
document.addEventListener('keydown',gridKeys);
render();
</script>""", extras="paper")


def _page(title: str, body: str, extras: str = "") -> str:
    """产物页外壳：共用主题（pagechrome）+ 各页自身样式。"""
    from .pagechrome import BASE_CSS, THEME_BTN, THEME_SCRIPT, THEME_VARS

    own_css = """
main{max-width:860px;margin:0 auto}
h1{font-size:22px;margin-bottom:6px}
.meta .mini{margin-left:8px}
.nofilter{}
.filters button{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:6px 14px;margin:0 6px 10px 0;cursor:pointer;font-family:inherit}
.filters button.on{border-color:var(--acc);color:var(--acc)}
.q{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:14px 16px;margin-bottom:12px}
.q.case{border-left:4px solid var(--miss)}
.qsub{border-top:1px dashed var(--line);margin-top:10px;padding-top:10px}
.casebar{background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.35);border-radius:10px;padding:8px 12px;margin:12px 0 6px;font-size:13px;color:var(--txt)}
.q.wrongq{border-color:var(--bad)}
.q.flagged{border-left:4px solid var(--miss)}
.qs{cursor:pointer;line-height:1.7}
details.q .qs:hover{color:var(--acc)}
.qb p,.qb ul{font-size:14px;line-height:1.8;margin:8px 0}
.qb ul{padding-left:22px}
.ans{color:var(--good);font-size:13.5px}
.ana{color:var(--dim);font-size:13px}
.opt{display:block;padding:8px 12px;margin:6px 0;border:1px solid var(--line);border-radius:9px;cursor:pointer;font-size:14px}
.opt:hover{border-color:var(--acc)}
.opt.right{border-color:var(--good);background:rgba(52,211,153,.12)}
.opt.wrong{border-color:var(--bad);background:rgba(248,113,113,.12)}
.opt.miss{border-color:var(--miss);background:rgba(251,191,36,.10)}
.optfs{border:none;padding:0;margin:0;min-width:0}
.sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.q.judged .opt{cursor:default}
.q.judged .opt input{opacity:.85}
.mark{float:right;font-size:11px}
.btns{margin:16px 0}
.act{background:linear-gradient(180deg,rgba(56,189,248,.95),rgba(29,129,186,.95));color:#04101f;border:none;border-radius:10px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit}
.gray{background:rgba(80,110,150,.25);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:10px 18px;margin-left:8px;cursor:pointer;font-family:inherit}
.score{font-size:17px;font-weight:700;color:var(--acc);margin:10px 0}
.sheet{display:flex;gap:14px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:11px;padding:10px 14px;margin-bottom:14px;flex-wrap:wrap}
.grid{display:flex;flex-wrap:wrap;gap:5px;max-width:520px}
.cell{min-width:30px;text-align:center;border:1px solid var(--line);border-radius:7px;padding:3px 6px;font-size:12px;color:var(--dim);cursor:pointer}
.cell.done{background:rgba(52,211,153,.16);color:var(--good);border-color:var(--good)}
.cell.mk{border-color:var(--miss);color:var(--miss)}
.sheetactions{margin-left:auto}
/* v0.7.1：搜索框 / 判分徽章 / 已答计数 / 提示条 */
.filters input[type=search]{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:6px 12px;font-size:13px;font-family:inherit;outline:none;min-width:200px;max-width:100%}
.filters input[type=search]:focus{border-color:var(--acc)}
.asw{font-size:12.5px;color:var(--dim);white-space:nowrap}
.asw b{color:var(--acc);font-variant-numeric:tabular-nums}
.banner{border-radius:9px;padding:9px 14px;margin:0 0 12px;font-size:13px;animation:bannerin .25s ease}
.banner.good{background:rgba(52,211,153,.14);border:1px solid rgba(52,211,153,.4);color:var(--good)}
.banner.bad{background:rgba(248,113,113,.12);border:1px solid rgba(248,113,113,.4);color:var(--bad)}
@keyframes bannerin{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
""" + _MEDIA_CSS + """
@media (max-width:640px){.filters input[type=search]{min-width:100%;margin-top:4px}.sheet{justify-content:center}}
@media print{.q{background:none;border:1px solid #999;break-inside:avoid}.sheet{display:none}.btns{display:none}.mini{display:none}.banner{display:none}}
"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{html_mod.escape(title)} · MedKit</title>
<style>
{THEME_VARS}
{BASE_CSS}
{own_css}
</style></head><body><main>{body}</main>
{THEME_BTN}
{THEME_SCRIPT}
</body></html>"""


if __name__ == "__main__":  # pragma: no cover 手动调试
    qs = [{"id": "Q001", "type": "X", "bloom": "理解", "subtopic": "测试",
           "question": "下列属于X型？<script>alert(1)</script>", "options": ["A", "B", "C", "D", "E"],
           "answer": "BDE", "analysis": "解析…"}]
    print(export_paper_html(qs)[:500])

