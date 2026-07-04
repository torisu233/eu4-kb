# -*- coding: utf-8 -*-
"""建置：chunk -> embed(可續跑) -> LanceDB -> (可選)organize -> INDEX.md。
與 前身專案 的差異：內容語言為英文（wiki + 遊戲檔案抽取皆為英文原文），
故拿掉簡繁轉換(OpenCC)，organize 的關鍵詞抽取改用 TF-IDF(sklearn) 取代 jieba。"""
import os, sys, json, re, time, shutil
from . import common
from .common import LP

CHAR_BUDGET=600; OVERLAP=80; SEG=256
HARD_SKIP=4000   # 單一塊超過此字數且為表格/資料型(無法句界切) → 跳過不索引(內容仍留 .md 供 get_doc 讀)

# chunk 階段從 manifest 透傳所有欄位進 chunk dict 時要排除的純內部欄位(非知識/檢索用途)
_MANIFEST_INTERNAL_KEYS = {"abs_path", "extract_status", "chars"}

# ---------------- chunk ----------------
def _heading(line):
    m=re.match(r'^(#{1,6})\s+(.*)', line); return (len(m.group(1)), m.group(2).strip()) if m else (0,None)

def _split_paras(body):
    """以空行分段，回 [(start, text)]：start 為段落首字元在 body 的位移(供 char_start)。"""
    return [(m.start(), m.group().strip()) for m in re.finditer(r'\S.*?(?=\n[ \t]*\n|\Z)', body, re.S)]

_END_RE = re.compile(r'[。！？；]|[.!?](?=\s|$)')   # 句末標點(中文全形 + 英文後接空白/結尾，語言中立)
def _is_dataish(b):
    """超大塊是否為表格/資料型(非散文)，供 HARD_SKIP 跳過判定。語言中立：
    (a) Markdown 表格(| 密度高)；或 (b) 句末標點密度極低(<8/千字) → CSV/匯入檔/資料 dump。"""
    nc = len(b)
    if nc == 0: return False
    if b.count("|") >= 0.05 * nc: return True
    return len(_END_RE.findall(b)) / nc * 1000 < 8

MIN_TAIL = 120   # 軟切最後一片若小於此字數，併回前一片(寧可稍微超預算，也不要留下幾乎無語意的孤立尾塊)
_TABLE_HEAD_RE = re.compile(r'^(\|.*\|)\n(\|[\s:|-]+\|)\n')   # Markdown表格「表頭列+分隔列」

def _soft_pieces(b):
    """把超過 budget 的單塊按句末標點/換行(表格列)切成 ≤budget 的子片，回 [(text, local_off)]；
    單一句界片仍超 budget 則硬字元切。子片連續無 overlap，確保 local_off 精確對齊 body。
    最後一片過小時併回前一片，避免切出脫離標題/清單脈絡、幾乎無資訊量的孤立尾塊。
    若整塊是 Markdown 表格(以表頭+分隔列開頭)，每個續片都補回表頭——否則表格中段被切開後，
    續片只剩裸資料列、看不出各欄位代表什麼(如 "| +20 | X reform |" 不知道 +20 是什麼欄位)，
    這是文獻常見的表格「schema collapse」問題，寧可讓續片稍微超預算也要保留表頭脈絡。"""
    table_header = None
    hm = _TABLE_HEAD_RE.match(b)
    if hm:
        table_header = hm.group(0)
    parts=[p for p in re.split(r'(?<=[。！？；\n])', b) if p]
    pieces=[]; buf=""; buf_off=0; pos=0
    for p in parts:
        if len(p) > CHAR_BUDGET:                       # 無句界的長串 → 硬字元切
            if buf: pieces.append((buf, buf_off)); buf=""
            for i in range(0, len(p), CHAR_BUDGET):
                pieces.append((p[i:i+CHAR_BUDGET], pos+i))
            pos += len(p); continue
        if buf and len(buf)+len(p) > CHAR_BUDGET:
            pieces.append((buf, buf_off)); buf=""
        if not buf: buf_off=pos
        buf += p; pos += len(p)
    if buf: pieces.append((buf, buf_off))
    if len(pieces) >= 2 and len(pieces[-1][0]) < MIN_TAIL:
        (t1, o1), (t2, _o2) = pieces[-2], pieces[-1]
        pieces[-2:] = [(t1 + t2, o1)]
    if table_header:
        for i in range(1, len(pieces)):
            text, off = pieces[i]
            if not text.startswith(table_header):
                pieces[i] = (table_header + text, off)
    return pieces

