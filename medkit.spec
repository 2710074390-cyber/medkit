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
    ("medkit/data", "medkit/data"),      # 示例素材（/api/sample）
    ("data/syllabus_seed_306.json", "data"),  # WP-01：内置西综306 大纲种子（core/syllabus.py:28 按 仓库根 data/ 解析）
]
# NX-02（R-3）：jieba 词典（dict.txt ≈5MB）不随包自动收集——缺词典则 FTS 分词静默退化
datas += collect_data_files("jieba")

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
