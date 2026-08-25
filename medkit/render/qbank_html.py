"""题库产物：qbank.md（分组导出） + qbank.html（自包含，可折叠+打印）
         + 押题卷.html（交互答题：checkbox 多选 / 集合判分 / 续答 / 答题卡 / 错题重练）
         + anki_export.txt（U8：Anki 文本导入）。

安全（A4，2026-08 审计）：押题卷 JS 所有插值经 esc() 转义；产物页自带明暗主题切换（V1）。
"""

import html as html_mod
from typing import Any

TYPE_LABELS = {"A1": "A1 型 · 单选", "A2": "A2 型 · 病例单选", "X": "X 型 · 多选",
               "B1": "B1 型 · 共用选项", "A3": "A3 型 · 案例单选", "A4": "A4 型 · 案例单选"}
LETTERS = "ABCDEFGHIJ"  # 渲染上限 10 个选项，超出部分由渲染前终检剔除（D2）


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


def _md_question(q: dict[str, Any], prefix: str = "###", show_options: bool = True) -> list[str]:
    out = [f"{prefix} {q.get('id')} · {TYPE_LABELS.get(q.get('type'), q.get('type', ''))} · {q.get('bloom', '')}"]
    out.append(f"**{q.get('subtopic', '')}**")
    out.append(q.get("question", ""))
    if show_options:
        for i, opt in enumerate(_effective_options(q)):
            out.append(f"- {LETTERS[i]}. {opt}")
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


def export_html(questions: list[dict[str, Any]], title: str = "题库") -> str:
    """题库 HTML：案例/选项组按组折叠（S3），单题保持原 <details class=q data-type> 结构。"""
    items = []
    for b in _case_blocks(questions):
        if b["kind"] == "case":
            first = b["items"][0]
            items.append(
                f'<details class="q case" data-type="{html_mod.escape(str(first.get("type", "")))}">'
                f'<summary class="qs"><span class="tag">📋 案例 '
                f'{html_mod.escape(str(b["key"][1]))}</span> '
                f'{html_mod.escape(TYPE_LABELS.get(str(first.get("type", "")), ""))} · '
                f'{len(b["items"])} 道子题 · 点击展开案例题干</summary>'
                f'<div class="qb"><p><b>案例题干</b>：{html_mod.escape(str(b["stem"]))}</p>'
                + "".join(_html_sub(q) for q in b["items"]) + '</div></details>')
        elif b["kind"] == "option_group":
            shared = "<ul>" + "".join(
                f"<li><b>{LETTERS[i]}</b> · {html_mod.escape(str(o))}</li>"
                for i, o in enumerate(b["options"])) + "</ul>"
            items.append(
                f'<details class="q case" data-type="B1">'
                f'<summary class="qs"><span class="tag">🧩 选项组（B1）</span>'
                f'（{len(b["items"])} 题共享下列选项）</summary>'
                f'<div class="qb">{shared}'
                + "".join(_html_sub(q, show_options=False) for q in b["items"]) + '</div></details>')
        else:
            q = b["items"][0]
            opts = "".join(
                f"<li><b>{LETTERS[i]}</b> · {html_mod.escape(str(o))}</li>"
                for i, o in enumerate(_effective_options(q)))
            items.append(
                f'<details class="q" data-type="{html_mod.escape(str(q.get("type", "")))}">'
                f'<summary class="qs">'
                f'<span class="tag">{html_mod.escape(str(q.get("type", "")))}</span> '
                f'<span class="tag b">{html_mod.escape(str(q.get("bloom", "")))}</span> '
                f'{html_mod.escape(str(q.get("question", ""))[:60])}…</summary>'
                f'<div class="qb"><p><b>{html_mod.escape(str(q.get("subtopic", "")))}</b> · '
                f'{html_mod.escape(str(q.get("question", "")))}</p><ul>{opts}</ul>'
                f'<p class="ans">✅ 答案：<b>{html_mod.escape(str(q.get("answer", "")))}</b></p>'
                f'<p class="ana">💡 {html_mod.escape(str(q.get("analysis", "")))}</p></div></details>')
    return _page(title, f"""
<h1>{html_mod.escape(title)}</h1>
<p class="meta">共 {len(questions)} 题 · 答案默认隐藏，点击题目展开查看</p>
<div class="filters">
  <button onclick="ft('')" class="on">全部</button>
  <button onclick="ft('A1')">A1 单选</button>
  <button onclick="ft('A2')">A2 病例</button>
  <button onclick="ft('X')">X 多选</button>
</div>
{''.join(items)}
<script>
function ft(t){{document.querySelectorAll('.q').forEach(d=>{{d.style.display=(!t||(d.dataset.type||'')===t)?'':'none'}});
document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('on'));event.target.classList.add('on');}}
</script>""", extras="qbank")


