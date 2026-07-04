# === EU4 KB 本機啟動腳本 ===
# 用法：
#   1) 先取得訂閱 OAuth token（只需做一次）：
#        claude setup-token        # 瀏覽器登入後會給你 sk-ant-oat01-... 的 token
#   2) 設定 token 後執行本腳本：
#        $env:CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat01-你的token"
#        ./run_local.ps1
#
# 本腳本會：確保 node/claude 在 PATH → 啟動 KB MCP server(8766) → 啟動問答後端(8781)
# 注意：連接埠與 前身專案(8765/8780) 錯開，可與其同時運行。

$ErrorActionPreference = "Stop"
$SRC     = $PSScriptRoot
$VPY     = "$SRC\.venv\Scripts\python.exe"

# 啟用 cross-encoder reranker（子行程 MCP server 會繼承此環境變數）。
# 首次啟動 warmup 會下載模型 BAAI/bge-reranker-v2-m3（~2GB，快取於使用者設定檔，之後免重載）；
# 下載失敗會自動退回「向量+BM25 融合」不致當機。要改小/快模型可設 KB_RERANK_MODEL。
$env:KB_USE_RERANK = "1"

# 若環境未設 token，先從 .env 補載（與問答後端 _load_env 行為一致，讓 token 可只放 .env）
if (-not $env:CLAUDE_CODE_OAUTH_TOKEN -and (Test-Path "$SRC\.env")) {
    foreach ($line in Get-Content "$SRC\.env" -Encoding utf8) {
        if ($line -match '^\s*CLAUDE_CODE_OAUTH_TOKEN\s*=\s*(.+?)\s*$') {
            $env:CLAUDE_CODE_OAUTH_TOKEN = $matches[1].Trim('"').Trim("'"); break
        }
    }
}

if (-not $env:CLAUDE_CODE_OAUTH_TOKEN) {
    Write-Host "[X] 未設定 CLAUDE_CODE_OAUTH_TOKEN。請先執行 'claude setup-token' 取得，再:" -ForegroundColor Red
    Write-Host '      $env:CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat01-..."' -ForegroundColor Yellow
    exit 1
}

# 1) KB MCP server (8766) — 若未在跑才啟動
$mcpUp = (Test-NetConnection -ComputerName 127.0.0.1 -Port 8766 -WarningAction SilentlyContinue).TcpTestSucceeded
if (-not $mcpUp) {
    Write-Host "[*] 啟動 KB MCP server (8766)... 首次預熱約 30s" -ForegroundColor Cyan
    Start-Process -FilePath $VPY -ArgumentList "kb_mcp_server.py","--kb","eu4","--http","--host","127.0.0.1","--port","8766" `
        -WorkingDirectory $SRC -RedirectStandardOutput "$SRC\_mcp_server.log" -RedirectStandardError "$SRC\_mcp_server.err.log" -WindowStyle Hidden
    do { Start-Sleep 5 } until ((Test-NetConnection -ComputerName 127.0.0.1 -Port 8766 -WarningAction SilentlyContinue).TcpTestSucceeded)
}
Write-Host "[OK] KB MCP server 已就緒 (127.0.0.1:8766/mcp)" -ForegroundColor Green

# 2) 問答後端 (8781) — 前景執行
Write-Host "[*] 啟動問答後端 (8781)... 開瀏覽器到 http://127.0.0.1:8781" -ForegroundColor Cyan
& $VPY kb_answer_backend.py
