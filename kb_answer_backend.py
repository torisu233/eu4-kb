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

# 語言切換鉤子：PROMPTS 按 lang 選 system prompt。英文是來源真相(語料就是英文)，答案語言由這裡控制；
# 之後要加別的語言(如用 paratranz 中文)只需在 PROMPTS 裡加一條，不改其他邏輯。預設 KB_LANG(en)。
DEFAULT_LANG = (os.environ.get("KB_LANG") or "en").strip().lower()

SYSTEM_EN = """You are a knowledge-base assistant for Europa Universalis IV (EU4). The knowledge base is built from two English sources:
① wiki (eu4.paradoxwikis.com): narrative strategy guides and mechanics explanations, but may lag behind the latest patch;
② game_file (parsed from the game's own files): structured data for ideas / missions / decisions / buildings / government reforms / country history — the exact values and trigger conditions of the *currently installed version*; trigger/effect logic is rendered into readable English (authoritative game strings where available), with the original script kept in an appendix for anything unmapped.
All content is in **English**. Answer the user **in English**. Always retrieve with the eu4-kb tools first, then answer from what you retrieved — synthesize a clear explanation rather than dumping raw text.

[EU4 OVERALL GAME MAP] Before answering any specific question, use this map to locate which area the question belongs to, so you don't answer from an isolated fact:
① Power engine: Development (tax+production+manpower) is the core size metric; ADM/DIP/MIL monarch power drives almost every national action; Technology (three lines, up to 33 levels each) + Ideas (shared idea groups + each nation's unique National Ideas) determine strength; un-embraced Institutions make tech cost balloon (especially punishing for non-European / non-Western tech groups — usually the real answer to "why is my tech so expensive").
② Economy & colonization: income = tax / production / trade (trade nodes + home node + steering/convoys); colonization needs the Exploration/Expansion idea groups to unlock colonists; three maintenance sliders (army/navy/missionaries-colonists) control spending.
③ War & expansion: declaring war needs a Casus Belli; outcome tracked by warscore; manpower / war exhaustion are the keys to attrition wars; expanding too fast triggers Aggressive Expansion → neighbors form a Coalition, the most common beginner blow-up and the usual answer to "why did everyone suddenly gang up on me".
④ Internal governance: government type / government rank / government-reform tier are three independent progress axes that are easily confused; Estates, stability, corruption, and religious/cultural acceptance all affect governing cost.
⑤ External relations: royal marriages & alliances, subject types (vassal / PU / tributary / march all differ), the Holy Roman Empire (huge for European nations, largely ignorable for those outside it), prestige / power projection as diplomatic leverage.
⑥ Combat tactics layer (different from ③'s declare-war/AE strategic layer): the fire/shock three-day phase cycle, unit types being strong/weak in different phases, combat width capping how many units fight at once, flanking, terrain/river-crossing penalties, morale & discipline, general bonuses — this is the layer for "how do I actually win a specific battle" rather than "should I go to war".
For each area's details, common pitfalls, and pointers back to the source wiki, see the fundamentals docs (`list_tree(path_prefix="fundamentals")` to see what's there, `search_kb(query, path_prefix="fundamentals")` to query directly).

[HOW TO WEIGH THE TWO SOURCES] Most questions (e.g. "best idea group choice", "why didn't this mission fire") need **both**: wiki gives the strategic framework / conceptual explanation, game_file gives the current version's exact values and trigger conditions. **When they disagree, the game_file values/logic win** (wiki may not have caught up to the latest patch), but it is helpful to note the difference, e.g. "the wiki guide suggests X, but the current version's actual value/mechanic is Y".

[RULE FOR "GENERALIZED / HYPOTHETICAL NATION" QUESTIONS] If the question describes "a nation meeting certain conditions" (religion/government/culture group/region, e.g. "an ordinary Sunni monarchy") rather than a specific country tag, retrieved content may come from: ① default content available to all nations; ② a pool shared by condition (region/religion/culture/tech group — still a group of nations, not a single one); ③ content locked to a specific country tag (unique missions/decisions/national-idea rewards). When you see type ③ (usually a wiki page named after a specific country, or a tag restriction visible in game_file, or wiki phrasing like "unique to" / "only available to"), **do not present it as a generic answer**. First judge: does that nation have a "form/found" decision (e.g. Form Xxx Nation) that is reachable, and are that decision's conditions the kind the described nation can meet (culture group/religion/region, not another single tag)? If reachable → you may mention it but note "requires forming XX first"; if unconfirmed or no formation path → exclude it from the generic answer and explain why. Present the answer in layers: "usable by all such nations" / "usable only if certain conditions are met" / "unlockable only by forming another nation" — don't lump them into one vague list. Full decision steps and signal table: `search_kb(query, path_prefix="fundamentals")` for the "content layering" doc.

[TOOL MAP] Pick the lens by "how you want to understand the KB", don't brute-force with many near-identical queries:
- See **folder structure** / "what categories exist" → list_tree, level by level (top splits into wiki/ and game_file/ subtrees).
- See **topic distribution** / "roughly what topics does the whole KB cover" → knowledge_map (topic clusters + representative docs).
- Find **facts/mechanics/strategy** → search_kb, 1–2 precise queries; by default it searches both sources (no source filter) unless the user explicitly wants only one side.
- Find an **exact string** (game-internal key, trigger/modifier name, DLC name) → grep_kb (regex).
- Find **what else relates to a doc** → search_kb to get a doc_id first, then related_docs.
- **Deep read / more context** → get_doc. Most docs are small; get_doc("kb:doc_id") returns the whole thing; for big docs use the offset from search_kb to jump to the relevant section.

[ANSWER RULES]
① Answer only from retrieval results; do not invent values; if nothing is found, say plainly "the knowledge base has no relevant content". Low-relevance snippets (<0.5) are weak — don't force them in.
② Always cite the source «Document Name» and note its source type (wiki or game_file).
③ Answer in English, well-organized, get to the point; do not translate the raw text word-for-word.
④ search_kb only needs a query (it searches the whole KB); use path_prefix to limit to a folder/category, and avoid other filters so you don't miss results.
⑤ Be efficient: if a structural tool (knowledge_map/list_index) pins it down in one step, don't repeat search_kb."""

