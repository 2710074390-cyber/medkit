/* =========================================================================
   SM-2 一致性黄金值测试（Node 运行：node mobile/test/sm2_golden.mjs）
   -------------------------------------------------------------------------
   黄金值不是手算的，而是**从桌面端实现直接产出的**：
     python -c "from medkit.core.vault_index import _grade_card_of; ..."
   序列 [5,5,5,3,1,5,4,5,5,2,5] 覆盖了"连续记住 / 模糊 / 忘了 / 重新学习"四种状态迁移。

   为什么需要它：手机端与电脑端共用一个记忆曲线。任何一侧改了常量或舍入方式，
   复习节奏就会静默漂移——用户只会觉得"怎么又要复习"，而永远查不出原因。
   ========================================================================= */
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));

/* 仓库根 package.json 是 `"type": "module"`，所以 js/sm2.js 在 Node 里按 ESM 执行；
   它自身是「给浏览器用的普通脚本」，副作用是把 SM2 挂到 globalThis。
   这里照搬浏览器的方式加载（import 只为触发副作用），不额外给生产代码开后门。 */
await import(pathToFileURL(path.join(here, "..", "js", "sm2.js")).href);
const SM2 = globalThis.SM2;
if (!SM2 || typeof SM2.gradeCard !== "function") {
  console.error("无法加载 mobile/js/sm2.js —— 它应当把 SM2 挂到全局对象上");
  process.exit(1);
}

let failed = 0;

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failed += 1;
    console.error(`✗ ${name}\n    actual   = ${JSON.stringify(actual)}\n    expected = ${JSON.stringify(expected)}`);
  }
  return ok;
}

// ---- 1. 序列黄金值（桌面端 vault_index._grade_card_of 的逐值输出）
const GOLDEN = [
  [5, 2.6, 1, 1, 0, "learning"],
  [5, 2.7, 6, 2, 0, "review"],
  [5, 2.8, 17, 3, 0, "review"],
  [3, 2.66, 45, 4, 0, "review"],
  [1, 2.46, 0, 0, 1, "relearning"],
  [5, 2.56, 1, 1, 1, "learning"],
  [4, 2.56, 6, 2, 1, "review"],
  [5, 2.66, 16, 3, 1, "review"],
  [5, 2.76, 44, 4, 1, "review"],
  [2, 2.56, 0, 0, 2, "relearning"],
  [5, 2.66, 1, 1, 2, "learning"],
];

const rec = { ease: SM2.INIT_EASE, reps: 0, interval: 0, lapses: 0 };
console.log("step  q  ease  interval  reps  lapses  state");
GOLDEN.forEach(([q, ease, interval, reps, lapses, state], i) => {
  const out = SM2.gradeCard(rec, q);
  Object.assign(rec, { ease: out.ease, interval: out.interval, reps: out.reps, lapses: out.lapses, state: out.state });
  const ok = check(`step ${i + 1}`, [out.ease, out.interval, out.reps, out.lapses, out.state],
    [ease, interval, reps, lapses, state]);
  console.log(
    `${String(i + 1).padStart(4)}  ${q}  ${String(out.ease).padEnd(4)}  ${String(out.interval).padStart(8)}`
    + `  ${String(out.reps).padStart(4)}  ${String(out.lapses).padStart(6)}  ${out.state}${ok ? "" : "   <-- 不一致"}`
  );
});

// ---- 2. Python round() 是 half-to-even，JS Math.round() 是 half-up：
//          这几个边界值必须按 Python 语义走，否则间隔会差一天。
check("pyRound(12.5) = 12（不是 13）", SM2.pyRound(12.5), 12);
check("pyRound(7.5) = 8", SM2.pyRound(7.5), 8);
check("pyRound(17.5) = 18", SM2.pyRound(17.5), 18);
check("pyRound(2.5) = 2", SM2.pyRound(2.5), 2);
check("pyRound(4.5) = 4", SM2.pyRound(4.5), 4);

// ---- 3. 上下限
check("ease 不会低于 1.3", SM2.sm2UpdateEase(1.3, 0, false), 1.3);
check("第一次记住 = 1 天", SM2.sm2NextIntervals(2.5, 0, 0), [1, 1]);
check("第二次记住 = 6 天", SM2.sm2NextIntervals(2.5, 1, 1), [6, 2]);

// ---- 4. 日期推进（本地时区，不是 UTC）
check("addDays 跨月", SM2.addDays("2026-01-31", 1), "2026-02-01");
check("addDays 跨年", SM2.addDays("2026-12-31", 0), "2026-12-31");

if (failed) {
  console.error(`\n${failed} 项不一致 —— 手机端与桌面端的复习节奏已经分叉。`);
  process.exit(1);
}
console.log("\n全部一致：手机端 SM-2 与 medkit/core/vault_index.py 逐值相同。");
