# MedKit v0.6.0 设计方案

日期:2026-08-26 · 状态:已批准

## 目标

1. 侧栏「题库与手册站」外链按钮(→ https://med-review-site.pages.dev/#reviews)+ 邮件反馈(2710074390@qq.com)
2. 内置更新检查(基于 GitHub Releases,仅提醒 + 跳转下载页)
3. 新图标(同网站图标结构,仅配色/字体风格改变)
4. 清理归档 → 打包 v0.6.0 → 推送 GitHub 并发布 Release

## 1. 跳转按钮 + 邮件反馈(零 API 成本)

- 侧栏 nav「提示词与规则」下方加分隔线 + 外链 `<a class="navext">`「题库与手册站」,target=_blank
- 图标:#i-book(书)+ #i-ext(外链小箭头),需要新增两个 SVG symbol
- 反馈入口:侧栏 sidefoot 主题按钮旁加邮件图标按钮(themeBtn 同款样式)
- 点击 → 反馈弹窗(复用 modal_mask):
  - 展示邮箱 + 复制按钮(navigator.clipboard + execCommand 降级)
  - 「写邮件」按钮 → mailto:2710074390@qq.com,主题 `MedKit vX.Y.Z 反馈`,正文自动附版本/系统/日期
  - 版本号从 /api/health 已有数据取(state 缓存)

## 2. 内置更新(仅提醒 + 跳转下载页)

### 后端

- `medkit/core/update.py`(纯逻辑,可测):
  - `GITHUB_REPO = "2710074390-cyber/medkit"` 常量
  - `_version_tuple(v)`:剥离 v 前缀,按点分段取数字 → 元组(补零对齐)
  - `is_newer(latest, current)`:元组比较
  - `check(timeout=8)`:httpx GET `https://api.github.com/repos/{repo}/releases/latest`,
    返回 `{current, latest, has_update, html_url, notes(≤800字), published_at}`;
    任何异常 → `{has_update: false, error: "network", html_url: releases页}` 优雅降级
- `medkit/routers/update.py`:`GET /api/update/check` → 调 check()
- main.py 挂载 r_update.router

### 前端

- 启动后延迟 4s 静默检查;has_update → 侧栏版本号加红点(class updot)+ toast「发现新版本 vX.Y.Z · 查看更新」(点击打开更新弹窗)
- 点击侧栏版本号 → 更新弹窗(复用 modal_mask):
  - 有新版:当前/最新版本 + 更新日志摘要 + 「打开下载页」(window.open html_url)
  - 已最新:「已是最新版本 vX.Y.Z」
  - 检查失败:「网络检查失败」+ 「打开发布页」兜底按钮

## 3. 新图标

网站图标(SVG):32×32 圆角方块 rx=7,蓝底 #679efe,等宽粗体「MW」#0a0a0a。
MedKit 图标:同结构(圆角方块 + MW 字标),改为 **青绿渐变(135°: #2dd4bf → #0d9488 → #134e4a)+ 白字 Segoe UI Bold**。
用 Pillow 逐尺寸原生绘制(16/24/32/48/64/128/256)生成 medkit.ico 覆盖原文件(spec/iss 引用不变)。

## 4. 清理归档 → 打包 → 推送

- `archive/installers/`(gitignore):收纳旧安装包 0.2.0~0.5.0
- `archive/`:收纳根目录两份旧报告 HTML(入 git 跟踪)
- 版本 0.5.0 → 0.6.0(__init__.py 单源)
- 先提交当前未提交的 v0.5 尾部改动,再提交 v0.6 功能
- GitHub 新建公开仓库 `2710074390-cyber/medkit`,push master
- pack/build.bat 打包 → dist-installer/MedKit-Setup-0.6.0.exe
- gh release create v0.6.0 上传安装包,Release notes 列新功能

## 验收

- pytest 全量通过(含新增 update 模块单测:版本比较 + 端点 mock)
- 浏览器双主题:外链按钮渲染/链接正确、反馈弹窗 mailto 生成、更新检查红点/弹窗(mock)
- medkit.ico 多尺寸正常,exe/安装包图标更新
- GitHub 仓库代码完整、Release v0.6.0 附安装包
- /api/update/check 在无网/无 Release 场景不 5xx