def _questions_json_for_page(questions: list[dict[str, Any]]) -> str:
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
                        "case_order": q.get("case_order", 0)})
    return json.dumps(compact, ensure_ascii=False).replace("</", "<\\/")


def export_paper_html(questions: list[dict[str, Any]], title: str = "押题卷") -> str:
    """交互押题卷（I3 练习化）：
    - X 型 checkbox + 集合判分（A1 修复）
    - localStorage 实时保存作答 + 重开续答 + 答题卡 + 计时器
    - 判分后「错题重练」（localStorage 错题集，可返回全卷）
    - 所有插值经 esc()（A4 修复）
    """
    qs = _questions_json_for_page(questions)
    return _page(title, f"""
<h1>{html_mod.escape(title)}</h1>
<p class="meta">共 {len(questions)} 题 · 作答自动保存 · <button class="mini" onclick="window.print()">🖨 打印</button>
  <span id="timer" style="float:right"></span></p>
<div id="quiz"><span class="spin"></span>加载中…</div>
<script>
let QUESTIONS = {qs};
const ORIG = QUESTIONS.slice();
const LETTERS = "ABCDEF";
const KEY = "medkit-paper-" + location.pathname.split('/').pop();
const RETRY_KEY = KEY + "-retry";
let secs = 0;

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
  const sa=a.split("").sort().join(""), sb=b.split("").sort().join("");
  return sa.length>0 && sa===sb;
}}

function render(){{
  const box=document.getElementById('quiz');
  let h='';
  h+='<div class="sheet"><div class="grid" id="grids"></div>'
     +'<div class="sheetactions"></div></div>';
  let lastCase='';
  QUESTIONS.forEach((q,i)=>{{
    if(!q.options||!q.options.length) return;
    if(q.case_label && q.case_label!==lastCase){{
      h+='<div class="casebar" data-case="'+esc(q.case_id)+'">'+esc(q.case_label)+'</div>';
      lastCase=q.case_label;
    }}
    h+='<div class="q" id="q'+i+'"><p class="qs"><span class="tag">'+esc(q.type)+'</span>'
      +'<span class="tag b">'+esc(q.bloom)+'</span> <b>'+(i+1)+'.</b> '+esc(q.question)+'</p>';
    q.options.forEach((o,j)=>{{
      const t=q.type==="X"?"checkbox":"radio";
      h+='<label class="opt"><input type="'+t+'" name="q'+i+'" value="'+LETTERS[j]+'"> '
        +LETTERS[j]+' · '+esc(o)+'</label>';
    }});
    h+='<button class="mark gray mini" onclick="mark('+i+')">旗</button></div>';
  }});
  h+='<div class="btns"><button class="act" onclick="grade()">提交判分</button>'
     +'<button class="gray" onclick="resetAll()">清空重做</button></div><div id="res"></div>';
  box.innerHTML=h;
  paintAnswers();
  buildGrid();
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
  saveState(st); buildGrid();
}}
function buildGrid(){{
  const g=document.getElementById('grids'); if(!g) return;
  const st=loadState()||{{answers:{{}},marked:null}};
  let h='';
  QUESTIONS.forEach((q,i)=>{{
    const a=st.answers&&st.answers[i];
    h+='<span class="cell'+(a?' done':'')+(st.marked===i?' mk':'')+'" onclick="jump('+i+')">'+(i+1)+'</span>';
  }});
  g.innerHTML=h;
}}
function jump(i){{document.getElementById('q'+i)?.scrollIntoView({{behavior:'smooth',block:'center'}});}}

function grade(){{
  collectAnswers();
  let score=0, wrong=[];
  const st=loadState()||{{answers:{{}}}};
  const caseScore={{}};
  QUESTIONS.forEach((q,i)=>{{
    const a=(st.answers&&st.answers[i])||"";
    const right=answersEqual(a,q.answer);
    if(right) score++;
    else wrong.push(i+1);
    const ck=q.case_id||"";
    if(ck){{ const cs=caseScore[ck]||={{n:0,t:0}}; cs.n++; if(right) cs.t++; caseScore[ck]=cs; }}
  }});
  const total=QUESTIONS.length;
  try{{localStorage.setItem(RETRY_KEY,JSON.stringify(
    {{title:"错题重练",questions:wrong.map(i=>QUESTIONS[i-1]).filter(Boolean)}}));}}catch(e){{}}
  const caseLines=Object.keys(caseScore)
    .filter(k=>k && caseScore[k].n>1)
    .map(k=>'案例 '+k+'：'+caseScore[k].t+'/'+caseScore[k].n)
    .join(' · ');
  document.getElementById('res').innerHTML=
    '<div class="score">得分 '+score+'/'+total+'（'+(total?Math.round(score*100/total):0)+' 分）</div>'+
    (caseLines?'<div class="hint">分组判分：'+esc(caseLines)+'</div>':'')+
    (wrong.length?'<div class="hint bad">错题回顾：'+wrong.join('、')+'（答案见下方解析区）</div>'
                 :'<div class="hint good">全对！</div>')+
    (wrong.length?'<button class="gray" onclick="retryWrong()">只练错题 →</button>':'')+
    '<button class="gray" onclick="resetAll()">重新作答</button>';
  QUESTIONS.forEach((q,i)=>{{
    const a=(st.answers&&st.answers[i])||"";
    const right=answersEqual(a,q.answer);
    const d=document.getElementById('q'+i); if(!d) return;
    if(!right) d.classList.add('wrongq');
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
}}
function retryWrong(){{
  let r=null; try{{r=JSON.parse(localStorage.getItem(RETRY_KEY)||"null");}}catch(e){{r=null;}}
  if(!r||!r.questions||!r.questions.length){{alert("暂无错题数据：先提交判分后再练错题");return;}}
  QUESTIONS=r.questions.slice();
  clearState(); document.getElementById('res').innerHTML='';
  render();
  document.getElementById('res').innerHTML=
    '<div class="hint good">错题重练：'+QUESTIONS.length+' 题 · '+
    '<button class="mini" onclick="backToAll()">返回全卷</button></div>';
}}
function backToAll(){{QUESTIONS=ORIG.slice(); clearState(); render();}}
function resetAll(){{clearState(); document.getElementById('res').innerHTML=''; render();}}

const T0=(loadState()||{{}}).t0||Date.now();
function tick(){{secs=Math.floor((Date.now()-T0)/1000);
  const m=Math.floor(secs/60), s=secs%60;
  const el=document.getElementById('timer');
  if(el) el.textContent='⏱ '+m+':'+(s<10?'0':'')+s;}}
setInterval(tick,1000); tick();
document.addEventListener('input',e=>{{if(e.target.matches('.opt input'))collectAnswers();}});
render();
</script>""", extras="paper")


