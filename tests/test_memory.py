# -*- coding: utf-8 -*-
"""长期记忆插件自测：检索命中旧记忆 + 重启后持久化。"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = os.path.join(os.path.dirname(HERE), "memory_service.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def call(port, path, payload=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def start(port, db):
    env = dict(os.environ, MEMORY_DB=db, MEMORY_PORT=str(port))
    proc = subprocess.Popen([sys.executable, SERVICE], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            call(port, "/health")
            return proc
        except Exception:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("service did not start")


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main():
    tmp = tempfile.mkdtemp(prefix="memory-test-")
    db = os.path.join(tmp, "memory.db")
    port = free_port()
    proc = start(port, db)
    try:
        call(port, "/add", {"text": "2025年圣诞节鱼答应给老爹织一条蓝色围巾", "chat": "private:3439292605",
                            "sender": "鱼", "kind": "agreement", "importance": 3})
        for i in range(40):
            call(port, "/add", {"text": f"普通聊天记录第{i}条：今天吃了饭，天气还行", "chat": "group:742832419",
                                "sender": "群友", "kind": "message", "importance": 1})
        hits = call(port, "/search", {"query": "蓝色围巾是谁答应织的", "k": 5})["results"]
        assert hits, "search returned nothing"
        assert "围巾" in hits[0]["text"], f"old memory not ranked first: {hits[0]}"
        print("PASS search hit:", hits[0]["text"])
    finally:
        stop(proc)

    port2 = free_port()
    proc2 = start(port2, db)
    try:
        health = call(port2, "/health")
        assert health["count"] == 41, health
        hits = call(port2, "/search", {"query": "蓝色围巾", "k": 3})["results"]
        assert hits and "围巾" in hits[0]["text"], hits
        print("PASS persistence after restart:", health, hits[0]["text"])
    finally:
        stop(proc2)
        shutil.rmtree(tmp, ignore_errors=True)

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
