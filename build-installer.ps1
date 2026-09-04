param(
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

python -m PyInstaller --noconfirm --clean NewsForwarder.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败' }

if (-not $InnoCompiler) {
    $InnoCompiler = Join-Path $ProjectDir 'tools\inno\ISCC.exe'
}
if (-not (Test-Path -LiteralPath $InnoCompiler)) {
    throw "找不到 Inno Setup 编译器：$InnoCompiler"
}

& $InnoCompiler installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup 构建失败' }

Write-Host "安装程序已生成：$ProjectDir\installer-dist-1.2.1" -ForegroundColor Green
