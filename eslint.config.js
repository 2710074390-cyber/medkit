/**
 * U-17（R6-09 / 独立核查 M-2）：前端静态防线（ESLint flat config）。
 *
 * 设计约束（不破项目既有原则）：
 * 1. **不引入打包器**：只做检查。产物仍是零构建、零 CDN 的原生 `<script src="/assets/js/...">`；
 *    本配置不参与打包、不产生构建步骤（`npm run lint` 只读）。
 * 2. **经典脚本共享全局作用域**：`medkit/web/js/` 下全部经典脚本靠「加载顺序即隐式契约」互相调用
 *    （`docs/AGENT_HANDOFF.md` §4.4 已自述「跨文件函数加载期前向引用会挂」）。
 *    V-15（2026-09-16）把 `learn.js` / `review-desk.js` 各拆为 4 片后本目录共 10 个脚本——
 *    配置**自动扫描目录**，新增文件无需改本文件；但分片顺序与加载期前向引用由
 *    `tests/test_v15_frontend_split.py` 守。
 *    因此**逐文件**把「其它文件的顶层声明」声明为共享全局符号——这样 `no-undef` 既能抓住
 *    「拼错函数名」（原先只有运行时才炸），又不会把合法的跨文件调用误报，
 *    也不会与本文件自身的定义冲突（`no-redeclare`）。
 *
 * 用法：`npm run lint`（0 error 为目标；warn 为既有基线，只减不增）。
 */
import fs from "node:fs";
import path from "node:path";

import globals from "globals";

const JS_DIR = "medkit/web/js";
const files = fs.readdirSync(JS_DIR).filter((f) => f.endsWith(".js")).sort();
const TOP_LEVEL_RE = /^(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)/gm;
// 经典脚本还常把接口挂到 window（如 md.js 的 `window.mdRender = …`）供其它文件/内联 HTML 调用
const WINDOW_ASSIGN_RE = /\b(?:window|global)\.([A-Za-z_$][\w$]*)\s*=/g;

/** 收集某文件对外暴露的顶层声明名（含 `window.X =` 挂载）。 */
function ownDeclarations(file) {
  const src = fs.readFileSync(path.join(JS_DIR, file), "utf8");
  const names = new Set();
  for (const m of src.matchAll(TOP_LEVEL_RE)) names.add(m[1]);
  for (const m of src.matchAll(WINDOW_ASSIGN_RE)) names.add(m[1]);
  return names;
}

const own = Object.fromEntries(files.map((f) => [f, ownDeclarations(f)]));
const allDeclared = new Set(files.flatMap((f) => [...own[f]]));

const RULES = {
  // —— 核心价值：拼错标识符静态即报（原先只有运行时才炸）——
  "no-undef": "error",
  "no-redeclare": "error",
  "no-dupe-keys": "error",
  "no-dupe-args": "error",
  "no-dupe-else-if": "error",
  "no-unreachable": "error",
  "no-fallthrough": "error",
  "no-self-assign": "error",
  "no-obj-calls": "error",
  "use-isnan": "error",
  "valid-typeof": "error",
  // —— 质量问题：先作 warn 基线化，只减不增 ——
  "no-unused-vars": ["warn", { args: "none", caughtErrors: "none" }],
  "no-empty": ["warn", { allowEmptyCatch: true }],
  "no-constant-condition": "warn",
  "no-cond-assign": "warn",
  "no-sparse-arrays": "warn",
  "no-template-curly-in-string": "warn",
  "no-async-promise-executor": "warn",
  eqeqeq: ["warn", "smart"],
};

// 逐文件配置：globals = 浏览器内置 + 其它文件的顶层声明（不含本文件自身的）
export default files.map((file) => {
  const sharedFromOthers = {};
  for (const name of allDeclared) {
    if (!own[file].has(name)) sharedFromOthers[name] = "writable";
  }
  return {
    files: [`${JS_DIR}/${file}`],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script", // 经典脚本（非 ESM）——共享全局作用域
      globals: { ...globals.browser, ...sharedFromOthers },
    },
    linterOptions: { reportUnusedDisableDirectives: true },
    rules: RULES,
  };
});
