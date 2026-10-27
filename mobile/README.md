# MedKit 手机端（刷题 / 错题 / 复习）

手机端只做三件事：**刷题 → 错题归纳 → 按记忆曲线复习**。
出题、审核、导入 PDF、提示词这些留在电脑上（屏幕放不下，也不需要随时做）。

- 零构建、零 CDN、零外部字体；纯静态文件，任何静态服务器都能托管。
- 数据只存在本机（IndexedDB），**不联网、不上传、不需要账号**。
- 断网可用（Service Worker 预缓存整个外壳，不到 100KB）。

## 怎么用

1. **电脑端**：MedKit →「题库」→ 某个题库的 `⋯` → **导出手机同步包**
   （得到 `medkit-bundle-YYYYMMDD-HHMM.json`，含题目、答案、作答记录、错题）。
2. **把文件传到手机**：微信「文件传输助手」发给自己 / QQ / 网盘 / 数据线，均可。
3. **手机端**：打开本应用 →「数据」→ **选择同步包文件**。
4. 开始刷题。答错的题会自动进错题本，并按考点聚类；到期的错题出现在「复习」。

> 重复同步是安全的：**已有的复习进度不会被覆盖**（只补新题、补错题、补作答流水）。

## 怎么托管

任何静态托管都行（内网 Nginx、GitHub Pages、Vercel…），只需把 `mobile/` 整个目录传上去。
`index.html` 是入口，无需构建步骤。

本地预览：

```bash
cd mobile
python -m http.server 8899
# 浏览器打开 http://127.0.0.1:8899
```

> Service Worker 只在 **https 或 localhost** 下生效；用局域网 IP 直接访问时，
> "离线可用"会降级（应用本身仍能正常用，只是不缓存）。

## 目录

```
mobile/
├── index.html              四个页面的外壳
├── css/app.css             全部样式（含深色模式、安全区、44px 命中区）
├── js/db.js                IndexedDB 薄封装
├── js/sm2.js               SM-2 间隔重复（与桌面端逐值一致）
├── js/model.js             同步包导入 / 作答记账 / 错题归纳 / 复习队列
├── js/drill.js             作答与复习的渲染
├── js/app.js               路由与页面
├── manifest.webmanifest    可"添加到主屏幕"
├── sw.js                   离线外壳
└── test/
    ├── sm2_golden.mjs      SM-2 一致性黄金值（node mobile/test/sm2_golden.mjs）
    └── sample-bundle.json  示范同步包（10 题 + 1 错题，可直接导入体验）
```

## 一致性约束

手机端与电脑端共用**同一条记忆曲线**。`js/sm2.js` 是
`medkit/core/vault_index.py` 的严格移植（含 Python `round()` 的 half-to-even 语义）：
任何一侧改常量或舍入方式，`node mobile/test/sm2_golden.mjs` 都会红。
