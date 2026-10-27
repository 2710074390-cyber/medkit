/* =========================================================================
   作答与复习的渲染层
   -------------------------------------------------------------------------
   两件事：
   1. 刷题会话（Drill）：单选点选即判分，多选需「确认作答」（与 MedKit 同口径）。
   2. 复习会话（Review）：先回忆 → 显示答案 → 三档评分（忘了/模糊/记得）。

   判分**完全在本地**完成（同步包里带答案）。这是刻意的：手机上网络常常很差，
   判一道题不该等一次请求。
   ========================================================================= */
(function (global) {
  "use strict";

  const esc = s => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  function tags(item) {
    const list = [];
    if (item.type) list.push(item.type);
    if (item.bloom) list.push(item.bloom);
    if (item.subtopic) list.push(item.subtopic);
    if (item.source_year) list.push(item.source_year);
    if (!list.length) return "";
    return `<div class="qmeta">${list.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>`;
  }

  function optionsHTML(item, { disabled }) {
    if (!Model.hasOptions(item)) {
      // 无选项（判断题/填空）：给一个"直接看答案"的形态，而不是渲染空白
      return `<p class="sec-hint">这道题没有选项，请在心里作答后点「显示答案」。</p>`;
    }
    const multi = Model.isMulti(item);
    return `<div class="opts" role="${multi ? "group" : "radiogroup"}"
      aria-label="${multi ? "多选题，可选中多个后确认" : "单选题，点选即判分"}">
      ${Model.letters(item.options.length).map((L, i) => `
        <button type="button" class="opt" data-i="${i}" role="${multi ? "checkbox" : "radio"}"
          aria-checked="false" ${disabled ? "disabled" : ""}>
          <span class="opt-k" aria-hidden="true">${L}</span>
          <span>${esc(item.options[i])}</span>
        </button>`).join("")}
    </div>`;
  }

  function paintSelection(box, picked) {
    box.querySelectorAll(".opt").forEach(el => {
      const on = picked.has(Number(el.dataset.i));
      el.classList.toggle("sel", on);
      if (el.hasAttribute("role")) el.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function paintResult(box, item, picked) {
    const right = new Set(String(item.answer || "").split(""));
    box.querySelectorAll(".opt").forEach(el => {
      const i = Number(el.dataset.i);
      const L = Model.LETTERS[i];
      el.disabled = true;
      el.classList.remove("sel", "right", "wrong");
      /* ⚠️ 不能写成 `classList.add(a ? "right" : (b ? "wrong" : ""))` ——
         传空串会抛 `Failed to execute 'add' on 'DOMTokenList'`，
         于是整个判定区不渲染（表现：点选项没反应）。桌面端 bank-drill.js 踩过同一个坑，
         这里按"先 remove 再按需 add"写，从形状上消掉这个错误。 */
      if (right.has(L)) el.classList.add("right");
      else if (picked.has(i)) el.classList.add("wrong");
      // 对错不能只靠颜色（色觉障碍）：把状态写进无障碍树
      if (el.hasAttribute("role")) {
        el.setAttribute("aria-checked", picked.has(i) ? "true" : "false");
      }
    });
  }

  /* ---------------------------------------------------------------- 刷题 */
  function Drill(opts) {
    const st = {
      items: opts.items || [],
      idx: 0,
      picked: new Set(),
      graded: false,
      rightCount: 0,
      wrongItems: [],
      card: opts.card,
      counter: opts.counter,
      prog: opts.prog,
      onExit: opts.onExit || function () {},
      onFinish: opts.onFinish || function () {},
    };

    function current() { return st.items[st.idx]; }

    function render() {
      const item = current();
      if (!item) { finish(); return; }
      st.picked = new Set();
      st.graded = false;
      st.card.innerHTML = `
        ${tags(item)}
        ${item.case_stem ? `<div class="qcase">${esc(item.case_stem)}</div>` : ""}
        <p class="qstem">${esc(item.question)}</p>
        ${optionsHTML(item, { disabled: false })}
        <div id="verdict"></div>
        <div class="qactions">
          <button class="btn btn-ghost" id="btn_prev" ${st.idx === 0 ? "disabled" : ""}>上一题</button>
          <button class="btn btn-primary" id="btn_main">${Model.isMulti(item) && Model.hasOptions(item) ? "确认作答" : "下一题"}</button>
        </div>
        <div class="sr-only" id="sr_verdict" role="status" aria-live="polite"></div>`;
      updateChrome();
      bind();
      if (!Model.hasOptions(item)) {
        // 无选项题：主按钮直接进入"看答案"
        const main = st.card.querySelector("#btn_main");
        main.textContent = "显示答案";
      }
      st.card.querySelector(".qcard") || null;
      st.card.scrollTop = 0;
      window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
    }

    function updateChrome() {
      if (st.counter) st.counter.textContent = `${st.idx + 1}/${st.items.length}`;
      /* 用 idx+1 而不是 idx：第 1 题时进度条为 0 宽，看起来像"没加载出来" */
      if (st.prog) st.prog.firstElementChild.style.width = `${((st.idx + 1) / st.items.length) * 100}%`;
    }

    function bind() {
      const box = st.card.querySelector(".opts");
      if (box) {
        box.addEventListener("click", e => {
          const el = e.target.closest(".opt");
          if (!el || st.graded) return;
          const i = Number(el.dataset.i);
          if (Model.isMulti(current())) {
            if (st.picked.has(i)) st.picked.delete(i); else st.picked.add(i);
            paintSelection(box, st.picked);
          } else {
            st.picked = new Set([i]);
            paintSelection(box, st.picked);
            grade();
          }
        });
      }
      st.card.querySelector("#btn_main").addEventListener("click", () => {
        if (!st.graded) {
          if (Model.hasOptions(current()) && !st.picked.size) {
            toastLocal("先选一个选项");
            return;
          }
          grade();
        } else {
          next();
        }
      });
      st.card.querySelector("#btn_prev").addEventListener("click", () => {
        if (st.idx > 0) { st.idx -= 1; render(); }
      });
    }

    async function grade() {
      const item = current();
      const pickedStr = [...st.picked].sort((a, b) => a - b)
        .map(i => Model.LETTERS[i]).join("");
      const correct = Model.sameSet(pickedStr, item.answer);
      st.graded = true;
      if (correct) st.rightCount += 1; else st.wrongItems.push(item);

      const box = st.card.querySelector(".opts");
      if (box) paintResult(box, item, st.picked);

      const verdict = st.card.querySelector("#verdict");
      const your = pickedStr || "（未选）";
      if (correct) {
        verdict.innerHTML = `<div class="verdict ok">答对了 <b>${esc(item.answer)}</b>
          ${Model.isMulti(item) ? "（多选按集合判分，不看顺序）" : ""}</div>`;
      } else {
        verdict.innerHTML = `<div class="verdict no">答错了。正确答案 <b>${esc(item.answer)}</b>，
          你的作答 <b>${esc(your)}</b>。已收进错题。</div>`;
      }
      if (item.analysis) {
        verdict.insertAdjacentHTML("beforeend", `<div class="explain">${esc(item.analysis)}</div>`);
      }
      const sr = st.card.querySelector("#sr_verdict");
      if (sr) sr.textContent = correct ? `答对了。正确答案${item.answer}` : `答错了。正确答案${item.answer}，你的作答${your}。`;

      try {
        await Model.recordAnswer({
          id: item.id, bank_id: item.bank_id, bank_name: item.bank_name,
          question: item.question, options: item.options, answer: item.answer,
          analysis: item.analysis, case_stem: item.case_stem,
          subject: item.subject, subtopic: item.subtopic,
        }, pickedStr, correct, "drill");
      } catch (e) {
        // 写库失败不该吞掉判分结果——用户已经看到对错了，这里只提示"这次没记上"
        toastLocal("这次作答没有保存成功");
      }

      st.card.querySelector("#btn_main").textContent = st.idx === st.items.length - 1 ? "完成" : "下一题";
      updateChrome();
    }

    function next() {
      if (st.idx === st.items.length - 1) { finish(); return; }
      st.idx += 1;
      render();
    }

    function finish() {
      if (st.prog) st.prog.firstElementChild.style.width = "100%";
      st.onFinish({
        total: st.items.length, right: st.rightCount,
        wrong: st.wrongItems.length,
      });
    }

    function toastLocal(msg) {
      if (typeof global.toast === "function") global.toast(msg);
    }

    return {
      start() { render(); },
      key(ev) {
        if (st.graded) {
          if (ev.key === "ArrowRight" || ev.key === "Enter") { ev.preventDefault(); next(); }
          if (ev.key === "ArrowLeft" && st.idx > 0) { ev.preventDefault(); st.idx -= 1; render(); }
          return;
        }
        const k = ev.key.toUpperCase();
        const i = Model.LETTERS.indexOf(k);
        if (i >= 0) {
          const item = current();
          if (!Model.hasOptions(item) || i >= item.options.length) return;
          const box = st.card.querySelector(".opts");
          if (!box) return;
          if (Model.isMulti(item) || !ev.shiftKey) {
            if (Model.isMulti(item)) {
              if (st.picked.has(i)) st.picked.delete(i); else st.picked.add(i);
            } else {
              st.picked = new Set([i]);
            }
            paintSelection(box, st.picked);
            if (!Model.isMulti(item)) grade();
            ev.preventDefault();
          }
        }
        if (ev.key === "Enter") {
          const btn = st.card.querySelector("#btn_main");
          if (btn) { ev.preventDefault(); btn.click(); }
        }
      },
    };
  }

  /* ---------------------------------------------------------------- 复习 */
  function Review(opts) {
    const st = {
      cards: opts.cards || [],
      idx: 0,
      revealed: false,
      card: opts.card,
      counter: opts.counter,
      prog: opts.prog,
      stats: { forgot: 0, vague: 0, known: 0 },
      onDone: opts.onDone || function () {},
      onExit: opts.onExit || function () {},
    };

    function current() { return st.cards[st.idx]; }

    function render() {
      const m = current();
      if (!m) { st.onDone(st.stats); return; }
      st.revealed = false;
      st.card.innerHTML = `
        ${tags(m)}
        <p class="qstem">${esc(m.question)}</p>
        ${Model.hasOptions(m) ? `<div class="opts">${Model.letters(m.options.length).map((L, i) => `
          <div class="opt" aria-hidden="false">  <!-- 复习阶段不可点选：这是"回忆"，不是"作答" -->
            <span class="opt-k" aria-hidden="true">${L}</span><span>${esc(m.options[i])}</span>
          </div>`).join("")}</div>` : ""}
        <div class="qactions">
          <button class="btn btn-primary" id="btn_reveal">显示答案</button>
        </div>
        <div id="rv_answer"></div>
        <div class="sr-only" id="sr_review" role="status" aria-live="polite"></div>`;
      if (st.counter) st.counter.textContent = `${st.idx + 1}/${st.cards.length}`;
      if (st.prog) st.prog.firstElementChild.style.width = `${(st.idx / st.cards.length) * 100}%`;
      st.card.querySelector("#btn_reveal").addEventListener("click", reveal);
      window.scrollTo({ top: 0, behavior: "auto" });
    }

    function reveal() {
      const m = current();
      st.revealed = true;
      const wrap = st.card.querySelector("#rv_answer");
      const rows = [];
      if (m.last_answer) {
        rows.push(`<div class="verdict no">上次答的是 <b>${esc(m.last_answer)}</b>，正确答案
          <b>${esc(m.answer)}</b>。这题已错 <b>${m.wrong_count || 1}</b> 次。</div>`);
      } else {
        rows.push(`<div class="verdict ok">正确答案 <b>${esc(m.answer)}</b></div>`);
      }
      if (m.analysis) rows.push(`<div class="explain">${esc(m.analysis)}</div>`);
      rows.push(`<div class="rate-row">
        <button class="rate forgot" data-r="forgot">忘了</button>
        <button class="rate vague" data-r="vague">模糊</button>
        <button class="rate known" data-r="known">记得</button>
      </div>`);
      wrap.innerHTML = rows.join("");
      /* 光在文字里写"正确答案 A"不够直观——把选项本身标出来（绿=对，红=上次选错的）。
         复习场景里用户是"回忆完对照"，视线落在选项上，标色比读一行字快得多。 */
      st.card.querySelectorAll(".opt").forEach(el => {
        const L = Model.LETTERS[Number(el.dataset.i)];
        if (String(m.answer || "").includes(L)) el.classList.add("right");
        else if (m.last_answer && String(m.last_answer).includes(L)) el.classList.add("wrong");
      });
      st.card.querySelector("#btn_reveal").closest(".qactions").remove();
      const sr = st.card.querySelector("#sr_review");
      if (sr) sr.textContent = `正确答案${m.answer}。请选择记得程度。`;
      wrap.querySelectorAll(".rate").forEach(b => {
        b.addEventListener("click", () => rateIt(b.dataset.r));
      });
      wrap.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    async function rateIt(key) {
      const m = current();
      try {
        await Model.rate(m, key);
        st.stats[key] += 1;
      } catch (e) {
        if (typeof global.toast === "function") global.toast("评分没有保存成功");
      }
      st.idx += 1;
      render();
    }

    return {
      start() { render(); },
      key(ev) {
        if (ev.key === " " || ev.key === "Enter") {
          ev.preventDefault();
          if (!st.revealed) reveal();
          return;
        }
        if (!st.revealed) return;
        const map = { 1: "forgot", 2: "vague", 3: "known" };
        if (map[ev.key]) { ev.preventDefault(); rateIt(map[ev.key]); }
      },
    };
  }

  global.Drill = Drill;
  global.Review = Review;
})(window);
