# -*- coding: utf-8 -*-
"""混合檢索器：向量(LanceDB) + 英文 BM25 → RRF 融合。可選 reranker。
模型惰性載入（首次呼叫才載入）。與中文版前身的差異：內容為英文，故 norm() 只做小寫化(不做簡繁轉換)，
BM25 tokenizer 改用正則英文分詞(取代 jieba)。"""
import os, sys, json, re
from . import common

_TOK_RE = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")   # 英文詞(含撇號/連字號複合詞，如 don't、province-trade)

class KBSearcher:
    def __init__(self, name, shared_emb=None):
        self.name=name; self.P=common.kb_paths(name)
        self.cfg=common.load_config(name)
        self.embed_model=self.cfg.get("embed_model", common.DEFAULT_EMBED_MODEL)
        self.use_rerank=os.environ.get("KB_USE_RERANK","0")!="0"
        self.rerank_model=os.environ.get("KB_RERANK_MODEL","BAAI/bge-reranker-v2-m3")
        self.rerank_topn=max(1,int(os.environ.get("KB_RERANK_TOPN","12")))   # 只重排前 N 個融合候選；CPU 延遲∝N(純CPU下~0.5s/筆，12→約4~5s/題)
        self._emb=shared_emb; self._rr=None; self._rr_tried=False; self._tbl=None; self._bm25=None
        self.chunks={}; self.order=[]
        for l in open(self.P["chunks"],encoding="utf-8"):
            c=json.loads(l); self.chunks[c["chunk_id"]]=c; self.order.append(c["chunk_id"])
        self._dm=None; self._tree=None; self._d2h=None

    def norm(self,t):
        return (t or "").lower()

    def _tok(self,s):
        return _TOK_RE.findall(self.norm(s))

    @property
    def emb(self):
        if self._emb is None:
            from sentence_transformers import SentenceTransformer
            self._emb=SentenceTransformer(self.embed_model)
            try: self._emb.max_seq_length=512   # 對齊建庫端(build.run_embed)，避免查詢端預設 128 截斷
            except Exception: pass
        return self._emb
    @property
    def rr(self):
        if not self.use_rerank: return None
        if self._rr is None and not self._rr_tried:
            self._rr_tried=True
            try:
                from sentence_transformers import CrossEncoder
                self._rr=CrossEncoder(self.rerank_model, trust_remote_code=True)  # jina-reranker-v2需要自訂建模代碼
            except Exception as e:
                sys.stderr.write(f"[reranker off] {repr(e)[:120]}\n"); self._rr=None
        return self._rr
    @property
    def tbl(self):
        if self._tbl is None:
            import lancedb
            self._tbl=lancedb.connect(self.P["lancedb"]).open_table("chunks")
        return self._tbl
    @property
    def bm25(self):
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi
            self._bm25=BM25Okapi([self._tok((self.chunks[c].get("context","")+" "+self.chunks[c]["text_norm"])) for c in self.order])  # 前置文件級脈絡(摘要+關鍵詞)，與查詢端小寫化對齊
        return self._bm25
    @property
    def doc_map(self):
        if self._dm is None:
            p=self.P["doc_map"]
            self._dm=json.load(open(p,encoding="utf-8")) if os.path.exists(p) else {"clusters":[],"related":{},"titles":{}}
        return self._dm

    @property
    def docid2hash(self):
        """doc_id → source_hash（供檢索時內容去重：同 hash+同文字＝重複歸檔副本）。"""
        if self._d2h is None:
            self._d2h={}
            mp=self.P["manifest"]
            if os.path.exists(mp):
                for l in open(mp,encoding="utf-8"):
                    if l.strip():
                        m=json.loads(l); self._d2h[m["doc_id"]]=m.get("source_hash","")
        return self._d2h

    @property
    def tree_docs(self):
        """tree.json 的文件 catalog：[[source_path, doc_id, title], ...]（供 list_tree）。"""
        if self._tree is None:
            p=self.P["tree"]
            self._tree=(json.load(open(p,encoding="utf-8")).get("docs",[]) if os.path.exists(p) else [])
        return self._tree

    @staticmethod
    def _normp(s):
        return str(s or "").replace("\\","/").strip("/").lower()

    @staticmethod
    def _under_prefix(source_path, prefix):
        """source_path 是否落在 path_prefix 子樹內（依路徑段邊界，分隔符/大小寫不敏感）。"""
        sp=KBSearcher._normp(source_path); pf=KBSearcher._normp(prefix)
        return (not pf) or sp==pf or sp.startswith(pf+"/")

    def _ok(self, cid, f):
        if not f: return True
        c = self.chunks[cid]
        for k, v in f.items():
            if not v: continue
            if k == "path_prefix":                       # 前綴比對：限定資料夾子樹
                if not self._under_prefix(c.get("source_path"), v): return False
                continue
            cv = c.get(k)
            if cv is None: return False
            if str(cv).strip().lower() != str(v).strip().lower():  # 大小寫/前後空白不敏感
                return False
        return True

    def search(self, query, filters=None, top_k=8, cand=60):
        import numpy as np
        # 限定範圍(單一文件 doc_id 或子樹 path_prefix)時，放大召回上限，確保範圍內片段不被全域 top-N 擠掉
        scoped = bool(filters and (filters.get("doc_id") or filters.get("path_prefix")))
        lim = 2000 if scoped else 120
        qn=self.norm(query)
        qv=self.emb.encode([qn], normalize_embeddings=True, show_progress_bar=False)[0].tolist()
        vhits=self.tbl.search(qv).metric("cosine").limit(lim).to_arrow().to_pylist()
        sim_map={h["chunk_id"]: round(1.0-float(h["_distance"]),4) for h in vhits}  # cosine 相似度：絕對相關信號(跨庫可比)，BM25-only 命中則無
        vrank={}; i=0
        for h in vhits:
            if self._ok(h["chunk_id"],filters): vrank[h["chunk_id"]]=i; i+=1
        scores=self.bm25.get_scores(self._tok(query)); brank={}; bi=0
        for idx in np.argsort(scores)[::-1]:
            if scores[idx]<=0 or bi>=lim: break
            cid=self.order[idx]
            if self._ok(cid,filters): brank[cid]=bi; bi+=1
        K=60; fuse={}
        for cid,r in vrank.items(): fuse[cid]=fuse.get(cid,0)+1.0/(K+r)
        for cid,r in brank.items(): fuse[cid]=fuse.get(cid,0)+1.0/(K+r)
        ranked=sorted(fuse,key=fuse.get,reverse=True)[:cand]
        if not ranked: return []
        rr=None
        try:
            rr=self.rr                                         # 載入可能因離線/SSL 失敗；絕不讓它拖垮查詢
        except Exception as e:
            sys.stderr.write(f"[reranker off] load 例外: {repr(e)[:140]}\n")
        if rr is not None:
            pool=ranked[:self.rerank_topn]                         # 只重排前 N 個(控 CPU 延遲)，其餘殿後備位
            try:
                rs=rr.predict([[query,self.chunks[c]["text"]] for c in pool])
                order=sorted(range(len(pool)),key=lambda i:rs[i],reverse=True)
                scored=[(pool[i],float(rs[i])) for i in order]                     # 重排段(依 cross-encoder 分)
                scored+=[(c,round(fuse[c],4)) for c in ranked[self.rerank_topn:]]  # 未重排尾段殿後(去重後補足 top_k 用)
            except Exception as e:
                sys.stderr.write(f"[reranker off] predict 例外: {repr(e)[:140]}\n")
                scored=[(c,round(fuse[c],4)) for c in ranked]     # 推論失敗 → 退回 RRF 融合分
                rr=None                                            # 標記本次未重排(供 reranked 旗標)
        else:
            scored=[(c,round(fuse[c],4)) for c in ranked]
        # 內容去重：同檔(source_hash)且同文字的片段(重複歸檔副本)只留最佳名次一筆，其餘位置併入 dup_paths
        out=[]; seen={}
        for cid,sc in scored:
            c=self.chunks[cid]; h=self.docid2hash.get(c["doc_id"],"")
            key=(h,c["text"])
            if h and key in seen:                              # 有 hash 才去重(空 hash 不誤併)
                seen[key]["dup_paths"].append(c["source_path"]); continue
            if len(out)>=top_k: continue                       # 已滿 top_k，但續掃以併入已收項的重複位置
            rec={"chunk_id":cid,"score":round(float(sc),4),"sim":sim_map.get(cid),"doc_id":c["doc_id"],"title":c["title"],
                 "heading_path":c["heading_path"],"doc_type":c["doc_type"],"version":c.get("version",""),
                 "source_path":c["source_path"],"md_path":c["md_path"],"char_start":c.get("char_start",0),
                 "text":c["text"],"dup_paths":[],"reranked":rr is not None,
                 "source":c.get("source",""),"entity_category":c.get("entity_category",""),
                 "wiki_category":c.get("wiki_category","")}   # reranked=True 時 score 為 cross-encoder 相關度(供多庫合併排序)
            seen[key]=rec; out.append(rec)
        return out

    def grep(self, pattern, limit=30, path_prefix=None):
        rx=re.compile(pattern,re.I); out=[]
        for cid in self.order:
            c=self.chunks[cid]
            if path_prefix and not self._under_prefix(c.get("source_path"), path_prefix): continue
            if rx.search(c["text"]):
                out.append({"chunk_id":cid,"title":c["title"],"heading_path":c["heading_path"],
                            "source_path":c["source_path"],"snippet":c["text"][:200]})
                if len(out)>=limit: break
        return out

    def get_doc(self, doc_id):
        """接受 doc_id 或 source_path(相對路徑)。"""
        key=self._normp(doc_id)
        def _read(rel): return open(os.path.join(self.P["base"], rel.replace("\\","/")),encoding="utf-8").read()
        for cid in self.order:
            c=self.chunks[cid]
            if c["doc_id"]==doc_id or self._normp(c.get("source_path"))==key:
                return _read(c["md_path"])
        # 後備：該文件可能無任何 chunk → 改從 manifest 反查 md_path，仍可閱讀全文
        mp=self.P["manifest"]
        if os.path.exists(mp):
            for l in open(mp,encoding="utf-8"):
                if not l.strip(): continue
                m=json.loads(l)
                if (m.get("doc_id")==doc_id or self._normp(m.get("source_path"))==key) and m.get("md_path"):
                    p=os.path.join(self.P["base"], m["md_path"].replace("\\","/"))
                    if os.path.exists(p): return open(p,encoding="utf-8").read()
        return None
