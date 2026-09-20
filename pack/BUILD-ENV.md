# 构建环境与产物纯净性（BUILD-ENV）

> 面向：出包的人。目的：**保证安装包里只有运行时必需内容**，没有测试用例、测试数据、
> 测试报告、临时日志、调试文件或测试专用依赖。
>
> 相关脚本：`pack/check-build-env.py`（构建前体检）、`pack/check-package.py`（出包后检查）、
> `pack/make_release.py`（打 zip + SHA256 清单）、`pack/build.bat`（总入口）。

---

## 1. 曾经出过的问题（有证据）

2026-09-19 复核 v0.10.3 产物（`dist-installer/MedKit-0.10.3-portable.zip`）时发现，
产物里混进了**不在 `requirements.lock` 闭包内**的发行包：

| 包 | 说明 |
|---|---|
| `attrs` / `email-validator` / `importlib-metadata` / `itsdangerous` | `check-package.py` 报的「4 个未声明发行包」 |
| `tzdata`（605 个文件） | 时区库；本项目**代码里不用** `zoneinfo`/`pytz` |
| `chardet` / `charset_normalizer` / `brotli` | httpx 的**可选**依赖，本项目未声明 |
| `fontTools` / `pywin32` / `pywin32_system32` | 与本项目无关 |

**同一份源码在干净环境重建后，这些全部消失**（产物 123 MB → 114 MB，
`check-package.py --strict` 从「通过（有警告）」变为**完全通过**）——
证明它们不是代码依赖，而是**构建环境带来的**。

## 2. 根本原因（三条）

1. **构建解释器不隔离**（主因）：`build.bat` 原先直接用 PATH 上的 `python -m PyInstaller`。
   若该环境装过 pytest / playwright / pip-audit / freezegun 等开发依赖，
   PyInstaller 的依赖分析可能把它们（以及它们的传递依赖，如 `attrs`、`tzdata`）一并收集进产物。
   `pack/check-build-env.py` 就是为此而加。
2. **只有事后检查、没有构建前拦截**：`check-package.py` 原先在**构建完成之后**才跑，
   发现问题时产物已经出来了；而且它的黑名单**不覆盖**测试报告 / 覆盖率 / 日志 / 调试产物 /
   测试专用依赖（后四类完全没有检查）。
3. **spec 的 `excludes` 不全**：原先只排了 `pytest` / `unittest` / `tkinter` 与一批重型科学计算库，
   没排 `_pytest` / `pluggy` / `iniconfig` / `coverage` / `playwright` / `debugpy` 等
   ——它们是**不带 dist-info 也能被以裸模块目录收集**的类型，闭包检查会漏判。

## 3. 排除范围（明确清单）

### 3.1 目录 / 路径（`check-package.py` 黑名单，命中即失败）

| 类别 | 命中规则 |
|---|---|
| 学科/样例数据 | `syllabus_seed_306.json`、`samples`、`medkit/data` |
| 测试用例与夹具 | `tests/`、`conftest.py`、`fixtures/` |
| 测试报告 / 覆盖率 / 测试缓存 | `.coverage`、`coverage.xml`、`htmlcov`、`.pytest_cache`、`.benchmarks`、`junit` |
| 日志与临时/备份 | `.log`、`.tmp`、`.bak`、`~$` |
| 调试产物 | `.pdb`、`.dSYM`、`debugpy` |
| 字节码 | `__pycache__`、`.pyc`、`.pyo` |

### 3.2 测试源码文件（扫文件名，不限目录）

`test_*.py` / `*_test.py`（含其 `.pyc`/`.pyo`）——单测源码进产物既是信息泄露
（暴露内部断言与测试样例），也会让用户以为这是开发包。

### 3.3 测试/开发专用依赖（按模块名直接拦）

`_pytest`、`pytest`、`pluggy`、`iniconfig`、`coverage`、`coverage_html`、`mock`、`nose`、
`nose2`、`hypothesis`、`freezegun`、`playwright`、`pyee`、`debugpy`、`pip_audit`、`pip_api`、
`pytest_cov`、`pytest_timeout`、`mypy`、`ruff`、`black`、`isort`、`flake8`、`pylint`、
`pdb`、`bdb`、`doctest`、`cProfile`

> ⚠️ **必须保留**：`setuptools`（jieba 运行期 `import pkg_resources`）。
> 它虽然写在 `requirements-dev.txt` 里，但**属于运行时依赖**，`check-build-env.py` 有专门例外。

### 3.4 闭包一致性

产物 `_internal/` 下出现的发行包（以 `*.dist-info` 为准）必须是 `requirements.lock` 闭包的**子集**；
多出来的即「未声明包」。**例外清单刻意留空**（`ALLOWED_EXTRA_DISTS = frozenset()`）——
新增例外必须写进代码并说明理由，否则漂移会再次静默发生。

## 4. 配置调整方案（已落地）

| 位置 | 调整 |
|---|---|
| `pack/check-build-env.py`（新增） | 构建前体检：当前解释器若装了 dev 专用依赖 → **拒绝构建**（`--allow-dirty` 可放行但会警告）。判定口径 = `requirements-dev.txt` − `requirements.txt`/`requirements.lock` − 运行时例外 |
| `pack/build.bat` | ① 新增体检步骤（失败即中断）；② 支持 `MEDKIT_BUILD_PYTHON` 指定干净解释器；③ 全部调用统一走 `%PY%` |
| `medkit.spec` | `excludes` 补齐测试/开发专用包（见 3.3），在 PyInstaller 侧前置排除 |
| `pack/check-package.py` | 黑名单扩到六类（见 3.1）；新增「测试源码文件」与「测试专用依赖」两项检查 |

## 5. 推荐出包流程

```bat
:: 1) 干净环境（只装运行时依赖 + PyInstaller）
python -m venv .buildenv
.buildenv\Scripts\pip install --require-hashes -r requirements.lock
.buildenv\Scripts\pip install pyinstaller

:: 2) 指定干净解释器
set MEDKIT_BUILD_PYTHON=%CD%\.buildenv\Scripts\python.exe

:: 3) 构建（含体检 → PyInstaller → 纯净检查 → 版本文件 → Inno Setup → 签名(可选) → zip+清单）
pack\build.bat
```

## 6. 验证方法

```bat
:: 出包后（--strict：没产物即失败，避免 CI 里变成空操作）
python pack\check-package.py dist\MedKit --strict
```

判定通过的标准输出：

```
[通过] 纯净安装包检查：dist\MedKit（无样例/种子/测试/字节码/测试报告/日志/调试产物；无测试专用依赖；闭包无未声明包）
```

**反向自检**（确认检查器不是摆设）：往产物副本里塞一个 `tests/test_x.py` 或
`_internal/_pytest/`，检查器**必须**报 `[失败]`。该行为由
`tests/test_release_artifacts.py` 与 `tests/test_check_package.py` 的守卫用例锁定。
