# push_github.ps1 —— 把本地仓库推到 GitHub
# 用法（二选一）：
#   1) 先在 GitHub 网页上建好空仓库，然后：
#      .\push_github.ps1 -Repo https://github.com/你的用户名/meshkit-ai.git
#      （会弹出登录窗口，用浏览器授权即可，不需要手动输 token）
#   2) 用 token：
#      .\push_github.ps1 -Repo https://github.com/你的用户名/meshkit-ai.git -Token ghp_xxx
param(
  [Parameter(Mandatory=$true)][string]$Repo,
  [string]$Token = ""
)

$ErrorActionPreference = 'Stop'
$git = 'D:\ai\tools\mingit\cmd\git.exe'
if (-not (Test-Path $git)) { throw "找不到 git: $git" }

$proj = 'D:\ai\meshkit'
Set-Location $proj

# 远程地址（有 token 时临时拼进 URL，不写入配置）
$pushUrl = $Repo
if ($Token) {
  $pushUrl = $Repo -replace '^https://', "https://x-access-token:$Token@"
}

$hasRemote = (& $git remote) -contains 'origin'
if ($hasRemote) {
  & $git remote set-url origin $Repo
} else {
  & $git remote add origin $Repo
}

Write-Host "推送到 $Repo ..."
& $git -c credential.helper= -c core.askPass= push -u $pushUrl main

if ($LASTEXITCODE -eq 0) {
  Write-Host "`n推送成功。" -ForegroundColor Green
  & $git remote set-url origin $Repo
  & $git log --oneline -1
} else {
  Write-Host "`n推送失败，请检查仓库地址/权限。" -ForegroundColor Yellow
  Write-Host "若需要登录：先执行  & '$git' config --global credential.helper manager"
}