def _page(title: str, body: str, extras: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{html_mod.escape(title)} · MedKit</title>
<style>
:root{{--bg:#0a1226;--card:rgba(18,32,62,.85);--line:rgba(120,180,255,.18);--txt:#dbeafe;--dim:#8aa4cc;
--good:#34d399;--bad:#f87171;--miss:#fbbf24;--acc:#38bdf8;--bgc1:#12305e;--bgc2:#060d1e}}
:root[data-theme=light]{{--bg:#f2f6fb;--card:#ffffff;--line:rgba(30,80,140,.22);--txt:#12233d;--dim:#5a6b85;
--good:#0d7a4f;--bad:#b3261e;--miss:#8a5a00;--acc:#0e6fb8;--bgc1:#dbe9f8;--bgc2:#eef4fb}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:radial-gradient(900px 500px at 20% -10%,var(--bgc1),transparent 55%),linear-gradient(160deg,var(--bg),var(--bgc2));color:var(--txt);padding:28px}}
main{{max-width:860px;margin:0 auto}}
h1{{font-size:22px;margin-bottom:6px}}
.meta{{color:var(--dim);font-size:12.5px;margin-bottom:14px}}
.meta .mini{{margin-left:8px}}
button.mini,.mini{{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:3px 10px;font-size:12px;cursor:pointer;font-family:inherit}}
.nofilter{{}}
.filters button{{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:6px 14px;margin:0 6px 10px 0;cursor:pointer;font-family:inherit}}
.filters button.on{{border-color:var(--acc);color:var(--acc)}}
.q{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:14px 16px;margin-bottom:12px}}
.q.case{{border-left:4px solid var(--miss)}}
.qsub{{border-top:1px dashed var(--line);margin-top:10px;padding-top:10px}}
.casebar{{background:rgba(251,191,36,.10);border:1px solid rgba(251,191,36,.35);border-radius:10px;padding:8px 12px;margin:12px 0 6px;font-size:13px;color:var(--txt)}}
.q.wrongq{{border-color:var(--bad)}}
.q.flagged{{border-left:4px solid var(--miss)}}
.qs{{cursor:pointer;line-height:1.7}}
details.q .qs:hover{{color:var(--acc)}}
.qb p,.qb ul{{font-size:14px;line-height:1.8;margin:8px 0}}
.qb ul{{padding-left:22px}}
.tag{{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11.5px;background:rgba(56,189,248,.14);color:var(--acc);margin-right:6px}}
.tag.b{{background:rgba(52,211,153,.14);color:var(--good)}}
.ans{{color:var(--good);font-size:13.5px}}
.ana{{color:var(--dim);font-size:13px}}
.opt{{display:block;padding:8px 12px;margin:6px 0;border:1px solid var(--line);border-radius:9px;cursor:pointer;font-size:14px}}
.opt:hover{{border-color:var(--acc)}}
.opt.right{{border-color:var(--good);background:rgba(52,211,153,.12)}}
.opt.wrong{{border-color:var(--bad);background:rgba(248,113,113,.12)}}
.opt.miss{{border-color:var(--miss);background:rgba(251,191,36,.10)}}
.mark{{float:right;font-size:11px}}
.btns{{margin:16px 0}}
.act{{background:linear-gradient(180deg,rgba(56,189,248,.95),rgba(29,129,186,.95));color:#04101f;border:none;border-radius:10px;padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit}}
.gray{{background:rgba(80,110,150,.25);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:10px 18px;margin-left:8px;cursor:pointer;font-family:inherit}}
.score{{font-size:17px;font-weight:700;color:var(--acc);margin:10px 0}}
.sheet{{display:flex;gap:14px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:11px;padding:10px 14px;margin-bottom:14px;flex-wrap:wrap}}
.grid{{display:flex;flex-wrap:wrap;gap:5px;max-width:520px}}
.cell{{min-width:30px;text-align:center;border:1px solid var(--line);border-radius:7px;padding:3px 6px;font-size:12px;color:var(--dim);cursor:pointer}}
.cell.done{{background:rgba(52,211,153,.16);color:var(--good);border-color:var(--good)}}
.cell.mk{{border-color:var(--miss);color:var(--miss)}}
.sheetactions{{margin-left:auto}}
.hint{{font-size:12.5px;color:var(--dim);margin-top:8px}}
.hint.good{{color:var(--good)}}
.hint.bad{{color:var(--bad)}}
.spin{{display:inline-block;width:12px;height:12px;border:2px solid rgba(56,189,248,.3);border-top-color:var(--acc);border-radius:50%;animation:sp .8s linear infinite}}
@keyframes sp{{to{{transform:rotate(360deg)}}}}
@media print{{body{{background:#fff;color:#111}} .q{{background:none;border:1px solid #999;break-inside:avoid}} .sheet{{display:none}} .btns{{display:none}}}}
</style></head><body><main>{body}</main>
<button class="mini" style="position:fixed;top:12px;right:12px;z-index:9;font-size:14px;padding:4px 12px" onclick="toggleTheme()" title="切换亮/暗主题">🌓</button>
<script>
try{{if(localStorage.getItem("medkit-theme")==="light")document.documentElement.dataset.theme="light";}}catch(e){{}}
function toggleTheme(){{const cur=document.documentElement.dataset.theme==="light"?"dark":"light";
document.documentElement.dataset.theme=cur;try{{localStorage.setItem("medkit-theme",cur);}}catch(e){{}}}}
</script>
</body></html>"""


if __name__ == "__main__":  # pragma: no cover 手动调试
    qs = [{"id": "Q001", "type": "X", "bloom": "理解", "subtopic": "测试",
           "question": "下列属于X型？<script>alert(1)</script>", "options": ["A", "B", "C", "D", "E"],
           "answer": "BDE", "analysis": "解析…"}]
    print(export_paper_html(qs)[:500])
