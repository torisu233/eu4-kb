# -*- coding: utf-8 -*-
"""常駐問答後端（用 Claude Agent SDK + 訂閱 OAuth token，非 API key）。

收到問題 -> 透過 Claude Agent SDK 驅動 Claude Code 引擎，讓它用 KB 的 MCP 工具
agentic 檢索知識庫 -> 作答附出處。用你的『訂閱 OAuth token』認證(免 API key)。

前置：
  1) 先啟動知識庫 MCP HTTP 服務： python kb_mcp_server.py --kb eu4 --http   (127.0.0.1:8766)
  2) 設定『訂閱 OAuth token』(用 `claude setup-token` 取得，prefix sk-ant-oat01-)：
       $env:CLAUDE_CODE_OAUTH_TOKEN = "sk-ant-oat01-..."
啟動：  python kb_answer_backend.py        (預設聽 127.0.0.1:8781)
呼叫：  POST http://127.0.0.1:8781/ask   body: {"question":"..."}
        -> {"answer":"...", "tools_used":[...]}
環境變數(可選)：CLAUDE_CODE_OAUTH_TOKEN(必填)、KB_MCP_URL、KB_ANSWER_MODEL、KB_ANSWER_HOST、KB_ANSWER_PORT、KB_ANSWER_MAX_TURNS
"""
import os, sys, json, asyncio, traceback, uuid, threading, re
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

KB_DIR     = os.path.dirname(os.path.abspath(__file__))

