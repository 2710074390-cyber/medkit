@echo off
chcp 65001 >nul
rem 脚本位于 pack\，spec/iss/资源都在仓库根目录 → 切到上一级
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python，请先安装 Python 3.11+。
  pause
  exit /b 1
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo [提示] 首次构建需要 PyInstaller：
  echo        pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
  pause
  exit /b 1
)

echo === 清理旧产物 ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === PyInstaller 构建（onedir 绿色版）===
python -m PyInstaller --noconfirm --clean medkit.spec
if errorlevel 1 (
  echo [错误] 构建失败，请查看上方日志。
  pause
  exit /b 1
)

echo.
echo === 打包纯净检查（WP-12：无样例/种子/测试数据）===
python pack\check-package.py
if errorlevel 1 (
  echo [错误] 打包检查未通过：dist 含违规数据（样例/种子/测试/字节码）。
  pause
  exit /b 1
)

echo.
echo === 构建完成 ===
echo 绿色版目录：dist\MedKit\
echo 使用：双击 dist\MedKit\MedKit.exe（或复制整个 MedKit 文件夹到别处使用）
echo 验证：启动后浏览器打开 http://127.0.0.1:4880

echo === 生成版本文件（单源：medkit/__init__.py __version__）===
python -c "import re, pathlib; t = pathlib.Path(r'medkit/__init__.py').read_text(encoding='utf-8'); v = re.search(r'__version__\s*=\s*[\"']([^\"']+)[\"']', t).group(1); pathlib.Path(r'pack/version.iss').write_text('#define MyAppVersion \"' + v + '\"\n', encoding='utf-8'); print('pack/version.iss =', v)"
if errorlevel 1 (
  echo [错误] 版本文件生成失败。
  pause
  exit /b 1
)

echo === 构建 Inno Setup 安装包（可选）===
set ISCC=%LOCALAPPDATA%\Programs\Inno\ISCC.exe
if not exist "%ISCC%" ( set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe )
if not exist "%ISCC%" ( set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe )
if not exist "%ISCC%" ( set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe )
if exist "%ISCC%" (
  "%ISCC%" medkit.iss
  echo 安装包：dist-installer\MedKit-Setup-*.exe
) else (
  echo [提示] 未找到 Inno Setup 编译器（ISCC.exe），跳过安装包构建。
  echo        需要时装：下载 ghproxy.net 上的 innosetup 安装器或从 jrsoftware.org 获取。
)
pause
