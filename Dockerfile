# 單容器跑兩個 HTTP 服務：kb_mcp_server.py(內部 127.0.0.1，不對外) + kb_answer_backend.py
# (對外，接管 Cloud Run 注入的 $PORT)。knowledge base 資料(kbs/eu4/)不在鏡像裡——由
# Cloud Run 的 GCS volume mount 掛進來(見 entrypoint.sh 的 EU4_KBS_DIR)，鏡像只含代碼。
FROM python:3.13-slim

WORKDIR /app

# claude-agent-sdk 會 shell 出去呼叫 Claude Code CLI(npm 包)，故鏡像需要 Node，不是純 Python 能解決的。
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# 先裝依賴(碼變依賴沒變時走快取層)。torch 必須**先**裝CPU-only wheel再裝requirements.txt——
# sentence-transformers依賴torch(無版本限定)，順序反過來的話pip會先從預設PyPI源抓GPU版
# torch(現在預設會帶一整套nvidia-cu*/cuda-toolkit依賴，好幾GB)，把runner磁碟塞爆
# (實測CI上直接因為這個順序錯誤導致 OSError: No space left on device)。CPU版先裝好，
# 之後requirements.txt解析torch依賴時視為已滿足，不會再去抓GPU版。
COPY requirements.txt ./
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# 源碼(不含 kbs/ 資料、.venv、fundamentals_src 以外的建置腳本不需要在 serving 鏡像裡，
# 但 fundamentals_extract.py 依賴 kb/ 套件，一併拷入無妨，體積很小)
COPY kb/ ./kb/
COPY clausewitz/ ./clausewitz/
COPY renderers/ ./renderers/
COPY wiki/ ./wiki/
COPY kb_mcp_server.py kb_answer_backend.py kb_ui.html ./

# 預熱下載 embedding + reranker 模型進鏡像層，避免 Cloud Run 冷啟動時現拉(reranker ~2.2GB，
# 實測本機首次下載約需數分鐘，容器內現拉會拖慢/拖不穩每次冷啟動)。
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')" \
    && python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh \
    && useradd -m app && chown -R app /app
USER app

# Cloud Run 預設從 8080 注入 PORT；問答後端監聽 0.0.0.0 才能從容器外訪問(kb_answer_backend.py
# 的 HOST 讀 KB_ANSWER_HOST，entrypoint.sh 會設成 0.0.0.0)。
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health')" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
