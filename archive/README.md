# archive/ —— 旧构建快照归档（不入库，见根 `.gitignore`）

> 2026-09-17 整理：把根目录下 16 个历史构建快照集中到这里，根目录条目从 27 收敛到 12。
> 2026-09-28 整理：`dist-installer/` 从 1.6 GB / 28 文件收敛到 **218 MB / 6 文件**。

## 目录结构

```
archive/
├── builds/          ← 原 build-old-*（PyInstaller 中间产物，9 个版本）
│   ├── 0.10.1/
│   ├── 0.10.2-pre-u10/
│   ├── 0.10.2-pre-u17/
│   ├── 0.10.3-pre-v09/ … -pre-v15/
├── dist/            ← 原 dist-old-*（**解压后的绿色版目录**，9 个版本）
│   ├── 0.10.1-pre-r5/
│   ├── 0.10.2-pre-u10/ …
│   └── 0.10.3-pre-v15/
└── dist-installer/  ← 2026-09-28 新增（**分发件**：安装包 exe + portable zip，11 个版本）
    ├── MedKit-Setup-0.9.0.exe + MedKit-0.9.0-portable.zip
    ├── … 0.10.0 / 0.10.1-pre-r5 / 0.10.2-pre-u17
    ├── 0.10.3-pre-v09 … -pre-v15（5 组）
    └── 0.10.3 / 0.10.4
```

> 目录名去掉了 `build-old-` / `dist-old-` 前缀（父目录已表达该语义）。
> 体积合计约 **2.75 GB**（builds ≈ 470 MB，dist ≈ 980 MB，dist-installer ≈ 1.3 GB）。

### `dist/` 与 `dist-installer/` 的区别（别当成冗余）

| | 内容 | 形态 |
|---|---|---|
| `archive/dist/<ver>/` | **解压后的目录树**，顶层直接是 `MedKit/` | 绿色版，未打包 |
| `archive/dist-installer/` | **分发件**：`MedKit-Setup-<ver>.exe` + `MedKit-<ver>-portable.zip` | 已打包，834+ 条目 |

两者**形态不同、不是重复**——一个用于直接读文件树，一个用于验分发件本身。


## 用途与边界

- **仅作打包机制取证**：查「某版本的 spec/iss/excludes 当时怎么配的」「产物里到底含什么」。
- ⚠️ **不得作为结论依据**：审查/验收报告一律引用 `HEAD` 的 `file:line`；
  旧产物只用于回溯打包行为（与 `docs/reviews/问题清单_供应链构建发布_2026-09-17.md` §0 的口径一致）。
- **当前活跃产物**仍在根目录：`build/`（中间产物）、`dist/MedKit/`（绿色版）、
  `dist-installer/`（安装包，2026-09-28 收敛后约 218 MB）。

## 维护

- 新增出包留档请直接放到 `archive/builds/` / `archive/dist/` / `archive/dist-installer/` 下，
  **不要再在根目录创建 `*-old-*`**。
- **`dist-installer/` 只留两代**：当前版本 + 上一代（作回归对照）。
  更早的产物移到 `archive/dist-installer/`。
- **入库范围**：`.gitignore` 规则是 `archive/*` + `!archive/README.md` ——
  **仅本文件入库**，`builds/` / `dist/` / `dist-installer/` 的实际产物均被忽略
  （保留本文件是为了让「为什么有 archive/」这件事不失传）。
  需要长期保留的历史产物请另行备份（本目录不随仓库分发）。