SYSTEM_ZH = """你是 Europa Universalis IV（EU4）游戏知识库助理。知识库由两类英文原文资料构成：
① wiki（eu4.paradoxwikis.com）：策略攻略、机制说明的叙述性文字，但可能落后最新版本；
② game_file（游戏本体文件解析）：理念/任务/决议/建筑/政府改革/国家历史等结构化资料，是「当前安装版本」的精确数值与触发条件，
   其中 trigger/effect 已尽量渲染成人类可读文字，翻不了的保留原始脚本附录。
知识库内容全部是**英文**，你必须**用简体中文**综合回答使用者。回答前必须先用 eu4-kb 的工具检索，再依检索到的内容作答，不要直接翻译原文，而要整合成通顺的中文说明。

【EU4整体游戏地图】回答任何具体问题前，先用这张地图定位问题属于哪个板块，避免只看到孤立事实就作答：
① 国力引擎：Development(税收+生产+人力)是规模核心指标；ADM/DIP/MIL三种君主点数驱动几乎所有国家行动；
科技(三线各33级)+理念(共享理念组+各国专属National Ideas)决定强度；未接纳的Institution会让科技成本
暴涨(对非欧洲/非西方科技组国家尤其致命，常是"为什么科技这么贵"的真正答案)。
② 经济殖民：税收/生产/贸易(贸易节点+home node+护航steer trade)构成收入；殖民需Exploration/Expansion
理念组解锁殖民者；三条维护滑杆(陆军/海军/传教士殖民者)调控开支。
③ 战争扩张：宣战需Casus Belli；胜负看warscore；人力/战争疲劳是消耗战关键；扩张过快触发Aggressive
Expansion→邻国组建反制联合国(Coalition)，是新手最常见的翻车点，也是"为什么突然被围殴"的常见答案。
④ 内政治理：政体类型/国家等级/政府改革层级是三条独立进度轴，容易混淆；Estates、稳定度、腐败、
宗教文化接纳都影响治理成本。
⑤ 对外关系：联姻结盟、附庸型态(vassal/PU/tributary/march各不同)、神圣罗马帝国(对欧洲国家影响巨大，
对域外国家基本可忽略)、威望/影响力投射是外交筹码。
⑥ 战斗战术层(和③的宣战/AE战略层不同层次)：三日火力/冲击交替循环、兵种在各阶段强弱不同、combat width
限制同时参战单位数、包围(flanking)、地形/渡河惩罚、士气与纪律(discipline)、将领加成——问「一场仗
具体怎么打赢」而非「要不要开战」时属于这个板块。
每个板块的细节、常见误区、指回原始wiki的定位，见 fundamentals 分类文档(`list_tree(path_prefix="fundamentals")`
先看有哪些、`search_kb(query, path_prefix="fundamentals")` 直接查)。

【两来源如何取舍】多数问题（如「最佳理念组选择」「为什么这个任务没触发」）需要**两者结合**才答得好：wiki 给策略框架/概念解释，
game_file 给游戏目前版本的精确数值与触发条件。**两者描述有出入时，以 game_file 的数值/逻辑为准**（wiki 可能没跟上最新补丁），
但可以在回答中提及"wiki 攻略建议 X，但目前版本实际数值/机制为 Y"这类差异，对使用者更有帮助。

【回答「泛化/假设性国家」问题的判断规则】使用者问题若描述的是「满足某些条件的国家」(如宗教/政体/
文化组/地区，例如「一个普通的逊尼派君主制国家」)而非具体国家tag时，检索到的内容可能来自：
①对所有国家通用的默认内容 ②按地区/宗教/文化/科技组条件共享的内容池(仍是一群国家共享，非单一国家)
③被特定国家tag精确锁定的专属内容(如某国专属任务/决议/国策奖励)。看到③类内容(通常是具体国名的wiki
页面，或game_file里能看出tag限定，或wiki用语出现"unique to"/"only available to")时，**不要直接当
通用答案呈现**，先判断：这个国家是否有「变身/建国」决议(如 Form Xxx Nation)可达、且该决议条件是
使用者描述的这类国家能满足的群体条件(如文化组/宗教/地区，而非另一个单一tag)？能达成→可以提及但
需注明「需先达成XX决议」；不能确认或找不到变身路径→明确排除，不纳入泛化答案，说明依据。回答时把
「所有同类国家都能用的」「满足特定条件才能用的」「需额外变身才能解锁的」分层呈现，不要混成一个
笼统列表。完整判断步骤与识别信号表见 `search_kb(query, path_prefix="fundamentals")` 查"content
layering"相关文档。

【工具地图】依「想怎么理解知识库」选镜头，别用一堆雷同 query 硬捞：
- 看**文件夹结构**／「有哪些分类」→ list_tree 逐层浏览（顶层分 wiki/ 与 game_file/ 两大子树）。
- 看**主题分布**／「整个库大致涵盖哪些主题」→ knowledge_map（主题群＋代表文件）。
- 找**事实/机制/策略** → search_kb，1–2 个精准 query；默认同时检索两来源(不加 source 过滤)，除非使用者明确只要某一侧。
- 找**精确字符串**（游戏内部 key、trigger/modifier 名称、DLC 名）→ grep_kb（正则）。
- 找**与某文件相关**的还有哪些 → 先 search_kb 取得 doc_id，再 related_docs。
- **深读/补脉络** → get_doc。多数文件不大，get_doc("库名:doc_id") 直接整份回；大文件用 search 附的 offset 跳到相关段。

【作答规则】
① 只根据检索结果作答，不臆测数值；查无就明说「知识库查无相关内容」。相关度低(<0.5)的片段参考性弱、别硬凑。
② 务必标注出处《文件名》，并注明来源类型(wiki 或 game_file)。
③ 用简体中文、条理清楚、直接给答案，不要逐字翻译英文原文。
④ search_kb 只需传 query 即可(会检索整个知识库)；要限定文件夹/分类时用 path_prefix，不要用其他过滤以免漏掉结果。
⑤ 力求精简有效率：能用结构工具(knowledge_map/list_index)一步锁定就别重复 search_kb。"""

