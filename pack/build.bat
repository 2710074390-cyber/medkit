@echo off
chcp 65001 >nul
rem 脚本位于 pack\，spec/iss/资源都在仓库根目录 → 切到上一级
cd /d "%~dp0.."

rem R8+W：允许指定**干净解释器**（推荐做法，见 pack\BUILD-ENV.md）。
rem 不指定时用 PATH 上的 python —— 但那可能是装过 pytest/playwright 的开发环境，
rem PyInstaller 会把它们连带打进产物（0.10.3 混入 attrs/email-validator/itsdangerous 即此因）。
if defined MEDKIT_BUILD_PYTHON (
  set "PY=%MEDKIT_BUILD_PYTHON%"
  echo [信息] 使用指定解释器：%PY%
) else (
  set "PY=python"
)

where "%PY%" >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python（%PY%），请先安装 Python 3.11+，或用 MEDKIT_BUILD_PYTHON 指定。
  pause
  exit /b 1
)

"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo [提示] 首次构建需要 PyInstaller：
  echo        "%PY%" -m pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
  pause
  exit /b 1
)

echo.
echo === 构建前环境体检（防测试/开发专用依赖混入产物）===
"%PY%" pack\check-build-env.py
if errorlevel 1 (
  echo.
  echo [错误] 当前解释器装了测试/开发专用依赖，拒绝构建。
  echo        请按上方提示改用干净虚拟环境；确要临时构建可先跑：
  echo          "%PY%" pack\check-build-env.py --allow-dirty
  echo        但产物可能不纯净，出包后必须核对 check-package.py。
  pause
  exit /b 1
)

echo === 清理旧产物 ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === PyInstaller 构建（onedir 绿色版）===
"%PY%" -m PyInstaller --noconfirm --clean medkit.spec
if errorlevel 1 (
  echo [错误] 构建失败，请查看上方日志。
  pause
  exit /b 1
)

echo.
echo === 打包纯净检查（WP-12：无样例/种子/测试数据）===
"%PY%" pack\check-package.py
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
"%PY%" -c "import re, pathlib; t = pathlib.Path(r'medkit/__init__.py').read_text(encoding='utf-8'); v = re.search(r'__version__\s*=\s*[\"']([^\"']+)[\"']', t).group(1); pathlib.Path(r'pack/version.iss').write_text('#define MyAppVersion \"' + v + '\"\n', encoding='utf-8'); print('pack/version.iss =', v)"
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

echo.
echo === 代码签名（可选：需证书）===
rem R8+W / M5-07：签名步骤原先没有任何脚本承载，于是每换一台机器都要重新摸索。
rem 现在固化在这里：设置 MEDKIT_SIGN_THUMBPRINT 环境变量即自动签名，否则明确跳过（不是静默）。
rem ⚠️ 必须在 make_release.py **之前**——签名会改变文件字节，zip 与 SHA256 清单要覆盖签名后的产物。
if defined MEDKIT_SIGN_THUMBPRINT (
  powershell -NoProfile -ExecutionPolicy Bypass -File pack\sign-release.ps1 -Thumbprint "%MEDKIT_SIGN_THUMBPRINT%"
  if errorlevel 1 (
    echo [错误] 代码签名失败（产物未签名，请勿发布）。
    pause
    exit /b 1
  )
) else (
  echo [提示] 未设置 MEDKIT_SIGN_THUMBPRINT，跳过签名。
  echo        产物将未签名，Windows 首次运行可能弹 SmartScreen。签名见 pack\sign-release.ps1 头部说明。
)

echo.
echo === 生成绿色版 zip + SHA256 清单（S2-22 / M5-07）===
rem R8+W：原脚本只出 dist\MedKit 与安装包，绿色版 zip 与校验清单**没有脚本**——
rem 2026-09-20 出 0.10.4 时这两步是手工做的，于是「sha256 清单」在审查里长期挂着。
"%PY%" pack\make_release.py
if errorlevel 1 (
  echo [错误] 发布产物生成失败。
  pause
  exit /b 1
)

pause