def _trim_to_word_boundary(s):
    """去掉字串開頭可能截斷到一半的單詞殘片(overlap種子用)：從第一個空白字元後開始，找不到就整段保留。"""
    m = re.search(r'\s', s)
    return s[m.end():] if m else s

def _parse_fm(text):
    if text.startswith("---"):
        e=text.find("\n---",3)
        if e!=-1:
            meta={}
            for ln in text[3:e].strip().splitlines():
                if ":" in ln:
                    k,v=ln.split(":",1); v=v.strip()
                    try: v=json.loads(v)
                    except Exception: pass
                    meta[k.strip()]=v
            return meta, text[e+4:].lstrip("\n")
    return {}, text

def _chunk_body(body):
    """回 [(heading_path, text, char_start)]。char_start 為 chunk 主要內容在 body 的字元位移(供 get_doc offset 跳轉)。
    超過 budget 的單塊會句界硬切；超大且表格/資料型的塊則跳過不出 chunk(內容仍在 .md)。"""
    stack={}; cur=[]; clen=0; path=[]; cur_start=None; out=[]
    def flush():
        nonlocal cur,clen,cur_start
        t="\n\n".join(cur).strip()
        if t: out.append((list(path), t, cur_start if cur_start is not None else 0))
        cur=[]; clen=0; cur_start=None
    for start,b in _split_paras(body):
        if not b: continue
        lvl,ht=_heading(b.splitlines()[0])
        if lvl and len(b.splitlines())==1:
            flush(); stack={k:v for k,v in stack.items() if k<lvl}; stack[lvl]=ht
            path=[stack[k] for k in sorted(stack)]; continue
        if len(b) > CHAR_BUDGET:                                  # 超大塊：先收掉累積，再單獨處理
            flush()
            if len(b) > HARD_SKIP and _is_dataish(b):
                continue                                          # 巨型表格/資料型(CSV/匯入檔) → 跳過不索引
            for txt,loc in _soft_pieces(b):
                tt=txt.strip()
                if tt: out.append((list(path), tt, start+loc))
            continue
        if clen+len(b)>CHAR_BUDGET and cur:                       # 一般塊累積，超 budget 則 flush(留 overlap)
            tail=_trim_to_word_boundary(("\n\n".join(cur))[-OVERLAP:]); flush()
            if tail.strip(): cur=[tail]; clen=len(tail)           # overlap 種子(非主要內容，char_start 仍指向下個真段)
        if cur_start is None: cur_start=start
        cur.append(b); clen+=len(b)
    flush(); return out

def _meta_context(source_hash):
    """讀 meta_cache(由 _meta_enrich.py 產)的 摘要+關鍵詞，組成要前置到 chunk 嵌入/BM25 的脈絡字串。
    廉價版 Contextual Retrieval：用文件級脈絡補上表格/程式碼缺的自然語言與關鍵詞信號。無 cache 則空(優雅退化)。"""
    if not source_hash: return ""
    p=os.path.join(common.ROOT,"meta_cache", source_hash+".json")
    if not os.path.exists(p): return ""
    try:
        o=json.load(open(p,encoding="utf-8"))
        return (str(o.get("summary",""))+" "+" ".join(str(k) for k in o.get("keywords",[]))).strip()
    except Exception: return ""