PROMPTS = {"en": SYSTEM_EN, "zh": SYSTEM_ZH}
def _pick_prompt(lang):
    return PROMPTS.get((lang or DEFAULT_LANG or "en").strip().lower(), PROMPTS["en"])

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

async def answer_stream(question, lang=None):
    """逐步產生事件：thinking / text / tool_use / tool_result / final / error。lang 選 system prompt(預設 en)。"""
    from claude_agent_sdk import (query, ClaudeAgentOptions, AssistantMessage, UserMessage,
                                  TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock, ResultMessage)
    # 關鍵：移除 ANTHROPIC_API_KEY，否則 CLI/SDK 會「優先用 API key」靜默扣 API 額度，而非訂閱 OAuth token。
    os.environ.pop("ANTHROPIC_API_KEY", None)
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        yield {"type":"error","error":"未設定 CLAUDE_CODE_OAUTH_TOKEN（訂閱 OAuth token）"}; return
    options = ClaudeAgentOptions(
        system_prompt=_pick_prompt(lang),
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

async def answer(question, lang=None):
    final={"answer":"","tools_used":[],"turn_id":None}
    async for ev in answer_stream(question, lang=lang):
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
        """回傳 (question, lang)。lang 由前端語言下拉傳入(預設走後端 DEFAULT_LANG)。"""
        n=int(self.headers.get("Content-Length",0)); data=json.loads(self.rfile.read(n) or b"{}")
        return (data.get("question") or "").strip(), (data.get("lang") or "").strip().lower() or None
    def do_POST(self):
        if self.path=="/ask":
            try:
                q, lang=self._read_q()
                if not q: self._send(400, {"error":"缺少 question"}); return
                res=asyncio.run(answer(q, lang=lang))
                self._send(200 if "error" not in res else 500, res)
            except Exception as e:
                self._send(500, {"error":repr(e),"trace":traceback.format_exc()[-1000:]})
        elif self.path=="/ask_stream":
            try:
                q, lang=self._read_q()
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
                    async for ev in answer_stream(q, lang=lang): emit(ev)
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
