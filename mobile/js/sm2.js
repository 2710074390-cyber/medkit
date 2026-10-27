/* =========================================================================
   SM-2 间隔重复 —— **严格移植** MedKit 桌面端 `medkit/core/vault_index.py`
   -------------------------------------------------------------------------
   为什么必须逐值一致（而不是"另外实现一个差不多的"）：
   同一道错题可能今天在电脑上复习、明天在手机上复习。两边算出的 `ease/interval/reps`
   若有一点偏差，复习节奏就会漂移，而漂移是无声的——用户只会觉得"怎么又要复习"。

   桌面端常量与公式（vault_index.py，2026-10-15 奥卡姆精简后唯一实现）：
     INIT_EASE = 2.5   MIN_EASE = 1.3   GRADE_PASS = 3
     sm2_next_intervals: reps+1 → 1 天 / 6 天 / max(round(interval*ease), interval+1)
     sm2_update_ease:    passed → e + (0.1 - (5-q)*(0.08 + (5-q)*0.02))
                         failed → e - 0.2
     失败：interval=0, reps=0, state=relearning, lapses+1, due=今天

   ⚠️ 舍入：Python 的 `round()` 是 **half-to-even**，JS 的 `Math.round()` 是 half-up。
   直接照搬会在 7.5 / 12.5 这类边界上分叉（例：round(5*2.5)=12 而非 13）。
   故这里自备 `pyRound`。黄金值见 `mobile/test/sm2_golden.mjs`。
   ========================================================================= */
(function (global) {
  "use strict";

  const INIT_EASE = 2.5;
  const MIN_EASE = 1.3;
  const GRADE_PASS = 3;
  const INTERVAL_FIRST = 1;
  const INTERVAL_SECOND = 6;

  /* Python round() 语义：四舍六入五取偶（负数按同一规则） */
  function pyRound(x) {
    const f = Math.floor(x);
    const diff = x - f;
    if (diff > 0.5) return f + 1;
    if (diff < 0.5) return f;
    return f % 2 === 0 ? f : f + 1;   // 恰好 .5 → 取偶数
  }

  function round2(x) { return pyRound(x * 100) / 100; }

  function sm2NextIntervals(ease, reps, interval) {
    reps += 1;
    if (reps === 1) return [INTERVAL_FIRST, reps];
    if (reps === 2) return [INTERVAL_SECOND, reps];
    return [Math.max(pyRound(interval * ease), interval + 1), reps];
  }

  function sm2UpdateEase(ease, quality, passed) {
    let e;
    if (passed) {
      const q = Math.max(0, Math.min(quality, 5));
      e = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02));
    } else {
      e = ease - 0.2;
    }
    return Math.max(MIN_EASE, round2(e));
  }

  /* 本地日期 YYYY-MM-DD（不用 toISOString：那是 UTC，晚上 8 点后会在东八区差一天） */
  function todayISO(d) {
    const t = d || new Date();
    const mm = String(t.getMonth() + 1).padStart(2, "0");
    const dd = String(t.getDate()).padStart(2, "0");
    return `${t.getFullYear()}-${mm}-${dd}`;
  }

  function addDays(iso, days) {
    const [y, m, d] = iso.split("-").map(Number);
    const t = new Date(y, m - 1, d);
    t.setDate(t.getDate() + days);
    return todayISO(t);
  }

  /* 推进一张卡：入参是「当前状态」，出参是要写回的字段。纯函数，不改入参。 */
  function gradeCard(rec, quality) {
    const q = Math.max(0, Math.min(Math.trunc(quality), 5));
    const passed = q >= GRADE_PASS;
    const ease = sm2UpdateEase(rec.ease || INIT_EASE, q, passed);
    let [interval, reps] = sm2NextIntervals(ease, rec.reps || 0, rec.interval || 0);
    let lapses = rec.lapses || 0;
    let state;
    if (passed) {
      state = reps >= 2 ? "review" : "learning";
    } else {
      interval = 0;
      reps = 0;
      state = "relearning";
      lapses += 1;
    }
    return { ease, interval, reps, lapses, state, due: addDays(todayISO(), interval) };
  }

  /* 三个评分按钮 → SM-2 quality。3 分是"模糊"，仍算通过（与桌面端 GRADE_PASS 一致）。 */
  const RATING = {
    forgot: { q: 1, label: "忘了" },
    vague: { q: 3, label: "模糊" },
    known: { q: 5, label: "记得" },
  };

  global.SM2 = {
    INIT_EASE, MIN_EASE, GRADE_PASS, RATING,
    sm2NextIntervals, sm2UpdateEase, gradeCard, todayISO, addDays, pyRound, round2,
  };

  /* 供 Node 单测直接 require（浏览器里 global === window，无副作用） */
  if (typeof module !== "undefined" && module.exports) module.exports = global.SM2;
})(typeof window !== "undefined" ? window : globalThis);