def _load_env(path=None):
    """讀取本地 .env（KEY=VALUE），設進環境變數。已存在於環境的值優先(不覆蓋)；空值跳過。"""
    path = path or os.path.join(KB_DIR, ".env")
    if not os.path.exists(path): return
    try:
        for line in open(path, encoding="utf-8-sig"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if not k or not v: continue
            cur = os.environ.get(k)
            if cur is None or cur.strip() == "":   # 缺少或為空才用 .env 補上(已設非空者優先)
                os.environ[k] = v
    except Exception as e:
        print(f"[警告] 讀取 .env 失敗: {e!r}", file=sys.stderr)

_load_env()  # 先載入 .env，再讀下面設定

KB_MCP_URL = os.environ.get("KB_MCP_URL", "http://127.0.0.1:8766/mcp")
MODEL      = os.environ.get("KB_ANSWER_MODEL", "claude-sonnet-4-6")
HOST       = os.environ.get("KB_ANSWER_HOST", "127.0.0.1")
PORT       = int(os.environ.get("KB_ANSWER_PORT", "8781"))
MAX_TURNS  = int(os.environ.get("KB_ANSWER_MAX_TURNS", "20"))
MCP_NAME   = "eu4-kb"
KB_TOOLS   = ["search_kb","grep_kb","get_doc","list_index","knowledge_map","related_docs","list_tree"]
# 排除問答用不到的 Claude Code 內建工具：①減小快取前綴(每查省token、更快/更省Max用量) ②安全(bypassPermissions下不掛 Bash/Edit/Write)。
# 保留 ToolSearch(CLI 把 MCP 工具當 deferred、需經它存取)。
DISALLOW   = ["Bash","BashOutput","KillShell","Edit","Write","Read","NotebookEdit","Glob","Grep","Task",
              "TodoWrite","WebFetch","WebSearch","ExitPlanMode","PowerShell","CronCreate","CronDelete",
              "CronList","DesignSync","EnterWorktree","ExitWorktree","Monitor","PushNotification","RemoteTrigger"]

# --- 回饋/問答日誌（自我學習地基；append-only JSONL，放 kbs 之外、部署需可寫 volume）---
FEEDBACK_DIR = os.path.join(KB_DIR, "feedback")
os.makedirs(FEEDBACK_DIR, exist_ok=True)
TURNS_LOG    = os.path.join(FEEDBACK_DIR, "turns.jsonl")
FEEDBACK_LOG = os.path.join(FEEDBACK_DIR, "feedback.jsonl")
_FB_LOCK     = threading.Lock()
_DOCID_RE    = re.compile(r'doc_id=([^\s)）｜,，"]+)')   # 從工具回傳文字抽「庫:doc_id」

def _now(): return datetime.now(timezone.utc).isoformat()
def _extract_doc_ids(text): return _DOCID_RE.findall(text or "")
def _append_jsonl(path, obj):
    try:
        with _FB_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        print(f"[feedback] 寫入失敗 {path}: {e!r}", file=sys.stderr)
def _log_turn(turn_id, question, answer, tools, doc_ids):
    _append_jsonl(TURNS_LOG, {"turn_id":turn_id,"ts":_now(),"question":question,
        "answer":answer,"model":MODEL,"tools":tools,"doc_ids":list(dict.fromkeys(doc_ids))})

SYSTEM = """你是 Europa Universalis IV（EU4）遊戲知識庫助理。知識庫由兩類英文原文資料構成：
① wiki（eu4.paradoxwikis.com）：策略攻略、機制說明的敘述性文字，但可能落後最新版本；
② game_file（遊戲本體檔案解析）：理念/任務/決議/建築/政府改革/國家歷史等結構化資料，是「當前安裝版本」的精確數值與觸發條件，
   其中 trigger/effect 已盡量翻譯成人類可讀文字，翻不了的保留原始腳本附錄。
知識庫內容全部是**英文**，你必須**用中文**綜合回答使用者。回答前必須先用 eu4-kb 的工具檢索，再依檢索到的內容作答，不要直接翻譯原文，而要整合成通順的中文說明。

【兩來源如何取捨】多數問題（如「最佳理念組選擇」「為什麼這個任務沒觸發」）需要**兩者結合**才答得好：wiki 給策略框架/概念解釋，
game_file 給遊戲目前版本的精確數值與觸發條件。**兩者描述有出入時，以 game_file 的數值/邏輯為準**（wiki 可能沒跟上最新補丁），
但可以在回答中提及"wiki 攻略建議 X，但目前版本實際數值/機制為 Y"這類差異，對使用者更有幫助。

【工具地圖】依「想怎麼理解知識庫」選鏡頭，別用一堆雷同 query 硬撈：
- 看**資料夾結構**／「有哪些分類」→ list_tree 逐層瀏覽（頂層分 wiki/ 與 game_file/ 兩大子樹）。
- 看**主題分布**／「整個庫大致涵蓋哪些主題」→ knowledge_map（主題群＋代表文件）。
- 找**事實/機制/策略** → search_kb，1–2 個精準 query；預設同時檢索兩來源(不加 source 過濾)，除非使用者明確只要某一側。
- 找**精確字串**（遊戲內部 key、trigger/modifier 名稱、DLC 名）→ grep_kb（正則）。
- 找**與某文件相關**的還有哪些 → 先 search_kb 取得 doc_id，再 related_docs。
- **深讀/補脈絡** → get_doc。多數文件不大，get_doc("庫名:doc_id") 直接整份回；大文件用 search 附的 offset 跳到相關段。

【作答規則】
① 只根據檢索結果作答，不臆測數值；查無就明說「知識庫查無相關內容」。相關度低(<0.5)的片段參考性弱、別硬湊。
② 務必標註出處《文件名》，並註明來源類型(wiki 或 game_file)。
③ 用簡體或繁體中文(依使用者輸入語言)、條理清楚、直接給答案，不要逐字翻譯英文原文。
④ search_kb 只需傳 query 即可(會檢索整個知識庫)；要限定資料夾/分類時用 path_prefix，不要用其他過濾以免漏掉結果。
⑤ 力求精簡有效率：能用結構工具(knowledge_map/list_index)一步鎖定就別重複 search_kb。"""

def _result_text(content):
    if content is None: return ""
    if isinstance(content, str): return content
    parts=[]
    try:
        for c in content:
            if isinstance(c, str): parts.append(c)
            elif isinstance(c, dict): parts.append(c.get("text") or c.get("content") or "")
            else: parts.append(getattr(c, "text", None) or "")
    except TypeError:
        pass
    txt="\n".join(p for p in parts if p)
    if not txt.strip():   # 後備：某些工具(如 ToolSearch)結構不同，避免完全空白
        try: txt = json.dumps(content, ensure_ascii=False, default=str)
        except Exception: txt = str(content)
    return txt

async def answer_stream(question):
    """逐步產生事件：thinking / text / tool_use / tool_result / final / error。"""
    from claude_agent_sdk import (query, ClaudeAgentOptions, AssistantMessage, UserMessage,
                                  TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock, ResultMessage)
    # 關鍵：移除 ANTHROPIC_API_KEY，否則 CLI/SDK 會「優先用 API key」靜默扣 API 額度，而非訂閱 OAuth token。
    os.environ.pop("ANTHROPIC_API_KEY", None)
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        yield {"type":"error","error":"未設定 CLAUDE_CODE_OAUTH_TOKEN（訂閱 OAuth token）"}; return
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM,
        mcp_servers={MCP_NAME: {"type": "http", "url": KB_MCP_URL}},
        allowed_tools=[f"mcp__{MCP_NAME}__{t}" for t in KB_TOOLS],
        disallowed_tools=DISALLOW,
        strict_mcp_config=True, setting_sources=[],
        permission_mode="bypassPermissions", max_turns=MAX_TURNS,
        model=MODEL, cwd=KB_DIR, env={"CLAUDE_CODE_OAUTH_TOKEN": token},
    )
    turn_id=uuid.uuid4().hex[:16]
    id2name={}; tools_used=[]; tool_calls=[]; doc_ids=[]; result_text=None; last_texts=[]
    yield {"type":"start","turn_id":turn_id}
    async for msg in query(prompt=question, options=options):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, ThinkingBlock):
                    yield {"type":"thinking","text":(b.thinking or "")}
                elif isinstance(b, TextBlock):
                    last_texts.append(b.text); yield {"type":"text","text":b.text}
                elif isinstance(b, ToolUseBlock):
                    id2name[b.id]=b.name; tools_used.append(b.name)
                    short=b.name.replace(f"mcp__{MCP_NAME}__","")
                    inp=b.input or {}
                    tool_calls.append({"name":short,"input":inp})        # 工具軌跡(name+參數)
                    if isinstance(inp,dict) and inp.get("doc_id"): doc_ids.append(str(inp["doc_id"]))
                    yield {"type":"tool_use","name":short,"input":inp}
        elif isinstance(msg, UserMessage):
            for b in (msg.content if isinstance(msg.content, list) else []):
                if isinstance(b, ToolResultBlock):
                    nm=id2name.get(b.tool_use_id,"").replace(f"mcp__{MCP_NAME}__","")
                    txt=_result_text(b.content)
                    doc_ids.extend(_extract_doc_ids(txt))                 # 從完整回傳抽命中 doc_id(非截斷版)
                    yield {"type":"tool_result","name":nm,"content":txt[:1500],
                           "truncated":len(txt)>1500}
        elif isinstance(msg, ResultMessage):
            result_text=getattr(msg,"result",None)
    final_answer=(result_text or "\n".join(last_texts)).strip()
    yield {"type":"final","answer":final_answer,"tools_used":tools_used,"turn_id":turn_id}
    # 落檔(只記正常答完的 turn；emit 斷線被吞、迴圈仍跑到這)。失敗/早退不會到此。
    _log_turn(turn_id, question, final_answer, tool_calls, doc_ids)

