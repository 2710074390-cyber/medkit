# ADR-007 · 许可决策：以 GNU AGPL-3.0 开源发布

- 状态：已接受（2026-09-15，AGPL 开源决策）
- 背景：
  - README 于 v0.6 起对外宣称「开源至 github.com/2710074390-cyber/medkit」，但仓库根一直**没有 `LICENSE`**
    ——发布声明与实际许可状态长期悬置（`THIRD_PARTY_NOTICES.md` 亦记录了「本项目目前未声明许可」）。
  - 引擎关键依赖 **PyMuPDF 为 AGPL-3.0 / Artifex 商业双授权**（强传染性）。此前工程侧记录「**声明 MIT 会与分发
    AGPL 组件自相矛盾**，LICENSE 暂缓写入」，把许可选择与 PyMuPDF 决策耦合悬置，等待产品侧定案。
  - 本次由产品/授权方**明确决策：接受 AGPL 开源**——即以 AGPL-3.0 作为本项目整体许可证，与 PyMuPDF 的
    传染性条款走向一致，从而解开「MIT/AGPL 自相矛盾」的结。
- 决策：
  1. 本项目以 **GNU Affero General Public License v3.0（AGPL-3.0）** 发布；仓库根新增 `LICENSE`
     （gnu.org canonical 全文，逐字对照，`END OF TERMS AND CONDITIONS` 结尾完整）。
  2. `THIRD_PARTY_NOTICES.md` 的 PyMuPDF 条目由「★需产品决策」降级为「已决策项」：项目同为 AGPL，
     许可口径一致；仍保留提示——以 AGPL 组件提供**网络服务**时须遵守 AGPL §13 的源码提供义务，
     本项目**不提供**面向公众的网络化服务（本地桌面应用，API 仅限本机回环），触发风险低。
  3. `LICENSE` 随**安装包/绿色版分发**（`medkit.spec` datas 追加 `("LICENSE", ".")`），
     落实 AGPL「随分发提供许可证副本」义务；第三方依赖的 `dist-info/LICENSE*` 由
     `THIRD_PARTY_NOTICES.md` + `pack/check-package.py` 兜底。
  4. 派生/再分发时：修改源码须以 AGPL 许可公开，并提供获取源码的可操作路径（README 指向本仓库）。
- 验证：
  - 仓库根存在 `LICENSE`（AGPL-3.0 全文）；`grep -n "AGPL" README.md THIRD_PARTY_NOTICES.md docs/adr/ADR-007-license-agpl.md` 有稳定引用；
  - `grep -n "(\"LICENSE\"" medkit.spec` 命中打包兜底；
  - 该决策不再反向改写 PyMuPDF 类依赖（许可一致即无需替换）。
- 后果：
  - 正面：开源声明落地、与 PyMuPDF 许一致、满足「随分发提供许可证」义务；验收项 `U-16` 五件套中的
    `LICENSE` 缺口补齐。
  - 负面：AGPL 为强 copyleft——任何对产物的再分发/网络化部署需承担源码公开义务；对依赖方需保留
    第三方各自许可（见 `THIRD_PARTY_NOTICES.md`）。接受此约束。
- 关联：`THIRD_PARTY_NOTICES.md`（许可证清单）· `U-16`（依赖治理）· 核查报告「PyMuPDF AGPL 决策」条目
  · README v0.6「开源至」声明。

## 补记（2026-09-17，R8+W 审查 S2-25）：同源公网站点的 §13 口径 —— **待产品/法务定版，本 ADR 不预设结论**

> 背景：侧栏外链的 **med-review-site**（`README.md:24-25`、`web/index.html:53-57`）是公网站点，
> 天然满足 AGPL §13「通过计算机网络远程交互」要件。它是否**触发** §13 的源码提供义务，
> 唯一变量是「站点是否分发/运行了 Program 的修改版」。而仓库内对「同源」有两种并存的读法：
>
> - **读法 A（同源 = 产物数据格式）**：站点是独立静态前端，仅与本软件产物按同一 JSON schema 互通
>   （`borrow-rules.md:59`「数据互通导入（WP-11）」）→ **§13 不触发**，一句澄清即可闭环。
> - **读法 B（同源 = 代码派生）**：站点复用本仓 `medkit/web` 或 `render` 的代码 → 站点**必须**显著
>   提供 Corresponding Source 并附 §5 修改声明；而本仓**零处**披露站点源码获取途径
>   （全仓 grep 无站点源仓链接）→ 若为读法 B，则为实际不合规。
>
> **本 ADR 的处理**：不代为定版。上面两种读法在文档层面**长期并存**（`README.md:24-25` 的
> 「同源同规格」与 `borrow-rules.md:59` 的「同源项目」），这是本项风险的来源本身。
>
> **待办（移交产品/法务）**：
> 1. **R1 定版「同源」语义**（唯一的前置决策）——定版后其余项一次闭环；
> 2. R2：若为读法 B → 站点侧履行 §13 源码提供 + §5 修改声明；
> 3. R3：本 ADR 按定版结果补「同源站口径」结论段（本补记届时收敛为历史记录）；
> 4. R4：`README.md:24-25` 与 `web/index.html:53-57` 的外链按定版口径补一句说明；
> 5. R5：`THIRD_PARTY_NOTICES.md` 补「同源站使用场景」小节。
>
> 桌面版自身不受影响：§13 对本软件不触发（`run_medkit.py:112` 仅绑回环 + `main.py:137-138`
> Host 守卫），§4/§6 义务已履行（`LICENSE` 全文随包、公开仓库、Releases 跳转链）。