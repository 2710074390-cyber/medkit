# -*- mode: python ; coding: utf-8 -*-
"""MedKit PyInstaller spec：绿色免安装版（onedir）。

构建：pyinstaller --noconfirm medkit.spec
产物：dist/MedKit/MedKit.exe（双击启动，自动打开浏览器 http://127.0.0.1:4880）
"""

from PyInstaller.utils.hooks import collect_submodules

# uvicorn 动态导入的模块 + 第三方可执行模块，显式收集
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "multipart", "markdown", "docx", "fitz", "openai",
]
hiddenimports += collect_submodules("pydantic")

datas = [
    ("medkit/web", "medkit/web"),        # 静态前端（零 CDN）
    ("medkit/prompts", "medkit/prompts"),  # 四个提示词模板（MedGen/QC/Fix/Review）
    ("medkit/data", "medkit/data"),      # 示例素材（/api/sample）
]

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
