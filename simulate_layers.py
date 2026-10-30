# -*- coding: utf-8 -*-
"""分层记忆模拟：6 个旧抽屉 + 快便签，3 轮学习/睡觉/做梦 + 回忆测试。

使用临时数据库，不污染正式记忆。
"""
import os
import random
import shutil
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="mem-sim-")
os.environ["MEMORY_DB"] = os.path.join(TMP, "sim.db")
os.environ["MEMORY_PORT"] = "3219"
sys.path.insert(0, r"D:\AI模型\dsh-memory-plugin")

import memory_service as ms  # noqa: E402


def seed_drawers():
    c = ms.conn()
    sentences = [
        ("2026 年夏天老爹答应带鱼去看海，鱼一直记着。", "看海"),
        ("鱼第一次学做甜粽子，把米泡了一整夜。", "甜粽子"),
        ("三五在部落群讲了一个冷笑话，没人笑。", "冷笑话"),
        ("凌晨三点和茉茉吵架，鱼后来有点后悔。", "吵架"),
        ("鱼把围巾织错了一半，拆了重来。", "围巾"),
        ("老爹说鱼唱歌像鸭子，鱼记仇到现在。", "唱歌像鸭子"),
    ]
    for i, (text, _) in enumerate(sentences, start=1):
        cur = c.execute("INSERT INTO drawers(name,created_at,locked,item_count) VALUES(?,?,1,?)",
                        (f"sim-drawer-{i}", int(time.time()) - (10 - i) * 86400, 2))
        did = cur.lastrowid
        c.commit()
        import hashlib

        def seed_mem(t, imp):
            toks = " ".join(ms.tokens_of(t))
            h = hashlib.sha1(("sim|" + t[:500]).encode("utf-8")).hexdigest()
            c.execute(
                "INSERT OR IGNORE INTO memories(hash,text,tokens,chat,sender,kind,importance,created_at,drawer_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (h, t, toks, "sim", "记忆", "fact", imp, int(time.time()), did),
            )

        seed_mem(text, 3)
        seed_mem(f"抽屉{i}的补充：这件事发生在很久以前。", 2)
        c.commit()
    c.commit()
    c.close()
    return sentences


def recall(query, expect):
    hits = ms.search_memory(query, k=5, chat="sim")
    top = hits[0]["text"] if hits else ""
    ok = expect in top
    return ok, top, len(hits)


def main():
    ms.init_db()
    sentences = seed_drawers()
    report = []
    rounds = [
        "新便签：今天群里有人问鱼喜欢什么颜色。",
        "新便签：老爹说周末要投喂鱼。",
        "新便签：鱼想学做一套新表情包。",
    ]
    for idx, note in enumerate(rounds, start=1):
        # 快层：这一轮的新对话
        ms.add_memory(note, "sim", "群友", "message", 1)
        ms.add_memory(f"第{idx}轮补充：鱼回了一句短话。", "sim", "我", "message", 1)
        fast_before = len(ms.search_memory("新便签", k=10, chat="sim"))
        sleep = ms.sleep_consolidate(min_notes=1)
        dream = ms.dream_review()
        # 睡眠后便签应清空
        c = ms.conn()
        fast_after = c.execute("SELECT COUNT(*) n FROM fast_notes").fetchone()["n"]
        drawers = [dict(r) for r in c.execute("SELECT id,locked,item_count FROM drawers ORDER BY id")]
        c.close()
        # 回忆旧抽屉
        old_ok, old_top, _ = recall("看海", "看海")
        long_ok, long_top, _ = recall("围巾", "围巾")
        new_ok, new_top, _ = recall(note[:10], note[:10])
        report.append({
            "round": idx,
            "sleep": sleep,
            "dream": dream,
            "fast_after": fast_after,
            "drawers": drawers,
            "old_recall": old_ok,
            "old_top": old_top[:40],
            "long_recall": long_ok,
            "long_top": long_top[:40],
            "new_recall": new_ok,
            "new_top": new_top[:40],
        })

    # 再测一次“很久之前”的抽屉内容
    old2, top2, _ = recall("唱歌像鸭子", "唱歌像鸭子")
    print("=== 模拟结果 ===")
    for r in report:
        print(f"第{r['round']}轮：睡眠={r['sleep'].get('learned')}条 抽屉={r['drawers']}")
        print(f"  梦：{r['dream'].get('score_before')} -> {r['dream'].get('score_after')} good={r['dream'].get('good')}")
        print(f"  便签清空后={r['fast_after']} 旧记忆回忆={r['old_recall']} 远期回忆={r['long_recall']} 新记忆回忆={r['new_recall']}")
    print(f"远期抽屉（唱歌像鸭子）回忆={old2}：{top2[:50]}")

    # 保留结果文件，删除临时库
    shutil.rmtree(TMP, ignore_errors=True)
    print("临时数据库已删除")


if __name__ == "__main__":
    main()
