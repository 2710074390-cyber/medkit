/* =========================================================================
   领域模型：同步包导入 / 作答记账 / 错题归纳 / 复习队列
   -------------------------------------------------------------------------
   这一层不碰 DOM。所有函数只读写 IndexedDB，便于以后换 UI（或做小程序壳）。

   一条贯穿全文件的原则：**导入永不覆盖本地复习进度**。
   同步包是"电脑端的事实"，SM-2 状态是"你在手机上练出来的记忆曲线"；
   合并时以本地 SRS 状态为准，只补新题、补错题、补作答流水。
   否则每次同步都会把复习节奏清零——那是最难察觉、后果最重的一类数据损失。
   ========================================================================= */
(function (global) {
  "use strict";

  const SCHEMA_PREFIX = "medkit.bundle/";

  const LETTERS = "ABCDEFGHIJ";

  function letters(n) { return LETTERS.slice(0, n).split(""); }

  /* 判断题/填空题在题库里可能没有 options —— 一律按"有选项"渲染会把题目渲染成空白 */
  function hasOptions(item) {
    return Array.isArray(item && item.options) && item.options.length > 0;
  }

  function isMulti(item) {
    const ans = String((item && item.answer) || "").replace(/[^A-J]/g, "");
    return ans.length > 1;
  }

  /* 集合判分：与 MedKit 同口径（"B D" == "BD" == "DB"），多选不看顺序 */
  function sameSet(a, b) {
    const norm = s => String(s || "").toUpperCase().replace(/[^A-J]/g, "").split("").sort().join("");
    return norm(a) === norm(b) && norm(a) !== "";
  }

  function nowISO() { return new Date().toISOString(); }

  // ---------------------------------------------------------------- 导入
  function validateBundle(data) {
    if (!data || typeof data !== "object") throw new Error("不是有效的 JSON 文件");
    const schema = String(data.schema || "");
    if (!schema.startsWith(SCHEMA_PREFIX)) {
      throw new Error("这不是 MedKit 的手机同步包（缺少 schema 标记）");
    }
    if (!Array.isArray(data.banks)) throw new Error("同步包缺少 banks 字段");
    return true;
  }

  async function importBundle(data) {
    validateBundle(data);
    const report = { banks: 0, items: 0, attempts: 0, mistakes: 0, skippedAttempts: 0 };

    const bankRows = [];
    const itemRows = [];
    for (const b of data.banks) {
      if (!b || !b.id) continue;
      bankRows.push({
        id: String(b.id), name: b.name || "未命名题库", subject: b.subject || "",
        item_count: (b.items || []).length,
        updated_at: b.updated_at || "", imported_at: nowISO(),
      });
      for (const it of b.items || []) {
        if (!it || !it.id) continue;
        itemRows.push({
          id: String(it.id), bank_id: String(b.id), bank_name: b.name || "",
          question: it.question || "", type: it.type || "", bloom: it.bloom || "",
          subtopic: it.subtopic || "", options: it.options || [],
          answer: String(it.answer || "").toUpperCase().replace(/[^A-J]/g, ""),
          analysis: it.analysis || "", case_stem: it.case_stem || "",
          source_year: it.source_year || "", starred: !!it.starred, note: it.note || "",
          subject: b.subject || "",
        });
      }
    }
    await DB.bulkPut("banks", bankRows);
    await DB.bulkPut("items", itemRows);
    report.banks = bankRows.length;
    report.items = itemRows.length;

    // ---- 作答流水：只补本地没有的（按 item_id + 发生时间 认定同一条）
    if (Array.isArray(data.attempts) && data.attempts.length) {
      const existing = await DB.all("attempts");
      const seen = new Set(existing.map(a => `${a.item_id}|${a.at}`));
      const fresh = [];
      for (const a of data.attempts) {
        if (!a || !a.item_id || !a.occurred_at) continue;
        const key = `${a.item_id}|${a.occurred_at}`;
        if (seen.has(key)) { report.skippedAttempts += 1; continue; }
        seen.add(key);
        fresh.push({
          item_id: String(a.item_id), bank_id: String(a.bank_id || ""),
          correct: !!a.correct, at: String(a.occurred_at), mode: a.mode || "drill",
          from: "desktop",
        });
      }
      if (fresh.length) await DB.bulkPut("attempts", fresh);
      report.attempts = fresh.length;
    }

    // ---- 错题：本地已有则**只补计数**，SRS 状态以本地为准（见文件头）
    if (Array.isArray(data.mistakes) && data.mistakes.length) {
      const local = await DB.all("mistakes");
      const byId = new Map(local.map(m => [m.id, m]));
      const writes = [];
      for (const m of data.mistakes) {
        if (!m || !m.id || !String(m.question || "").trim()) continue;
        const id = `mk:${m.id}`;
        const hit = byId.get(id);
        if (hit) {
          hit.wrong_count = Math.max(hit.wrong_count || 0, Number(m.miss_count) || 0);
          hit.updated_at = nowISO();
          writes.push(hit);
          continue;
        }
        writes.push(newMistake({
          id, item_id: "", source: "desktop", bank_id: "", bank_name: "",
          question: m.question, options: m.options || [], answer: m.answer || "",
          analysis: m.analysis || "", subject: m.subject || "",
          subtopic: m.topic || m.chapter || "",
          wrong_count: Math.max(1, Number(m.miss_count) || 1),
          last_wrong_at: m.last_tried || m.created_at || "",
          last_answer: m.user_answer || "",
        }));
        report.mistakes += 1;
      }
      if (writes.length) await DB.bulkPut("mistakes", writes);
    }

    await DB.setMeta("last_import", {
      at: nowISO(), exported_at: data.exported_at || "", app_version: data.app_version || "",
      banks: report.banks, items: report.items,
    });
    return report;
  }

  // ---------------------------------------------------------------- 错题
  /* 错题记录是**自包含**的：带题干/选项/答案/解析快照。
     为什么冗余存一份而不只存 item_id：桌面端的错题本（vault）本来就没有题库题目，
     而且题库重新导出后题目可能被删——只存引用会让复习列表出现"空白卡"。 */
  function newMistake(src) {
    const t = SM2.todayISO();
    return {
      id: src.id,
      item_id: src.item_id || "",
      source: src.source || "bank",
      bank_id: src.bank_id || "",
      bank_name: src.bank_name || "",
      question: src.question || "",
      options: src.options || [],
      answer: src.answer || "",
      analysis: src.analysis || "",
      case_stem: src.case_stem || "",
      subject: src.subject || "",
      subtopic: src.subtopic || "",
      wrong_count: src.wrong_count || 1,
      last_wrong_at: src.last_wrong_at || nowISO(),
      last_answer: src.last_answer || "",
      // SRS 初始态：新卡今天到期（与 vault 的 `rec.setdefault("due", 今天)` 一致）
      ease: SM2.INIT_EASE, interval: 0, reps: 0, lapses: 0,
      state: "new", due: t,
      created_at: src.created_at || nowISO(),
      updated_at: nowISO(),
    };
  }

  async function upsertMistakeFromWrong(item, picked) {
    /* 从「错题组」里再练时，题目是错题本身的快照（可能来自桌面端，没有 item_id）。
       这时必须沿用原错题 id，否则每练一次就会派生出一条新的重复错题。 */
    const id = item.mistake_id || `it:${item.item_id || item.id}`;
    const hit = await DB.get("mistakes", id);
    if (hit) {
      hit.wrong_count = (hit.wrong_count || 0) + 1;
      hit.last_wrong_at = nowISO();
      hit.last_answer = picked;
      hit.updated_at = nowISO();
      // 题干可能在题库里被修正过：以最新题库为准，避免复习旧版本
      hit.question = item.question;
      hit.options = item.options || [];
      hit.answer = item.answer || "";
      hit.analysis = item.analysis || "";
      await DB.put("mistakes", hit);
      return hit;
    }
    const rec = newMistake({
      id, item_id: item.item_id || (item.mistake_id ? "" : item.id), source: "bank",
      bank_id: item.bank_id, bank_name: item.bank_name,
      question: item.question, options: item.options || [], answer: item.answer || "",
      analysis: item.analysis || "", case_stem: item.case_stem || "",
      subject: item.subject || "", subtopic: item.subtopic || "",
      wrong_count: 1, last_wrong_at: nowISO(), last_answer: picked,
    });
    await DB.put("mistakes", rec);
    return rec;
  }

  // ---------------------------------------------------------------- 作答
  async function recordAnswer(item, picked, correct, mode) {
    await DB.put("attempts", {
      item_id: item.id, bank_id: item.bank_id || "", correct: !!correct,
      at: nowISO(), mode: mode || "drill", from: "phone",
    });
    if (!correct) return upsertMistakeFromWrong(item, picked);
    return null;
  }

  // ---------------------------------------------------------------- 归纳
  /* 薄弱点聚类：优先 subtopic（题库里的"考点"），没有则退回题库名。
     排序 = 错得多 + 错得近。这两个信号合起来才是"现在最该练的"。 */
  async function groupMistakes() {
    const all = await DB.all("mistakes");
    const today = SM2.todayISO();
    const map = new Map();
    for (const m of all) {
      const key = (m.subtopic || "").trim() || (m.bank_name || "").trim() || "未归类";
      let g = map.get(key);
      if (!g) {
        g = { key, label: key, count: 0, due: 0, lastAt: "", subject: m.subject || "" };
        map.set(key, g);
      }
      g.count += 1;
      if ((m.due || today) <= today) g.due += 1;
      if ((m.last_wrong_at || "") > g.lastAt) g.lastAt = m.last_wrong_at;
    }
    const out = [...map.values()];
    out.sort((a, b) => (b.count - a.count) || String(b.lastAt).localeCompare(String(a.lastAt)));
    return out;
  }

  async function mistakesOfGroup(key) {
    const all = await DB.all("mistakes");
    return all.filter(m => ((m.subtopic || "").trim() || (m.bank_name || "").trim() || "未归类") === key);
  }

  // ---------------------------------------------------------------- 复习
  async function dueList(limit) {
    const today = SM2.todayISO();
    const all = await DB.all("mistakes");
    const due = all.filter(m => (m.due || today) <= today);
    due.sort((a, b) => String(a.due || "").localeCompare(String(b.due || ""))
      || (b.wrong_count || 0) - (a.wrong_count || 0));
    return limit ? due.slice(0, limit) : due;
  }

  async function rate(rec, ratingKey) {
    const r = SM2.RATING[ratingKey];
    if (!r) throw new Error("未知评分");
    const next = SM2.gradeCard(rec, r.q);
    Object.assign(rec, next, { updated_at: nowISO() });
    await DB.put("mistakes", rec);
    return rec;
  }

  // ---------------------------------------------------------------- 统计
  async function stats() {
    const [items, banks, mistakes, attempts] = await Promise.all([
      DB.count("items"), DB.count("banks"), DB.all("mistakes"), DB.all("attempts"),
    ]);
    const today = SM2.todayISO();
    const due = mistakes.filter(m => (m.due || today) <= today).length;
    const todayAttempts = attempts.filter(a => String(a.at || "").slice(0, 10) === today);
    const right = todayAttempts.filter(a => a.correct).length;
    return {
      banks, items, mistakes: mistakes.length, due,
      todayDone: todayAttempts.length,
      todayAcc: todayAttempts.length ? Math.round((right / todayAttempts.length) * 100) : null,
      mastered: mistakes.filter(m => m.state === "review" && (m.reps || 0) >= 2).length,
    };
  }

  // ---------------------------------------------------------------- 导出（换机用）
  async function exportAll() {
    const [banks, items, attempts, mistakes, meta] = await Promise.all([
      DB.all("banks"), DB.all("items"), DB.all("attempts"),
      DB.all("mistakes"), DB.all("meta"),
    ]);
    return {
      schema: "medkit.mobile.backup/v1",
      exported_at: nowISO(),
      banks, items, attempts, mistakes, meta,
    };
  }

  global.Model = {
    LETTERS, letters, hasOptions, isMulti, sameSet, nowISO,
    importBundle, validateBundle, recordAnswer,
    newMistake, upsertMistakeFromWrong,
    groupMistakes, mistakesOfGroup, dueList, rate, stats, exportAll,
  };
})(window);
