@echo off
chcp 65001 >nul
cd /d %~dp0

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python。请安装 Python 3.11+，并勾选 "Add Python to PATH"。
  echo        下载地址：https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import fastapi, uvicorn, openai, multipart, fitz, docx, markdown, httpx" >nul 2>nul
if errorlevel 1 (
  echo [提示] 首次运行需要安装依赖，请先执行：
  echo        pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  echo        安装完成后再双击本文件。
  pause
  exit /b 1
)

echo 正在启动 MedKit ...（端口 4880，被占用时自动回退 4881-4889）
python run_medkit.py
pause
