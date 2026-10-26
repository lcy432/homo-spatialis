# 测试站（真实规划器，经 Tailscale Funnel 对外）的启动 / 停止 / 更新。开机自启动时由“启动”文件夹里的 homo-spatialis-site.cmd 调用。
#
#   powershell -ExecutionPolicy Bypass -File C:\dev\hs-site\scripts\site_start.ps1            启动（已在跑的先停掉再起）
#   powershell -ExecutionPolicy Bypass -File C:\dev\hs-site\scripts\site_start.ps1 -Stop      停止
#   powershell -ExecutionPolicy Bypass -File C:\dev\hs-site\scripts\site_start.ps1 -Update    把站点工作树换到主工作区 main 的最新提交、重装依赖、重新构建网页，再启动
#   powershell -ExecutionPolicy Bypass -File C:\dev\hs-site\scripts\site_start.ps1 -Install   在“启动”文件夹里放开机自启动的入口（-Uninstall 删掉）
#
# 做的事：
#   1. 站点代码来自只含已提交代码的工作树 C:\dev\hs-site（不受别的窗口未提交改动影响）；数据在 C:\dev\hs-site-data（配置里写的）。
#   2. 从主工作区的 .env 只取 DASHSCOPE_API_KEY，放进这个进程的环境再传给服务与 worker；不打印、不写日志、不复制 .env。
#   3. 后台起三个进程（不开窗口）：服务接口 127.0.0.1:8766、worker、网页预览 127.0.0.1:4173（/api 代理到 8766）。日志在数据目录的 logs\。
#   4. 确认 Tailscale Funnel 把 443 转到 4173（已经开着就不动）。
# Python 用主工作区的 .venv；Node 要 22。电脑睡眠或关机时站点就停了。
param(
  [string]$Repo = "C:\dev\homo-spatialis",
  [string]$Site = "C:\dev\hs-site",
  [string]$Config = "configs/product/service_tailscale_live.yaml",
  [string]$PublicHost = "your-machine.your-tailnet.ts.net",
  [int]$ApiPort = 8766,
  [int]$WebPort = 4173,
  [switch]$Stop,
  [switch]$Update,
  [switch]$Install,
  [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$py = Join-Path $Repo ".venv\Scripts\python.exe"
$web = Join-Path $Site "web"
$data = "C:\dev\hs-site-data"
$logs = Join-Path $data "logs"
$tailscale = "C:\Program Files\Tailscale\tailscale.exe"
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "homo-spatialis-site.cmd"

function Log([string]$Msg) {
  New-Item -ItemType Directory -Force $logs | Out-Null
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
  Add-Content -Path (Join-Path $logs "site.log") -Value $line -Encoding UTF8
  Write-Host $Msg
}

function Stop-Site {
  $mine = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
      ($_.CommandLine -match "product_service\.py (api|worker)" -and $_.CommandLine -match [regex]::Escape($Config)) -or
      ($_.CommandLine -match "vite(\.js)?\S*\s+preview" -and $_.CommandLine -match "--port $WebPort")
    )
  }
  foreach ($p in $mine) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  if ($mine) { Log ("停掉了 {0} 个站点进程" -f @($mine).Count) }
}

if ($Install) {
  Set-Content -Path $startup -Encoding ASCII -Value "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Site\scripts\site_start.ps1`""
  Write-Host "已放开机自启动入口：$startup（登录 Windows 后自动启动测试站）"
  return
}
if ($Uninstall) {
  Remove-Item -Path $startup -ErrorAction SilentlyContinue
  Write-Host "已删除开机自启动入口：$startup"
  return
}

Stop-Site
if ($Stop) { Log "测试站已停止"; return }

if ($Update) {
  $head = (git -C $Repo rev-parse main).Trim()
  Log "更新站点工作树到 main $head"
  git -C $Site checkout --detach $head | Out-Null
  Push-Location $web
  try {
    npm ci --no-audit --no-fund | Out-Null; if ($LASTEXITCODE -ne 0) { throw "npm ci 失败" }
    npm run build | Out-Null; if ($LASTEXITCODE -ne 0) { throw "网页构建失败" }
  } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $web "dist\index.html"))) { throw "没有构建好的网页：先用 -Update" }

# 只取需要的那个密钥
$envFile = Join-Path $Repo ".env"
$keyLine = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "^\s*DASHSCOPE_API_KEY\s*=" } | Select-Object -First 1
if (-not $keyLine) { throw "主工作区的 .env 里没有 DASHSCOPE_API_KEY" }
$env:DASHSCOPE_API_KEY = ($keyLine -replace "^\s*DASHSCOPE_API_KEY\s*=\s*", "").Trim().Trim('"').Trim("'")
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
$env:E5_PUBLIC_HOST = $PublicHost
$env:E5_API_TARGET = "http://127.0.0.1:$ApiPort"

New-Item -ItemType Directory -Force $logs | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
foreach ($role in "api", "worker") {
  Start-Process -FilePath $py -ArgumentList "scripts\product_service.py", $role, "--config", $Config -WorkingDirectory $Site -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "$role-$stamp.out.log") -RedirectStandardError (Join-Path $logs "$role-$stamp.err.log") | Out-Null
}
Start-Process -FilePath "node" -ArgumentList "node_modules\vite\bin\vite.js", "preview", "--host", "127.0.0.1", "--port", "$WebPort", "--strictPort" -WorkingDirectory $web -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $logs "web-$stamp.out.log") -RedirectStandardError (Join-Path $logs "web-$stamp.err.log") | Out-Null
Remove-Item Env:DASHSCOPE_API_KEY

function Test-Http([string]$Url) {
  try { $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3; return $true }
  catch { return ($null -ne $_.Exception.Response) }
}
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
  if ((Test-Http "http://127.0.0.1:$ApiPort/api/v1/session") -and (Test-Http "http://127.0.0.1:$WebPort/")) { $ok = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $ok) { Log "服务接口或网页 90 秒内没有起来：看 $logs 里 $stamp 的日志"; exit 1 }

# Funnel：已经把 443 转到这个端口就不动；没有就打开（第一次申请证书要几十秒）
$status = & $tailscale funnel status 2>&1 | Out-String
if ($status -notmatch "127\.0\.0\.1:$WebPort") {
  Log "打开 Tailscale Funnel → $WebPort"
  $p = Start-Process -FilePath $tailscale -ArgumentList "funnel", "--bg", "$WebPort" -WindowStyle Hidden -PassThru
  if (-not $p.WaitForExit(120000)) { $p.Kill(); Log "Funnel 两分钟没有打开：检查 Tailscale 管理台的 HTTPS 与 Funnel 设置" }
}
Log "测试站已启动：https://$PublicHost （服务 $ApiPort、网页 $WebPort、日志 $stamp）"
