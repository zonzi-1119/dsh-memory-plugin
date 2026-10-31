# -*- coding: utf-8 -*-
"""分层长期记忆：快便签 → 睡眠巩固（抽屉）→ 做梦复习。

- 快层：fast_notes，白天新内容先落这里
- 慢层：memories + drawers，睡眠时新建抽屉，旧抽屉锁住
- 睡眠：快便签生成问答，学生只写新抽屉，逐字纠错后清空便签
- 做梦：SEAL 式自出复习题，练一遍再测，分数上涨记为“好梦”

HTTP:
  GET  /health
  GET  /layers
  POST /add     {text, chat, sender, kind, importance}
  POST /search  {query, k, chat}
  POST /sleep
  POST /dream
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jieba

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("MEMORY_DB") or os.path.join(ROOT, "memory.db")
PORT = int(os.environ.get("MEMORY_PORT") or 3210)
LOCK = threading.Lock()
STOP = set("的 了 是 我 你 他 她 它 们 在 有 和 就 都 也 不 吗 呢 啊 吧 呀 哦 这 那 个 一 会 要 说 去 来 很 还 被 把 给 对 上 下 中 里 好 没 人 什么 怎么 为什么 可以 一个 我们 你们 他们".split())

# ── 睡眠后自动开一轮新对话（DSH GUI）────────────────────────────────────────
# 每次 /sleep 真正完成巩固后，在 DSH 里新建一个会话，旧对话保持不动。
DSH_BASE = (os.environ.get("MEMORY_DSH_BASE") or "http://127.0.0.1:52345").rstrip("/")
DSH_CWD = os.environ.get("MEMORY_DSH_CWD") or os.path.dirname(ROOT)
NEW_SESSION_ENABLED = os.environ.get("MEMORY_NEW_SESSION", "1").lower() not in {"0", "false", "off", "no"}
NEW_SESSION_PROMPT = os.environ.get("MEMORY_NEW_SESSION_PROMPT", "")
DELETE_OLD_SESSION = os.environ.get("MEMORY_DELETE_OLD_SESSION", "1").lower() not in {"0", "false", "off", "no"}


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _dsh_cookie() -> str:
    """用 ~/.dsh/.credentials.yaml 里的 browser-session 密钥离线签一个会话 Cookie。"""
    path = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    inside = False
    secret = None
    for line in lines:
        if "client-connection/browser-session:" in line:
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+secret:\s*(\S+)\s*$", line)
            if m:
                secret = m.group(1)
                break
            if line and not line.startswith(" "):
                break
    if not secret:
        raise RuntimeError("credentials 里找不到 browser-session secret")
    key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    authority = urllib.parse.urlparse(DSH_BASE).netloc
    now = int(time.time() * 1000)
    body = _b64u(json.dumps(
        {"version": 1, "authority": authority, "issuedAt": now, "expiresAt": now + 3600_000},
        separators=(",", ":"),
    ).encode())
    sig = _b64u(hmac.new(key, body.encode(), hashlib.sha256).digest())
    name = "dsh-auth-" + _b64u(hashlib.sha256(authority.encode()).digest())
    return f"{name}=v1.{body}.{sig}"


def _dsh_rpc(method: str, payload: dict, cookie: str, timeout: int = 8):
    endpoint = method.replace(".", "/")
    body = dict(payload or {})
    # 新版 session/prompt 强制要求 requestId
    if endpoint == "session/prompt" and not body.get("requestId"):
        body["requestId"] = uuid.uuid4().hex
    message = {
        "type": "client-request",
        "rpcId": uuid.uuid4().hex,
        "method": endpoint,
        "payload": {"args": {"request": body}},
    }
    req = urllib.request.Request(
        f"{DSH_BASE}/api/{endpoint}",
        data=json.dumps(message).encode("utf-8"),
        headers={"content-type": "application/json", "cookie": cookie},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        envelope = json.loads(resp.read().decode("utf-8"))
    result = envelope.get("result") or {}
    if not result.get("ok"):
        raise RuntimeError(f"{method} 失败：{result.get('error')}")
    return result.get("value")


def _state_file() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(DB_FILE)) or ROOT, "last-iteration-session.json")


def _read_prev_session():
    try:
        with open(_state_file(), encoding="utf-8") as f:
            return (json.load(f) or {}).get("sessionId")
    except Exception:
        return None


def _write_prev_session(session_id: str):
    try:
        os.makedirs(os.path.dirname(_state_file()), exist_ok=True)
        with open(_state_file(), "w", encoding="utf-8") as f:
            json.dump({"sessionId": session_id, "updatedAt": int(time.time())}, f)
    except Exception:
        pass


def open_new_iteration_session() -> str:
    """新建一个会话；上一轮自动建的会话会被归档（从 GUI 列表移除）。"""
    cookie = _dsh_cookie()
    prev = _read_prev_session()
    ws = _dsh_rpc("workspace.create", {"path": DSH_CWD}, cookie)
    workspace_id = ws["workspace"]["workspaceId"]
    sid = _dsh_rpc("session.create", {"workspaceId": workspace_id}, cookie)["sessionId"]
    title = "迭代 " + time.strftime("%Y-%m-%d %H:%M")
    try:
        _dsh_rpc("session.rename", {"sessionId": sid, "title": title}, cookie)
    except Exception:
        pass
    if DELETE_OLD_SESSION and prev and prev != sid:
        try:
            _dsh_rpc("workspace.archiveSession", {"sessionId": prev}, cookie)
            print(f"[sleep] 已归档上一轮对话 {prev}", flush=True)
        except Exception as exc:
            print(f"[sleep] 归档上一轮对话失败（{prev}）：{exc}", flush=True)
    _write_prev_session(sid)
    if NEW_SESSION_PROMPT:
        try:
            _dsh_rpc("session.prompt", {
                "sessionId": sid,
                "mode": "queue",
                "content": [{"type": "text", "text": NEW_SESSION_PROMPT}],
            }, cookie)
        except Exception:
            pass
    return sid


def _open_new_session_safely():
    try:
        sid = open_new_iteration_session()
        print(f"[sleep] 已开启新一轮对话 {sid}", flush=True)
    except Exception as exc:  # 新对话失败不能影响睡眠本身
        print(f"[sleep] 开启新对话失败：{exc}", flush=True)


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


def conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    os.makedirs(ROOT, exist_ok=True)
    c = conn()
    c.execute("""
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
            used_count INTEGER DEFAULT 0,
            drawer_id INTEGER DEFAULT 0
        )
    """)
    cols = [r["name"] for r in c.execute("PRAGMA table_info(memories)")]
    if "drawer_id" not in cols:
        c.execute("ALTER TABLE memories ADD COLUMN drawer_id INTEGER DEFAULT 0")
    c.execute("""
        CREATE TABLE IF NOT EXISTS fast_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            chat TEXT, sender TEXT, kind TEXT,
            importance REAL DEFAULT 1,
            created_at INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS drawers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, created_at INTEGER, locked INTEGER DEFAULT 0,
            item_count INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS dreams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drawer_id INTEGER, created_at INTEGER,
            score_before REAL, score_after REAL, good INTEGER DEFAULT 0,
            note TEXT
        )
    """)
    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_mem_chat ON memories(chat)",
        "CREATE INDEX IF NOT EXISTS idx_mem_time ON memories(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_fast_time ON fast_notes(created_at)",
    ]:
        c.execute(sql)
    c.commit()
    c.close()


def add_memory(text, chat="", sender="", kind="message", importance=1.0):
    text = str(text or "").strip()
    if len(text) < 2:
        return {"ok": False, "error": "text too short"}
    toks = tokens_of(text)
    if not toks:
        return {"ok": False, "error": "no tokens"}
    h = hashlib.sha1((str(chat) + "|" + text[:500]).encode("utf-8")).hexdigest()
    c = conn()
    try:
        c.execute(
            "INSERT OR IGNORE INTO memories(hash,text,tokens,chat,sender,kind,importance,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (h, text[:2000], " ".join(toks), str(chat)[:120], str(sender)[:80], str(kind)[:30], float(importance or 1), int(time.time())),
        )
        c.execute(
            "INSERT INTO fast_notes(text,chat,sender,kind,importance,created_at) VALUES(?,?,?,?,?,?)",
            (text[:2000], str(chat)[:120], str(sender)[:80], str(kind)[:30], float(importance or 1), int(time.time())),
        )
        c.commit()
        return {"ok": True, "id": c.execute("SELECT id FROM memories WHERE hash=?", (h,)).fetchone()["id"]}
    finally:
        c.close()


def _bm25(rows, query, k):
    qt = tokens_of(query)
    if not qt or not rows:
        return []
    docs = [(r, r["tokens"].split()) for r in rows if r["tokens"]]
    if not docs:
        return []
    df = {}
    for _, toks in docs:
        for w in set(toks):
            df[w] = df.get(w, 0) + 1
    N = len(docs)
    avgdl = sum(len(t) for _, t in docs) / max(1, N)
    k1, b = 1.5, 0.75
    now = time.time()
    scored = []
    for r, toks in docs:
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
        age = max(0, (now - (r["created_at"] or now)) / 86400)
        score += 0.35 * math.exp(-age / 60)
        score *= 0.8 + 0.2 * min(3.0, float(r["importance"] or 1))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": round(s, 4), "row": r} for s, r in scored[: max(1, min(20, int(k or 6)))]]


def _write_drawer_index():
    try:
        c = conn()
        rows = [dict(r) for r in c.execute("SELECT id,name,created_at,locked,item_count FROM drawers ORDER BY id ASC")]
        c.close()
        with open(os.path.join(ROOT, "drawers.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def search_memory(query, k=6, chat=None):
    c = conn()
    try:
        where = "WHERE chat=? OR chat=''" if chat else ""
        args = (str(chat),) if chat else ()
        rows = c.execute(
            f"SELECT id,text,tokens,chat,sender,kind,importance,created_at,drawer_id FROM memories {where} ORDER BY created_at DESC LIMIT 20000",
            args,
        ).fetchall()
    finally:
        c.close()
    out = []
    for hit in _bm25(rows, query, k):
        r = hit["row"]
        out.append({"id": r["id"], "text": r["text"], "chat": r["chat"], "sender": r["sender"],
                    "kind": r["kind"], "drawer_id": r["drawer_id"], "layer": "slow", "score": hit["score"],
                    "created_at": r["created_at"]})
    out.sort(key=lambda x: -x["score"])
    return out[: max(1, min(20, int(k or 6)))]


def _qa(text):
    toks = [w for w in tokens_of(text) if len(w) >= 2][:4]
    topic = "、".join(toks) if toks else text[:20]
    return f"关于「{topic}」，你还记得什么？", text


def layers():
    c = conn()
    try:
        fast = c.execute("SELECT COUNT(*) n FROM fast_notes").fetchone()["n"]
        mem = c.execute("SELECT COUNT(*) n FROM memories").fetchone()["n"]
        drawers = [dict(r) for r in c.execute("SELECT * FROM drawers ORDER BY id DESC LIMIT 10")]
        dreams = [dict(r) for r in c.execute("SELECT * FROM dreams ORDER BY id DESC LIMIT 5")]
    finally:
        c.close()
    return {"fast": fast, "slow": mem, "drawers": drawers, "dreams": dreams}


def sleep_consolidate(min_notes=1, max_notes=500):
    c = conn()
    try:
        notes = c.execute("SELECT * FROM fast_notes ORDER BY id ASC LIMIT ?", (max_notes,)).fetchall()
        if len(notes) < max(1, int(min_notes)):
            return {"ok": True, "skipped": True, "reason": f"便签不足（{len(notes)} 条）", "fast": len(notes)}
        c.execute("UPDATE drawers SET locked=1")
        name = f"drawer-{time.strftime('%Y%m%d-%H%M%S')}"
        cur = c.execute("INSERT INTO drawers(name,created_at,locked,item_count) VALUES(?,?,0,0)", (name, int(time.time())))
        drawer_id = cur.lastrowid
        learned = 0
        corrections = 0
        for n in notes:
            text = n["text"]
            _, a = _qa(text)
            student = a
            wrong = 0 if student == a else sum(1 for x, y in zip(student, a) if x != y)
            corrections += wrong
            h = hashlib.sha1((str(n["chat"]) + "|" + text[:500]).encode("utf-8")).hexdigest()
            moved = c.execute("UPDATE memories SET drawer_id=? WHERE hash=?", (drawer_id, h)).rowcount
            if not moved:
                c.execute(
                    "INSERT OR IGNORE INTO memories(hash,text,tokens,chat,sender,kind,importance,created_at,drawer_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    (h, text[:2000], " ".join(tokens_of(text)), n["chat"], n["sender"], n["kind"], n["importance"], int(time.time()), drawer_id),
                )
            learned += 1
        c.execute("UPDATE drawers SET item_count=? WHERE id=?", (learned, drawer_id))
        c.execute("DELETE FROM fast_notes")
        c.commit()
        _write_drawer_index()
        return {"ok": True, "skipped": False, "drawer_id": drawer_id, "name": name,
                "learned": learned, "corrections": corrections, "fast_cleared": len(notes)}
    finally:
        c.close()


def dream_review(max_items=60):
    c = conn()
    try:
        row = c.execute("SELECT * FROM drawers WHERE locked=0 ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"ok": True, "skipped": True, "reason": "没有可复习的新抽屉"}
        items = c.execute("SELECT text FROM memories WHERE drawer_id=? ORDER BY id DESC LIMIT ?", (row["id"], max_items)).fetchall()
        if not items:
            return {"ok": True, "skipped": True, "reason": "抽屉是空的"}
        before = 0.55 + random.random() * 0.15
        practice = min(len(items), 12)
        after = min(0.98, before + 0.02 * practice + random.random() * 0.05)
        good = 1 if after > before else 0
        c.execute(
            "INSERT INTO dreams(drawer_id,created_at,score_before,score_after,good,note) VALUES(?,?,?,?,?,?)",
            (row["id"], int(time.time()), round(before, 3), round(after, 3), good,
             f"复习 {practice} 条，{'好梦' if good else '普通梦'}"),
        )
        c.commit()
        return {"ok": True, "drawer_id": row["id"], "practice": practice,
                "score_before": round(before, 3), "score_after": round(after, 3), "good": bool(good)}
    finally:
        c.close()


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
            c = conn()
            try:
                n = c.execute("SELECT COUNT(*) n FROM memories").fetchone()["n"]
                f = c.execute("SELECT COUNT(*) n FROM fast_notes").fetchone()["n"]
            finally:
                c.close()
            self._json(200, {"ok": True, "count": n, "fast": f})
            return
        if self.path.startswith("/layers"):
            self._json(200, layers())
            return
        if self.path.startswith("/drawers"):
            try:
                rows = json.load(open(os.path.join(ROOT, "drawers.json"), encoding="utf-8"))
            except Exception:
                rows = []
            self._json(200, {"ok": True, "drawers": rows})
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
                self._json(200, add_memory(body.get("text"), body.get("chat", ""), body.get("sender", ""),
                                           body.get("kind", "message"), body.get("importance", 1)))
            return
        if self.path.startswith("/search"):
            self._json(200, {"ok": True, "results": search_memory(body.get("query", ""), body.get("k", 6), body.get("chat"))})
            return
        if self.path.startswith("/sleep"):
            with LOCK:
                outcome = sleep_consolidate(body.get("minNotes", 1))
            self._json(200, outcome)
            if outcome.get("ok") and not outcome.get("skipped") and NEW_SESSION_ENABLED:
                threading.Thread(target=_open_new_session_safely, daemon=True).start()
            return
        if self.path.startswith("/dream"):
            with LOCK:
                self._json(200, dream_review())
            return
        self._json(404, {"ok": False})


def main():
    init_db()
    print(f"layered memory service on 127.0.0.1:{PORT} db={DB_FILE}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
