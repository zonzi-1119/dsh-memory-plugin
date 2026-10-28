# DSH 长期记忆插件

给 DeepSeek Harness / QQ 桥接里的角色加一个本地长期记忆服务。

不依赖云端 embedding，不下载模型，CPU 占用低：

- SQLite 存记忆
- jieba 中文分词
- BM25 + 时间衰减 + 重要度检索
- HTTP 接口，任何桥接都能接

## 为什么需要

聊天上下文只有最近几十/上百条，几个月前的约定、关系、偏好会被挤掉。这个服务把每条对话持久化，唤醒时只把**最相关的几条旧记忆**注入提示词，从而突破上下文窗口。

## 启动

```bash
pip install -r requirements.txt
python memory_service.py
```

默认监听 `127.0.0.1:3210`，数据库在脚本同目录 `memory.db`。

可用环境变量：

```bash
MEMORY_PORT=3210
MEMORY_DB=/path/to/memory.db
```

## API

### 健康检查

```http
GET /health
```

### 写入记忆

```http
POST /add
Content-Type: application/json

{
  "text": "老爹喜欢叫我大肥鱼",
  "chat": "private:3439292605",
  "sender": "老爹",
  "kind": "message",
  "importance": 1
}
```

`importance` 越高，检索时权重越大；长期事实建议 3。

### 检索

```http
POST /search
Content-Type: application/json

{
  "query": "老爹喜欢叫我什么",
  "chat": "private:3439292605",
  "k": 6
}
```

返回按相关度排序的记忆片段。

## 接入桥接

每次收到消息时：

```js
fetch('http://127.0.0.1:3210/add', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ text, chat: key, sender, kind: 'message', importance: 1 })
});
```

每次唤醒前，用最新一条消息检索：

```js
const res = await fetch('http://127.0.0.1:3210/search', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ query: lastText, chat: key, k: 6 })
});
const hits = (await res.json()).results;
```

把 `hits` 拼成一段【长期记忆检索】注入提示词，并注明“这些是更早的片段，不是这一轮发生的事”。

## 自动回填历史

服务默认在启动时自动回填一次历史聊天，来源：

- `SOCIAL_STATE`：QQ 桥接的 `state/social-v2.json`，读取 `recentMessages`
- `ARCHIVE_DIR`：`D:\QQ桥接文档\上下文交接` 下的归档 `.md`，解析「完整对话」段落

```bash
# 默认开启；设为 0 可关闭
AUTO_BACKFILL=0 python memory_service.py

# 自定义来源
SOCIAL_STATE=/path/to/social-v2.json
ARCHIVE_DIR=/path/to/archives
```

也可以手动触发：

```http
POST /backfill
```

返回：

```json
{"ok": true, "social": 228, "archives": 721, "total": 949}
```

重复内容由 `hash` 自动去重，多次回填不会重复入库。

## 测试

```bash
python tests/test_memory.py
```

测试会：

1. 启动一个临时数据库和端口
2. 写入 40 条记忆，其中一条是很早以前的独特事实
3. 用相关 query 检索，确认旧事实能排到前面
4. 重启服务，再次检索，确认记忆持久化

## 设计取舍

- 不引入 embedding 模型：部署简单、离线可用、无 GPU 依赖
- BM25 对中文短语和关键词效果稳定；语义近似查询不如向量模型
- 需要更强语义时，可以在 `/search` 前加一层 embedding 召回，再交给 BM25 混排

## 故障排查

卡巴斯基等杀毒软件可能把本地 HTTP + SQLite 的 Python 服务误报为恶意软件，处理方式见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

## License

MIT