async def answer(question):
    final={"answer":"","tools_used":[],"turn_id":None}
    async for ev in answer_stream(question):
        if ev["type"]=="error": return {"error":ev["error"]}
        if ev["type"]=="final": final={"answer":ev["answer"],"tools_used":ev["tools_used"],"turn_id":ev["turn_id"]}
    return final

UI_HTML = os.path.join(KB_DIR, "kb_ui.html")

class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.send_header("Access-Control-Allow-Methods","POST, GET, OPTIONS")
    def _send(self, code, obj):
        body=json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self._cors()
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def _html(self, path):
        try: body=open(path,"rb").read()
        except Exception: self._send(500,{"error":"找不到 kb_ui.html"}); return
        self.send_response(200); self._cors()
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*a): pass
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.send_header("Content-Length","0"); self.end_headers()
    def do_GET(self):
        if self.path=="/" or self.path.startswith("/?"): self._html(UI_HTML)
        elif self.path=="/health":
            self._send(200, {"status":"ok","kb_mcp":KB_MCP_URL,"model":MODEL,
                             "auth": "CLAUDE_CODE_OAUTH_TOKEN" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else None})
        else: self._send(404, {"error":"not found"})
    def _read_q(self):
        n=int(self.headers.get("Content-Length",0)); data=json.loads(self.rfile.read(n) or b"{}")
        return (data.get("question") or "").strip()
    def do_POST(self):
        if self.path=="/ask":
            try:
                q=self._read_q()
                if not q: self._send(400, {"error":"缺少 question"}); return
                res=asyncio.run(answer(q))
                self._send(200 if "error" not in res else 500, res)
            except Exception as e:
                self._send(500, {"error":repr(e),"trace":traceback.format_exc()[-1000:]})
        elif self.path=="/ask_stream":
            try:
                q=self._read_q()
                if not q: self._send(400, {"error":"缺少 question"}); return
            except Exception as e:
                self._send(400, {"error":repr(e)}); return
            self.send_response(200); self._cors()
            self.send_header("Content-Type","application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control","no-cache"); self.send_header("X-Accel-Buffering","no")
            self.end_headers()
            def emit(ev):
                try:
                    self.wfile.write((json.dumps(ev,ensure_ascii=False)+"\n").encode("utf-8")); self.wfile.flush()
                except Exception: pass
            async def run():
                try:
                    async for ev in answer_stream(q): emit(ev)
                except Exception as e:
                    emit({"type":"error","error":repr(e)})
            try: asyncio.run(run())
            except Exception as e: emit({"type":"error","error":repr(e)})
        elif self.path=="/feedback":
            try:
                n=int(self.headers.get("Content-Length",0)); data=json.loads(self.rfile.read(n) or b"{}")
                tid=(data.get("turn_id") or "").strip()
                rating=(data.get("rating") or "").strip().lower()
                comment=(data.get("comment") or "").strip()[:500]
                if not tid or rating not in ("up","down"):
                    self._send(400, {"error":"need turn_id and rating in {up,down}"}); return
                _append_jsonl(FEEDBACK_LOG, {"turn_id":tid,"ts":_now(),"rating":rating,"comment":comment})
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(400, {"error":repr(e)})
        else:
            self._send(404, {"error":"use POST /ask, /ask_stream or /feedback"})

def main():
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("[警告] 未設定 CLAUDE_CODE_OAUTH_TOKEN，/ask 會回錯誤。", file=sys.stderr, flush=True)
    print(f"[answer-backend] 聽 http://{HOST}:{PORT}  /ask  (KB={KB_MCP_URL}, model={MODEL}, 認證=訂閱OAuth)", flush=True)
    ThreadingHTTPServer((HOST,PORT), Handler).serve_forever()

if __name__=="__main__":
    main()
