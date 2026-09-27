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
COPY kb_mcp_server.py kb_answer_backend.py kb_ui.html entrypoint.sh ./
COPY demo/ ./demo/
RUN chmod +x entrypoint.sh

# 建執行期用戶，並「在下載大模型檔案之前」就讓它擁有 /app 和快取路徑——踩過兩次坑才定案:
# ① 若在useradd/USER app之前(root身份)下載模型，快取進/root/.cache，實際serving時是
#    USER app(/home/app/.cache全新空目錄)讀不到，導致Cloud Run運行時重新下載觸發OOM。
# ② 若下載完模型(root寫入)後才補一句chown -R給app，overlay檔案系統對已存在的大檔案做
#    metadata變更常會整份copy-up到新層，等於憑空多佔一份好幾GB的空間，CI runner磁碟被
#    這個chown動作本身撐爆(觀察到的錯誤是"No space left on device"發生在chown那一步)。
# 正解：先建用戶+空目錄的chown(此時目錄是空的，chown很便宜)，之後直接切換USER app再下載，
# 模型檔案從誕生那一刻起就屬於app，不需要之後再對大檔案做任何owner/權限異動。
ENV HF_HOME=/opt/hf-cache
RUN useradd -m app \
    && mkdir -p "$HF_HOME" \
    && chown -R app /app "$HF_HOME"
USER app

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')" \
    && python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"

# Cloud Run 預設從 8080 注入 PORT；問答後端監聽 0.0.0.0 才能從容器外訪問(kb_answer_backend.py
# 的 HOST 讀 KB_ANSWER_HOST，entrypoint.sh 會設成 0.0.0.0)。
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health')" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
