# -*- coding: utf-8 -*-
"""多庫檢索：同時載入多個知識庫，跨庫檢索並合併結果（共用一個嵌入模型）。
單庫亦可用（names=[一個]）。doc 參照用 "庫名:doc_id" 全域唯一。"""
import os, json
from . import common
from .searcher import KBSearcher

class MultiKBSearcher:
    def __init__(self, names=None):
        self.names = names or common.list_kbs()
        self._emb = None
        self.searchers = {}

    def _embmodel(self):
        if self._emb is None:
            from sentence_transformers import SentenceTransformer
            self._emb = SentenceTransformer(common.DEFAULT_EMBED_MODEL)
            try: self._emb.max_seq_length=512   # 對齊建庫端；MCP 多庫共用此模型，須在此設定才生效
            except Exception: pass
        return self._emb

    def s(self, name):
        if name not in self.searchers:
            self.searchers[name] = KBSearcher(name, shared_emb=self._embmodel())
        return self.searchers[name]

    def _targets(self, kb):
        # 空/None/"all"(不分大小寫)→全部；庫名大小寫不敏感；支援逗號或 list 多庫
        if not kb:
            return list(self.names)
        low = {n.lower(): n for n in self.names}
        want = kb if isinstance(kb, list) else [kb]
        out = []
        for w in want:
            for part in str(w).replace("，", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                if part.lower() == "all":
                    return list(self.names)
                n = low.get(part.lower())
                if n and n not in out:
                    out.append(n)
        return out or list(self.names)   # 全部認不得時退回全部(總比靜默空結果好)

    def warmup(self):
        for n in self.names:
            try: self.s(n).search("warmup", top_k=1)
            except Exception: pass

    def search(self, query, kb=None, filters=None, top_k=8):
        res = []
        per = max(5, top_k)
        for n in self._targets(kb):
            try:
                for h in self.s(n).search(query, filters=filters, top_k=per):
                    h = dict(h); h["kb"] = n; res.append(h)
            except Exception:
                pass
        # 跨庫合併排序：
        #   有 reranker → 用 cross-encoder 相關度分(score)當主鍵(比 cosine 更準、且對全部候選皆有定義、跨庫可比)，
        #                 否則 KBSearcher 的 rerank 排名會被下面的 cosine 重排蓋掉、等於白開。
        #   無 reranker → 以 cosine(sim，絕對可比)為主鍵；BM25-only(無 sim)沉到後段，內部再依 RRF 分排。
        if any(h.get("reranked") for h in res):
            res.sort(key=lambda h: h.get("score", -1e9), reverse=True)
        else:
            res.sort(key=lambda h: (h["sim"] if h.get("sim") is not None else -1.0, h.get("score", 0)), reverse=True)
        return res[:top_k]

    def grep(self, pattern, kb=None, limit=30, path_prefix=None):
        out = []
        for n in self._targets(kb):
            for h in self.s(n).grep(pattern, limit=limit, path_prefix=path_prefix):
                h = dict(h); h["kb"] = n; out.append(h)
                if len(out) >= limit: return out
        return out

    def list_tree(self, path_prefix="", depth=1, kb=None):
        """列出資料夾樹某層的子資料夾(含子樹文件數)與檔案。path_prefix 限定起點、depth 展開層數。"""
        pf=(path_prefix or "").replace("\\","/").strip("/"); depth=max(1,int(depth))
        folders={}; files=[]
        for n in self._targets(kb):
            for sp, did, title in self.s(n).tree_docs:
                spn=sp.replace("\\","/").strip("/")
                if pf:
                    if not KBSearcher._under_prefix(sp, pf): continue   # 共用段邊界規則
                    rem=spn[len(pf):].lstrip("/")
                else:
                    rem=spn
                if not rem: continue
                parts=rem.split("/")
                if len(parts)>depth:                     # 在更深的子資料夾 → 計入該層資料夾
                    rel_folder="/".join(parts[:depth])
                    folder=(pf+"/"+rel_folder) if pf else rel_folder   # 顯示完整路徑，可直接當 path_prefix 下鑽
                    folders[folder]=folders.get(folder,0)+1
                else:                                     # 本層(depth 內)的檔案
                    files.append({"name":rem,"doc_id":did,"title":title,"kb":n,"path":spn})
        return {"prefix":pf,"folders":sorted(folders.items()),"files":sorted(files,key=lambda x:x["name"])}

    def get_doc(self, ref):
        if ":" in ref:
            kb, did = ref.split(":", 1)
            if kb in self.names:
                return self.s(kb).get_doc(did)
        for n in self.names:
            r = self.s(n).get_doc(ref)
            if r: return r
        return None

    def related(self, ref):
        """回 (kb, doc_id, doc_map) 或 (None, did, None)。"""
        kb = None; did = ref
        if ":" in ref: kb, did = ref.split(":", 1)
        targets = [kb] if (kb in self.names) else self.names
        for n in targets:
            dm = self.s(n).doc_map
            if did in dm.get("titles", {}):
                return n, did, dm
        return None, did, None

    def manifest(self, name):
        rows = []
        p = common.kb_paths(name)["manifest"]
        if os.path.exists(p):
            for l in open(p, encoding="utf-8"):
                m = json.loads(l)
                if m.get("md_path"): rows.append(m)
        return rows
