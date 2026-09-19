# -*- mode: python ; coding: utf-8 -*-
"""MedKit PyInstaller spec：绿色免安装版（onedir）。

构建：pyinstaller --noconfirm medkit.spec
产物：dist/MedKit/MedKit.exe（双击启动，自动打开浏览器 http://127.0.0.1:4880）
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# uvicorn 动态导入的模块 + 第三方可执行模块，显式收集
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "multipart", "markdown", "docx", "fitz", "openai", "genanki",
]
hiddenimports += collect_submodules("pydantic")
# NX-02（R-3）：jieba 的 posseg/finalseg 等为运行时动态导入，不显式收集则打包后 ImportError
hiddenimports += collect_submodules("jieba")
# NX-04（WP-05）：scheduler.py 对 fsrs 为函数级懒导入（静态分析捕不到）——显式收集
hiddenimports += collect_submodules("fsrs")

datas = [
    ("medkit/web", "medkit/web"),        # 静态前端（零 CDN）
    ("medkit/prompts", "medkit/prompts"),  # 提示词模板（medgen/medqc/medfix/medreview/medexplain/medtutor/medcards/syllabus_extract）
    # U 开源决策：AGPL-3.0 许可证正文随安装包分发（遵守 AGPL「随分发提供许可证」要求）
    ("LICENSE", "."),
    # WP-12 纯净安装包：示例素材（medkit/data）与内置大纲种子（data/syllabus_seed_306.json）
    # 仅保留在仓库（开发/CI），不再打进 dist —— 用户自行上传教材/教师重点/官方大纲。
]
# NX-02（R-3）：jieba 词典（dict.txt ≈5MB）不随包自动收集——缺词典则 FTS 分词静默退化
datas += collect_data_files("jieba")


# S2-18 / S2-22（R8+W）：随产物保留各依赖的 dist-info（内含 LICENSE / COPYING 原文）。
# PyInstaller 默认**剥掉** dist-info，导致产物长期报「33 个已声明依赖缺 dist-info」——
# 而 THIRD_PARTY_NOTICES 明确承诺「随产物保留 LICENSE 原文」。这里按 requirements.lock
# 的**闭包**精确收集（不收集 pyinstaller 等构建期依赖，避免产物虚胖）。
def _lock_closure_names() -> set[str]:
    import re as _re
    from pathlib import Path as _P

    lock = _P("requirements.lock")
    if not lock.exists():
        return set()
    names: set[str] = set()
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = _re.match(r"^([A-Za-z0-9_.\-]+)==([^\s#\\]+)", line)
        if m:
            names.add(m.group(1).lower().replace("_", "-"))
    return names


def _dist_info_datas() -> list[tuple[str, str]]:
    import sysconfig
    from pathlib import Path as _P

    want = _lock_closure_names()
    if not want:
        return []
    purelib = _P(sysconfig.get_paths()["purelib"])
    out: list[tuple[str, str]] = []
    for d in sorted(purelib.glob("*.dist-info")):
        base = d.name[: -len(".dist-info")]
        nm = base.rsplit("-", 1)[0].lower().replace("_", "-")
        if nm in want:
            out.append((str(d), d.name))
    return out


datas += _dist_info_datas()

a = Analysis(
    ["run_medkit.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 与本应用无关的大件：cv2/pyarrow/onnxruntime/scipy/pandas/matplotlib/PIL 等
    # （可能是分析器过度收集环境里已装包所致；运行时不需要）
    excludes=[
        "tkinter", "unittest", "pytest",
        "cv2", "pyarrow", "onnxruntime", "scipy", "pandas",
        "matplotlib", "PIL", "numpy", "torch", "transformers",
        "sentence_transformers", "sklearn", "seaborn",
        "streamlit", "gradio", "crawl4ai", "jupyter", "IPython",
        "notebook", "plotly", "polars", "duckdb", "sqlalchemy",
        "cryptography", "Crypto",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MedKit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # 显示启动日志（uvicorn 输出）；用户可关闭窗口退出
    icon="medkit.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MedKit",
)