def run_chunk(name):
    P=common.kb_paths(name)
    docs=[json.loads(l) for l in open(P["manifest"],encoding="utf-8") if l.strip()]
    docs=[m for m in docs if m.get("md_path")]
    n=0
    with open(P["chunks"],"w",encoding="utf-8") as out:
        for m in docs:
            raw=open(os.path.join(P["base"],m["md_path"]),encoding="utf-8").read()
            _,body=_parse_fm(raw)
            ctx=_meta_context(m.get("source_hash"))
            for i,(hp,text,cstart) in enumerate(_chunk_body(body)):
                # 照單全收：manifest 的所有欄位(含 source/wiki_category/entity_category 等擴充欄位)原樣透傳進 chunk，
                # 讓 extractor 決定 schema、下游不需為每個新欄位改程式碼。
                ch={k:v for k,v in m.items() if k not in _MANIFEST_INTERNAL_KEYS}
                ch.update({
                    "chunk_id": f"{m['doc_id']}#{i:04d}",
                    "heading_path": " > ".join(hp),
                    "text": text,
                    "text_norm": text.lower() if text else text,   # 英文大小寫不敏感比對用；不再做簡繁轉換
                    "context": ctx,
                    "char_start": cstart,
                    "n_chars": len(text),
                })
                out.write(json.dumps(ch,ensure_ascii=False)+"\n"); n+=1
    # re-chunk 後 chunk_id/數量已變，舊 emb/ 與 lancedb/ 都失效 → 一併清空，
    # 避免半套狀態(新 chunks.jsonl + 舊 lancedb)在查詢期 self.chunks[cid] KeyError；強制 embed+lance 重建
    shutil.rmtree(P["emb"], ignore_errors=True)
    shutil.rmtree(P["lancedb"], ignore_errors=True)
    print(f"[chunk] DONE chunks={n} docs={len(docs)}（已清空 emb/ 與 lancedb/ 待重建）",flush=True)
    return n

# ---------------- embed (可續跑) ----------------
def run_embed(name, model_name=None):
    P=common.kb_paths(name); model_name=model_name or common.DEFAULT_EMBED_MODEL
    os.makedirs(P["emb"], exist_ok=True)
    rows=[json.loads(l) for l in open(P["chunks"],encoding="utf-8")]
    texts=[(f"{r.get('context','')} {r['title']} {r['heading_path']}: {r['text_norm']}").strip() for r in rows]  # 前置文件級脈絡(摘要+關鍵詞)
    n=len(texts); nseg=(n+SEG-1)//SEG
    import numpy as np
    todo=[i for i in range(nseg) if not os.path.exists(os.path.join(P["emb"],f"seg_{i:05d}.npy"))]
    print(f"[embed] chunks={n} segments={nseg} 待做={len(todo)} model={model_name}",flush=True)
    if todo:
        from sentence_transformers import SentenceTransformer
        m=SentenceTransformer(model_name)
        try: m.max_seq_length=512
        except Exception: pass
        t0=time.time()
        for i in todo:
            v=m.encode(texts[i*SEG:(i+1)*SEG], batch_size=16, normalize_embeddings=True, show_progress_bar=False)
            np.save(os.path.join(P["emb"],f"seg_{i:05d}.npy"), v.astype("float32"))
            done=len([f for f in os.listdir(P["emb"]) if f.endswith(".npy")])
            print(f"[embed] {done}/{nseg} ({int(time.time()-t0)}s)",flush=True)
    print("[embed] DONE",flush=True)
    return n

def build_lance(name):
    import numpy as np, lancedb
    P=common.kb_paths(name)
    rows=[json.loads(l) for l in open(P["chunks"],encoding="utf-8")]
    n=len(rows); nseg=(n+SEG-1)//SEG
    parts=[]
    for i in range(nseg):
        p=os.path.join(P["emb"],f"seg_{i:05d}.npy")
        if not os.path.exists(p): raise RuntimeError(f"缺少嵌入段 {i}，請先 run_embed")
        parts.append(np.load(p))
    vecs=np.concatenate(parts,axis=0)
    assert len(vecs)==n, f"向量數 {len(vecs)} != chunks {n}"
    recs=[dict(r, vector=vecs[i].tolist()) for i,r in enumerate(rows)]
    db=lancedb.connect(P["lancedb"])
    try: db.drop_table("chunks")
    except Exception: pass
    tbl=db.create_table("chunks", data=recs)
    print(f"[lance] DONE rows={tbl.count_rows()} dim={vecs.shape[1]}",flush=True)
    return vecs.shape[1]

