/* WP-9：本地极简 Markdown → 富文本渲染器（零 CDN，XSS 安全：先转义再解析）。
   暴露：window.mdRender(src) / window.mdHighlight(text) / window.mdKeywords。 */
(function (global) {
  "use strict";
  const KW = ["首选药", "首选", "金标准", "确诊", "禁忌证", "禁忌症", "禁用", "一线",
              "特效药", "不良反应", "并发症", "鉴别诊断", "急性", "慢性", "休克"];
  const escMap = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => escMap[c] || c);
  }
  function highlight(text) {
    let s = esc(text);
    for (const k of KW) s = s.split(k).join(`<b class="kw">${k}</b>`);
    return s;
  }
  function inline(t) {
    // R4-26：代码段（`...`）先提取保护——外围 keyword 高亮/粗斜体不嵌套进 <code>，
    // 代码内容只转义（先 esc 后解析，XSS 安全不变）。
    const codes = [];
    const segs = String(t == null ? "" : t).split("`");
    let raw = "";
    segs.forEach((seg, i) => {
      if (i % 2 === 1) { codes.push(seg); raw += "\u0001" + (codes.length - 1) + "\u0001"; }
      else raw += seg;
    });
    let s = highlight(raw)
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\*([^*]+)\*/g, "<i>$1</i>");
    s = s.replace(/\u0001(\d+)\u0001/g, (m, n) => "<code>" + esc(codes[+n]) + "</code>");
    return s;
  }
  function mdRender(src) {
    const raw = String(src || "");
    const cells = t => String(t).split("|").slice(1, -1).map(c => inline(c.trim()));
    const isTableRow = l => /^\|.*\|$/.test((l || "").trim());
    const isSepRow = l => /^\|[\s:|-]+\|$/.test((l || "").trim());
    let html = "", inList = false, inQuote = false, inCode = false;
    const lines = raw.split("\n");
    for (let li = 0; li < lines.length; li++) {
      const line = (lines[li] || "").trim();
      if (inCode) {
        if (line.startsWith("```")) { html += "</code></pre>"; inCode = false; }
        else html += esc(line) + "\n";
        continue;
      }
      if (line.startsWith("```")) {
        if (inList) { html += "</ul>"; inList = false; }
        if (inQuote) { html += "</blockquote>"; inQuote = false; }
        html += "<pre><code>"; inCode = true; continue;
      }
      if (!line) {
        if (inList) { html += "</ul>"; inList = false; }
        if (inQuote) { html += "</blockquote>"; inQuote = false; }
        continue;
      }
      if (isTableRow(line) && li + 1 < lines.length && isSepRow(lines[li + 1])) {
        if (inList) { html += "</ul>"; inList = false; }
        if (inQuote) { html += "</blockquote>"; inQuote = false; }
        html += "<table><thead><tr>" + cells(line).map(c => "<th>" + c + "</th>").join("") + "</tr></thead><tbody>";
        li++;
        while (li + 1 < lines.length && isTableRow(lines[li + 1])) {
          li++;
          html += "<tr>" + cells(lines[li]).map(c => "<td>" + c + "</td>").join("") + "</tr>";
        }
        html += "</tbody></table>";
        continue;
      }
      if (line.startsWith("### ")) { if (inList) { html += "</ul>"; inList = false; } html += "<h4>" + inline(line.slice(4)) + "</h4>"; continue; }
      if (line.startsWith("## ")) { if (inList) { html += "</ul>"; inList = false; } html += "<h3>" + inline(line.slice(3)) + "</h3>"; continue; }
      if (line.startsWith("# ")) { if (inList) { html += "</ul>"; inList = false; } html += "<h2>" + inline(line.slice(2)) + "</h2>"; continue; }
      if (/^[-*·] /.test(line)) { if (!inList) { html += "<ul>"; inList = true; } html += "<li>" + inline(line.slice(2)) + "</li>"; continue; }
      if (line.startsWith("> ")) { if (!inQuote) { html += "<blockquote>"; inQuote = true; } html += "<p>" + inline(line.slice(2)) + "</p>"; continue; }
      if (line === "---") { if (inList) { html += "</ul>"; inList = false; } if (inQuote) { html += "</blockquote>"; inQuote = false; } html += "<hr>"; continue; }
      if (inList) { html += "</ul>"; inList = false; }
      if (inQuote) { html += "</blockquote>"; inQuote = false; }
      html += "<p>" + inline(line) + "</p>";
    }
    if (inList) html += "</ul>";
    if (inQuote) html += "</blockquote>";
    if (inCode) html += "</code></pre>";
    return html;
  }
  global.mdRender = mdRender;
  global.mdHighlight = highlight;
  global.mdKeywords = KW;
})(window);
