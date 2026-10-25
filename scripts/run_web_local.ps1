# 一键在本机跑网页（离线 mock，不需要任何密钥，不调用真实模型）。
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_web_local.ps1
#
# 做的事：
#   1. 用只含已提交代码的工作树 C:\dev\hs-e5-try（HEAD）跑，不受别的窗口未提交改动的影响；已存在就更新到当前 HEAD。
#   2. 在三个新窗口里分别起：服务接口（127.0.0.1:8765）、worker、网页开发服务器（http://localhost:5173）。
#      关掉那三个窗口就是停止。
#   3. 第一次会在 web/ 里按锁文件装依赖（npm ci，约 1–2 分钟）。
#   4. 服务就绪后建一条专属链接，并直接用默认浏览器打开。链接只显示这一次，不要贴进日志或截图。
#
# Python 用主工作区的 .venv（已经 uv sync 过）；Node 要 22。
param(
  [string]$Repo = (Split-Path -Parent $PSScriptRoot),
  [string]$Try = "C:\dev\hs-e5-try",
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$py = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "找不到 $py：先在仓库根目录运行 uv sync" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "找不到 npm：需要 Node 22" }

# 1. 只含已提交代码的工作树
if (Test-Path (Join-Path $Try ".git")) {
  Write-Host "更新工作树 $Try 到当前 HEAD"
  git -C $Try checkout --detach (git -C $Repo rev-parse HEAD) | Out-Null
} else {
  Write-Host "建立工作树 $Try（当前 HEAD）"
  git -C $Repo worktree add --detach $Try HEAD | Out-Null
}
$web = Join-Path $Try "web"
$cfg = "configs/product/service_local_mock.yaml"

# 2. 网页依赖（按锁文件）
if (-not (Test-Path (Join-Path $web "node_modules"))) {
  Write-Host "第一次：在 web/ 里按锁文件安装依赖……"
  Push-Location $web
  try { npm ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw "npm ci 失败" } } finally { Pop-Location }
}

# 有回应就算起来了（服务接口没登录会回 401，也是回应）。Vite 只监听 localhost（这台机器上是 ::1），所以按主机名问 HTTP，不按 IP 探端口。
function Test-Http([string]$Url) {
  try { $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3; return $true }
  catch { return ($null -ne $_.Exception.Response) }
}
function Wait-Http([string]$Url, [string]$What) {
  for ($i = 0; $i -lt 120; $i++) {
    if (Test-Http $Url) { return }
    Start-Sleep -Seconds 1
  }
  throw "$What 120 秒内没有起来（看对应窗口里的报错）"
}

function Start-Window([string]$Title, [string]$Dir, [string]$Command) {
  $inner = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$Dir'; $Command"
  Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $inner | Out-Null
}

# 3. 三个窗口（已经在跑的就不再开第二份）
if (Test-Http "http://127.0.0.1:8765/api/v1/capabilities") {
  Write-Host "服务接口已经在 8765 上运行，直接用（worker 请确认也在跑）"
} else {
  Start-Window "homo-spatialis · 服务接口 8765" $Try "& '$py' scripts\product_service.py api --config $cfg"
  Start-Window "homo-spatialis · worker" $Try "& '$py' scripts\product_service.py worker --config $cfg"
}
if (Test-Http "http://localhost:5173/") {
  Write-Host "网页已经在 5173 上运行，直接用"
} else {
  Start-Window "homo-spatialis · 网页 5173" $web "npm run dev"
}

Write-Host "等服务接口……"; Wait-Http "http://127.0.0.1:8765/api/v1/capabilities" "服务接口"
Write-Host "等网页……";     Wait-Http "http://localhost:5173/" "网页"

# 4. 专属链接
Push-Location $Try
try {
  $out = & $py scripts\product_service.py invite create --label "本地试用" --config $cfg 2>&1
} finally { Pop-Location }
$link = ($out | Where-Object { $_ -match "#token=" } | Select-Object -First 1)
if (-not $link) { Write-Host ($out -join "`n"); throw "没有拿到专属链接" }
$link = "$link".Trim()

Write-Host ""
Write-Host "一切就绪。专属链接（只显示这一次）："
Write-Host "  $link"
Write-Host "服务、worker、网页各在一个新窗口里；关掉窗口即停止。数据在 $Try\runs\product-service-local。"
if (-not $NoBrowser) { Start-Process $link }
