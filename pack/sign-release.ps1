# MedKit 发布物代码签名（R8+W / M5-07）
#
# 为什么有这个脚本：审查项 M5-07「无可复现 CI 构建/签名/sha256 清单」长期挂着，其中
# 「签名」一项一直没落地——不是不想做，而是**没有证书**，且签名步骤没有任何脚本承载，
# 于是每换一个人/一台机器都要重新摸索顺序。本脚本把顺序与参数固化下来：
# 有证书时一条命令签完，没证书时**明确报错退出**（绝不静默跳过）。
#
# ⚠️ 顺序很重要（build.bat 已按此接线）：
#   1. PyInstaller 出 dist/MedKit
#   2. 签名 dist/MedKit/MedKit.exe          ← 先签，因为 zip 是从这里打的
#   3. Inno Setup 出 dist-installer/MedKit-Setup-<ver>.exe
#   4. 签名安装包
#   5. pack/make_release.py 打 zip + 生成 SHA256SUMS ← **必须最后**，否则清单对不上签名后的字节
#
# 用法：
#   # 用证书存储里的证书（推荐：私钥不出存储）
#   powershell -File pack\sign-release.ps1 -Thumbprint <证书指纹>
#   # 用 PFX 文件
#   powershell -File pack\sign-release.ps1 -Pfx D:\cert.pfx -PfxPassword '***'
#
# 参数：
#   -Thumbprint    证书指纹（Cert:\CurrentUser\My 或 Cert:\LocalMachine\My）
#   -Pfx / -PfxPassword   也可改用 PFX 文件
#   -TimestampUrl  时间戳服务（默认 DigiCert RFC3161）。**强烈建议保留**：
#                  否则证书过期后已签名的产物会一并失效。
#   -SkipInstaller 只签 dist/MedKit（安装包尚未构建时）

# 参数刻意**不设 Mandatory**：Mandatory 会让缺参时进入交互式询问，CI/批处理里直接挂死
# （实测：不带参数运行会停在 "请为以下参数提供值: Thumbprint:"）。改为手动校验 + 快速失败。
param(
  [string]$Thumbprint,
  [string]$Pfx,
  [string]$PfxPassword,
  [string]$TimestampUrl = 'http://timestamp.digicert.com',
  [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Fail($msg) { Write-Host "[失败] $msg" -ForegroundColor Red; exit 1 }

if (-not $Thumbprint -and -not $Pfx) {
  Fail ("必须提供 -Thumbprint 或 -Pfx/-PfxPassword 之一（本脚本不做交互式询问）。`n" +
        "  例：powershell -File pack\sign-release.ps1 -Thumbprint <证书指纹>`n" +
        "  例：powershell -File pack\sign-release.ps1 -Pfx D:\cert.pfx -PfxPassword '***'")
}
$usePfx = [bool]$Pfx

# ---- 1. 找 signtool ----
$candidates = @()
$candidates += Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue
$candidates += Get-ChildItem 'C:\Program Files\Windows Kits\10\bin' -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue
$sdkSign = $candidates | Where-Object { $_.FullName -match 'x64' } | Select-Object -Last 1
if (-not $sdkSign) { $sdkSign = $candidates | Select-Object -Last 1 }
if ($sdkSign) {
  $signTool = $sdkSign.FullName
} else {
  $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($onPath) { $signTool = $onPath.Source } else {
    # 注意：这里刻意不用 here-string —— 它的收尾标记必须**顶格**，缩进进代码块里会解析失败。
    # （这条注释本身也别写出那个两字符标记：PowerShell 会把它当成收尾符，直接炸解析。踩过。）
    Fail ("未找到 signtool.exe。它随 Windows SDK 分发，安装方式（任选其一）：`n" +
          "    winget install Microsoft.WindowsSDK.10.0.22621`n" +
          "    或在 Visual Studio Installer 里勾选「Windows SDK」`n" +
          "装好后重跑本脚本。")
  }
}
Write-Host "signtool: $signTool"

# ---- 2. 组装签名参数 ----
$common = @('sign', '/fd', 'SHA256', '/tr', $TimestampUrl, '/td', 'SHA256', '/v')
if (-not $usePfx) {
  $found = @()
  $found += Get-ChildItem 'Cert:\CurrentUser\My' -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Thumbprint -eq $Thumbprint }
  $found += Get-ChildItem 'Cert:\LocalMachine\My' -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Thumbprint -eq $Thumbprint }
  if (-not $found) { Fail "证书存储里找不到指纹为 $Thumbprint 的**代码签名**证书（注意：普通 SSL 证书不能用于代码签名）。" }
  if ($found[0].NotAfter -lt (Get-Date)) { Fail "证书已于 $($found[0].NotAfter) 过期。" }
  Write-Host "证书: $($found[0].Subject)（到期 $($found[0].NotAfter.ToString('yyyy-MM-dd'))）"
  $common += @('/sha1', $Thumbprint)
} else {
  if (-not (Test-Path $Pfx)) { Fail "PFX 不存在：$Pfx" }
  Write-Host "证书: PFX $Pfx"
  $common += @('/f', $Pfx, '/p', $PfxPassword)
}

# ---- 3. 收集待签目标 ----
$version = (Select-String -Path 'medkit\__init__.py' -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if (-not $version) { Fail '无法从 medkit/__init__.py 读取版本号' }
Write-Host "版本: $version"

$targets = @()
$mainExe = Join-Path 'dist\MedKit' 'MedKit.exe'
if (-not (Test-Path $mainExe)) { Fail "未找到 $mainExe —— 请先跑 PyInstaller（pack\build.bat 前两步）。" }
$targets += (Resolve-Path $mainExe).Path

if (-not $SkipInstaller) {
  $setup = Join-Path 'dist-installer' "MedKit-Setup-$version.exe"
  if (-not (Test-Path $setup)) {
    Fail "未找到 $setup —— 请先跑 Inno Setup 出安装包（或加 -SkipInstaller 只签绿色版）。"
  }
  $targets += (Resolve-Path $setup).Path
}

# ---- 4. 逐个签名并**验证** ----
$failed = @()
foreach ($t in $targets) {
  Write-Host "`n=== 签名 $t ==="
  & $signTool @common $t
  if ($LASTEXITCODE -ne 0) { $failed += $t; continue }
  # 签完必须回验——只看 signtool 退出码不够（例如时间戳失败时仍可能返回 0）
  $sig = Get-AuthenticodeSignature -FilePath $t
  if ($sig.Status -ne 'Valid') {
    Write-Host "  签名状态: $($sig.Status)（非 Valid）" -ForegroundColor Yellow
    $failed += $t
  } else {
    Write-Host "  签名状态: Valid · 签名者: $($sig.SignerCertificate.Subject)" -ForegroundColor Green
  }
}

if ($failed.Count -gt 0) { Fail "以下产物签名未通过验证：`n  $($failed -join "`n  ")" }

Write-Host "`n[完成] 全部产物已签名并通过验证。" -ForegroundColor Green
Write-Host "提示：签名会改变文件字节，务必**之后再**跑 pack/make_release.py 生成 zip 与 SHA256SUMS.txt。"
exit 0
