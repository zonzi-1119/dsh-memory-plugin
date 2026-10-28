# -*- coding: utf-8 -*-
"""历史聊天回填：social-v2 状态 + 上下文交接归档。

可单独运行，也可被 memory_service.py 在启动时自动调用。
"""
from __future__ import annotations

import glob
import json
import os
import re

DEFAULT_SOCIAL = os.environ.get(
    "SOCIAL_STATE",
    r"C:\Users\zongzi\Documents\qq-bridge\state\social-v2.json",
)
DEFAULT_ARCHIVE = os.environ.get(
    "ARCHIVE_DIR",
    r"D:\QQ桥接文档\上下文交接",
)


def _add(add_fn, text, chat, sender, kind="message", importance=1):
    text = str(text or "").strip()
    if len(text) < 2:
        return False
    if text.startswith("[图片]") or text.startswith("[表情]"):
        return False
    return bool(add_fn(text[:1000], chat, sender, kind, importance).get("ok"))


def backfill_social(add_fn, path=DEFAULT_SOCIAL):
    added = 0
    try:
        v = json.load(open(path, encoding="utf-8"))
    except Exception:
        return 0
    for key, c in (v.get("conversations") or {}).items():
        for m in c.get("recentMessages") or []:
            sender = "我" if m.get("isSelf") else (m.get("sender") or "群友")
            if _add(add_fn, m.get("text"), key, sender):
                added += 1
    return added


def backfill_archives(add_fn, archive_dir=DEFAULT_ARCHIVE):
    added = 0
    for p in glob.glob(os.path.join(archive_dir, "*.md")):
        base = os.path.basename(p)
        mm = re.match(r"(group|private)_([^_]+)_", base)
        if not mm:
            continue
        chat = ("group:" if mm.group(1) == "group" else "private:") + mm.group(2)
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        idx = txt.find("完整对话")
        if idx < 0:
            continue
        for line in txt[idx:].splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("【") or line.startswith("#"):
                continue
            m = re.match(r"^([^：:]{1,24})[：:](.+)$", line)
            if not m:
                continue
            sender, text = m.group(1).strip(), m.group(2).strip()
            if sender == "你":
                sender = "我"
            if "桥接" in text and "断开" in text:
                continue
            if _add(add_fn, text[:500], chat, sender):
                added += 1
    return added


def run_backfill(add_fn, social_state=None, archive_dir=None):
    """返回 {social, archives, total}；重复内容由记忆服务的 hash 自动去重。"""
    social = backfill_social(add_fn, social_state or DEFAULT_SOCIAL)
    archives = backfill_archives(add_fn, archive_dir or DEFAULT_ARCHIVE)
    return {"social": social, "archives": archives, "total": social + archives}


if __name__ == "__main__":
    import sys
    import urllib.request

    service = os.environ.get("MEMORY_URL", "http://127.0.0.1:3210")

    def add_http(text, chat, sender, kind="message", importance=1):
        body = json.dumps(
            {"text": text, "chat": chat, "sender": sender, "kind": kind, "importance": importance},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            service + "/add", data=body, headers={"content-type": "application/json"}
        )
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    print(run_backfill(add_http))
