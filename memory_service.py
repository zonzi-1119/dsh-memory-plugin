# -*- coding: utf-8 -*-
"""鱼的长期记忆服务：SQLite + jieba + BM25 检索。

HTTP:
  GET  /health
  POST /add    {text, chat, sender, kind, importance}
  POST /search {query, k, chat}
  GET  /stats
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jieba

try:
    from backfill import run_backfill
except Exception:
    run_backfill = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("MEMORY_DB") or os.path.join(ROOT, "memory.db")
PORT = int(os.environ.get("MEMORY_PORT") or 3210)
LOCK = threading.Lock()
STOP = set("的 了 是 我 你 他 她 它 们 在 有 和 就 都 也 不 吗 呢 啊 吧 呀 哦 这 那 个 一 会 要 说 去 来 很 还 被 把 给 对 上 下 中 里 好 没 人 什么 怎么 为什么 可以 一个 我们 你们 他们".split())


def tokens_of(text: str):
    text = re.sub(r"https?://\S+", " ", str(text or ""))
    out = []
    for w in jieba.cut(text):
        w = w.strip().lower()
        if not w or w in STOP:
            continue
        if len(w) == 1 and not re.match(r"[a-z0-9]", w):
            continue
        out.append(w)
    return out


def init_db():
    os.makedirs(ROOT, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT UNIQUE,
            text TEXT NOT NULL,
            tokens TEXT NOT NULL,
            chat TEXT,
            sender TEXT,
            kind TEXT,
            importance REAL DEFAULT 1,
            created_at INTEGER,
            used_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_chat ON memories(chat)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_time ON memories(created_at)")
    conn.commit()
    conn.close()


STOPWORDS = STOP


def add_memory(text, chat="", sender="", kind="message", importance=1.0):
    text = str(text or "").strip()
    if len(text) < 2:
        return {"ok": False, "error": "text too short"}
    toks = tokens_of(text)
    if not toks:
        return {"ok": False, "error": "no tokens"}
    h = hashlib.sha1((str(chat) + "|" + text[:500]).encode("utf-8")).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO memories(hash,text,tokens,chat,sender,kind,importance,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (h, text[:2000], " ".join(toks), str(chat)[:120], str(sender)[:80], str(kind)[:30], float(importance or 1), int(time.time())),
        )
        conn.commit()
        return {"ok": True, "id": conn.execute("SELECT id FROM memories WHERE hash=?", (h,)).fetchone()[0]}
    finally:
        conn.close()


def search_memory(query, k=6, chat=None):
    qt = tokens_of(query)
    if not qt:
        return []
    conn = sqlite3.connect(DB_FILE)
    try:
        if chat:
            rows = conn.execute(
                "SELECT id,text,tokens,chat,sender,kind,importance,created_at FROM memories WHERE chat=? OR chat='' ORDER BY created_at DESC LIMIT 20000",
                (str(chat),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id,text,tokens,chat,sender,kind,importance,created_at FROM memories ORDER BY created_at DESC LIMIT 20000"
            ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    docs = []
    df = {}
    for r in rows:
        toks = r[2].split()
        docs.append((r, toks))
        for w in set(toks):
            df[w] = df.get(w, 0) + 1
    N = len(docs)
    avgdl = sum(len(t) for _, t in docs) / max(1, N)
    k1, b = 1.5, 0.75
    now = time.time()
    scored = []
    for r, toks in docs:
        if not toks:
            continue
        tf = {}
        for w in toks:
            tf[w] = tf.get(w, 0) + 1
        score = 0.0
        for q in set(qt):
            if q not in tf:
                continue
            idf = math.log(1 + (N - df.get(q, 0) + 0.5) / (df.get(q, 0) + 0.5))
            score += idf * (tf[q] * (k1 + 1)) / (tf[q] + k1 * (1 - b + b * len(toks) / max(1, avgdl)))
        if score <= 0:
            continue
        age_days = max(0, (now - (r[7] or now)) / 86400)
        score += 0.35 * math.exp(-age_days / 60)
        score *= 0.8 + 0.2 * min(3.0, float(r[6] or 1))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[: max(1, min(20, int(k or 6)))]:
        out.append({
            "id": r[0], "text": r[1], "chat": r[3], "sender": r[4],
            "kind": r[5], "score": round(score, 4), "created_at": r[7]
        })
    ids = [x["id"] for x in out]
    if ids:
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.executemany("UPDATE memories SET used_count=used_count+1 WHERE id=?", [(i,) for i in ids])
            conn.commit()
        finally:
            conn.close()
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            conn = sqlite3.connect(DB_FILE)
            n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.close()
            self._json(200, {"ok": True, "count": n})
            return
        self._json(404, {"ok": False})

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        if self.path.startswith("/add"):
            with LOCK:
                self._json(200, add_memory(body.get("text"), body.get("chat", ""), body.get("sender", ""), body.get("kind", "message"), body.get("importance", 1)))
            return
        if self.path.startswith("/backfill"):
            if not run_backfill:
                self._json(500, {"ok": False, "error": "backfill module not found"})
                return
            with LOCK:
                result = run_backfill(add_memory)
            self._json(200, {"ok": True, **result})
            return
        if self.path.startswith("/search"):
            self._json(200, {"ok": True, "results": search_memory(body.get("query", ""), body.get("k", 6), body.get("chat"))})
            return
        self._json(404, {"ok": False})


def main():
    init_db()
    print(f"memory service on 127.0.0.1:{PORT} db={DB_FILE}", flush=True)
    if run_backfill and os.environ.get("AUTO_BACKFILL", "1") != "0":
        def _auto():
            try:
                print("auto backfill:", run_backfill(add_memory), flush=True)
            except Exception as e:
                print("auto backfill failed:", e, flush=True)
        threading.Thread(target=_auto, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
