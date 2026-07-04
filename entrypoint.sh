#!/bin/sh
# 容器入口：MCP server 當內部背景進程(只聽 127.0.0.1，不對外)，問答後端接管 Cloud Run 注入的
# $PORT 當主進程(exec 換掉 shell，直接收 SIGTERM，關閉乾淨)。
set -e

export EU4_KBS_DIR="${EU4_KBS_DIR:-/mnt/kb-data}"
export KB_USE_RERANK="${KB_USE_RERANK:-1}"
export KB_MCP_URL="${KB_MCP_URL:-http://127.0.0.1:8766/mcp}"
export KB_ANSWER_HOST="0.0.0.0"
export KB_ANSWER_PORT="${PORT:-8781}"

echo "[entrypoint] 啟動 kb_mcp_server.py（EU4_KBS_DIR=${EU4_KBS_DIR}）..."
python kb_mcp_server.py --kb eu4 --http --host 127.0.0.1 --port 8766 &
MCP_PID=$!

# 輪詢等 MCP server 就緒(reranker/embedding 模型雖已烤進鏡像層，仍需載入進記憶體，
# 首次載入可能要幾十秒)。GET /mcp 預期回 406(不支援 GET 的 MCP 端點，但代表連得上)，
# 用 Python(不依賴 curl，鏡像裡沒裝) urlopen：HTTPError(406等) = 已就緒；URLError(拒絕連線) = 還沒起來。
i=0
until python -c "
import urllib.request, urllib.error
try:
    urllib.request.urlopen('http://127.0.0.1:8766/mcp', timeout=3)
except urllib.error.HTTPError:
    pass  # 有 HTTP 回應(即便是 406)＝伺服器已在聽
" 2>/dev/null || [ "$i" -ge 60 ]; do
  i=$((i+1)); sleep 2
done
if [ "$i" -ge 60 ]; then
  echo "[entrypoint] kb_mcp_server 60次輪詢(120s)仍未就緒，繼續啟動問答後端(它會在實際調用時報錯，方便看日誌定位)" >&2
fi
echo "[entrypoint] kb_mcp_server 已就緒，啟動 kb_answer_backend.py（PORT=${KB_ANSWER_PORT}）..."

# 確保 MCP 背景進程和主進程一起收信號結束，避免殭屍進程
trap 'kill "$MCP_PID" 2>/dev/null' TERM INT

exec python kb_answer_backend.py
