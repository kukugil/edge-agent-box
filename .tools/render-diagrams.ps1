# 把 docs/学习笔记/图/ 下所有 .mmd 渲染成同名 .svg（笔记里引用）和 .png（贴飞书用）。
# 用法（项目根目录）：powershell -ExecutionPolicy Bypass -File .tools\render-diagrams.ps1
# 借用本机 Edge 当渲染浏览器，所以没下载 Chromium（配置见 mermaid\puppeteer-config.json）。
# 注意：在 Git Bash 里跑会启动浏览器失败，要用 PowerShell。
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$M = Join-Path $PSScriptRoot 'mermaid'
$mmdc = Join-Path $M 'node_modules\.bin\mmdc.cmd'
Get-ChildItem (Join-Path $root 'docs\学习笔记\图') -Filter *.mmd | ForEach-Object {
    $base = Join-Path $_.DirectoryName $_.BaseName
    & $mmdc -p "$M\puppeteer-config.json" -c "$M\mermaid-config.json" -b white -i $_.FullName -o "$base.svg" | Out-Null
    & $mmdc -p "$M\puppeteer-config.json" -c "$M\mermaid-config.json" -b white -s 2 -i $_.FullName -o "$base.png" | Out-Null
    Write-Output "已渲染 $($_.Name)"
}