# ---------------- organize (可選) ----------------
# 設計說明：EU4 資料本身已有乾淨的現成分類(source x doc_type/entity_category/wiki_category)，
# 不像 雜亂的通用文件堆(doc_type 只是從檔名猜的、真的沒有可靠分類)需要無監督聚類去發現結構。
# 實測 KMeans+TF-IDF 對本專案資料的結果：16 群裡 12 群對某個既有 doc_type 純度 >65%(4 群 >90%)，
# 且把同一個 doc_type(如 714 篇 government_reform)武斷切成 3~4 坨、關鍵詞多是模板樣板詞，
# 等於花力氣重新(且較粗糙地)算出我們已經知道的答案。故 organize 改為：分類統計(確定性、免ML) +
# 向量最近鄰(獨立信號，供 related_docs 的「向量最相關」，這部分聚類與否都有意義，保留)。
def run_organize(name):
    import numpy as np, lancedb
    P=common.kb_paths(name)
    chunks=[json.loads(l) for l in open(P["chunks"],encoding="utf-8")]
    by_doc={}
    for c in chunks: by_doc.setdefault(c["doc_id"],[]).append(c)
    rows=lancedb.connect(P["lancedb"]).open_table("chunks").to_arrow().to_pylist()
    vec={r["chunk_id"]:np.asarray(r["vector"],dtype="float32") for r in rows}
    docs=[]
    for did,cs in by_doc.items():
        vs=[vec[c["chunk_id"]] for c in cs if c["chunk_id"] in vec]
        if not vs: continue
        dv=np.mean(vs,axis=0); dv/=(np.linalg.norm(dv)+1e-9); m=cs[0]
        docs.append({"doc_id":did,"title":m["title"],"doc_type":m.get("doc_type",""),
                     "source":m.get("source",""),"vec":dv})
    titles={d["doc_id"]:d["title"] for d in docs}
    if len(docs)<2:
        json.dump({"categories":[],"related":{},"titles":titles},
                  open(P["doc_map"],"w",encoding="utf-8"),ensure_ascii=False)
        print("[organize] 文件太少，跳過"); return 0

    cat_groups={}
    for d in docs:
        key=f"{d['source']}/{d['doc_type']}" if d["source"] else (d["doc_type"] or "(未分類)")
        cat_groups.setdefault(key,[]).append(d["doc_id"])
    categories=[{"key":k2,"docs":v} for k2,v in sorted(cat_groups.items(), key=lambda kv:-len(kv[1]))]

    X=np.vstack([d["vec"] for d in docs])
    sim=X@X.T; related={}
    for i,d in enumerate(docs):
        rel=[]; seen={d["title"]}
        for j in np.argsort(sim[i])[::-1]:
            if j==i: continue
            tj=docs[j]["title"]
            if tj in seen: continue
            seen.add(tj); rel.append(docs[j]["doc_id"])
            if len(rel)>=3: break
        related[d["doc_id"]]=rel
    json.dump({"categories":categories,"related":related,"titles":titles},
              open(P["doc_map"],"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    print(f"[organize] DONE categories={len(categories)}",flush=True)
    return len(categories)

def generate_index(name):
    P=common.kb_paths(name)
    docs=[json.loads(l) for l in open(P["manifest"],encoding="utf-8") if l.strip()]
    have=[m for m in docs if m.get("md_path")]
    by_t={}
    for m in have: by_t.setdefault(m["doc_type"],[]).append(m)
    L=[f"# 知識庫索引：{name}\n", f"> 共 {len(have)} 份文件（另 {len(docs)-len(have)} 份無內文/待補）。\n"]
    for t in sorted(by_t, key=lambda k:-len(by_t[k])):
        L.append(f"\n## {t}（{len(by_t[t])}）\n")
        for m in sorted(by_t[t], key=lambda x:x["doc_id"]):
            v=f" · {m['version']}" if m.get("version") else ""
            L.append(f"- `{m['doc_id']}`{v} **{m['title']}** ‹{m['source_path']}›")
    open(P["index_md"],"w",encoding="utf-8").write("\n".join(L))
    print(f"[index] DONE -> {P['index_md']}",flush=True)

def generate_tree(name):
    """從 manifest 產文件路徑目錄 tree.json（供 list_tree 導航 + path_prefix 限定子樹）。
    只收有內文(md_path)的文件；source_path 已正規化為 /。"""
    P=common.kb_paths(name)
    docs=[json.loads(l) for l in open(P["manifest"],encoding="utf-8") if l.strip()]
    cat=[[m["source_path"], m["doc_id"], m["title"]] for m in docs if m.get("md_path")]
    cat.sort()
    json.dump({"docs":cat}, open(P["tree"],"w",encoding="utf-8"), ensure_ascii=False)
    print(f"[tree] DONE docs={len(cat)} -> {P['tree']}",flush=True)
    return len(cat)
