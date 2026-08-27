"""产物页共用「页面皮肤」：主题变量 / 基础样式 / 明暗切换。

设计目标（2026-08-27）：题库 HTML、押题卷 HTML、复习手册 HTML 三套产物页原本各自内嵌
一套主题（深蓝径向渐变 + 亮/暗双主题），极易漂移。此处单源定义，三个渲染模块统一引用；
结构差异（题库的折叠卡、押题卷的答题卡、手册的文章版面）仍由各模块自己的 CSS 补充。

约定：
- 变量名 / 值即两套主题（dark 默认 + light）——改这里即全局生效；
- THEME_BTN / THEME_SCRIPT 为标准「明暗切换」按钮与脚本（localStorage 容错）；
- 产物页全部为自包含 HTML（零 CDN、零第三方）。
"""

# 主题变量（dark / light 双主题，径向渐变背景）
THEME_VARS = """\
:root{--bg:#0a1226;--card:rgba(18,32,62,.85);--line:rgba(120,180,255,.18);--txt:#dbeafe;--dim:#8aa4cc;--good:#34d399;--bad:#f87171;--miss:#fbbf24;--acc:#38bdf8;--bgc1:#12305e;--bgc2:#060d1e}
:root[data-theme=light]{--bg:#f2f6fb;--card:#ffffff;--line:rgba(30,80,140,.22);--txt:#12233d;--dim:#5a6b85;--good:#0d7a4f;--bad:#b3261e;--miss:#8a5a00;--acc:#0e6fb8;--bgc1:#dbe9f8;--bgc2:#eef4fb}"""

# 基础样式：通用重置 / 字体 / 渐变背景 / meta / mini 按钮 / hint / tag / chip / spinner
BASE_CSS = """\
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:radial-gradient(900px 500px at 20% -10%,var(--bgc1),transparent 55%),linear-gradient(160deg,var(--bg),var(--bgc2));color:var(--txt);padding:28px}
.meta{color:var(--dim);font-size:12.5px;margin-bottom:14px}
button.mini,.mini{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:3px 10px;font-size:12px;cursor:pointer;font-family:inherit}
.hint{font-size:12.5px;color:var(--dim);margin-top:8px}
.hint.good{color:var(--good)}
.hint.bad{color:var(--bad)}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11.5px;background:rgba(56,189,248,.14);color:var(--acc);margin-right:6px}
.tag.b{background:rgba(52,211,153,.14);color:var(--good)}
.chip{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11.5px;font-weight:600}
.chip.yes{background:rgba(52,211,153,.16);color:var(--good)}
.chip.no{background:rgba(248,113,113,.16);color:var(--bad)}
.spin{display:inline-block;width:12px;height:12px;border:2px solid rgba(56,189,248,.3);border-top-color:var(--acc);border-radius:50%;animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}"""

# 标准明暗切换按钮（固定右上角）
THEME_BTN = """\
<button id="themeBtn" class="mini" style="position:fixed;top:12px;right:12px;z-index:9;font-size:14px;padding:4px 12px;background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;cursor:pointer" onclick="toggleTheme()" title="切换亮/暗主题" aria-label="切换亮暗主题" aria-pressed="false">🌓</button>"""

# 明暗切换脚本（localStorage 全容错：隐私模式不中断）
THEME_SCRIPT = """\
<script>
try{if(localStorage.getItem("medkit-theme")==="light")document.documentElement.dataset.theme="light";}catch(e){}
function toggleTheme(){const cur=document.documentElement.dataset.theme==="light"?"dark":"light";
document.documentElement.dataset.theme=cur;try{localStorage.setItem("medkit-theme",cur);}catch(e){}applyThemeBtn();}
function applyThemeBtn(){const b=document.getElementById("themeBtn");
  if(b)b.setAttribute("aria-pressed",document.documentElement.dataset.theme==="light"?"true":"false");}
applyThemeBtn();
</script>"""

# 打印基线：白底黑字 + 隐藏交互控件（各页可在 print 规则后追加自己的细化）
PRINT_BASE = """\
@media print{body{background:#fff;color:#111}}"""
