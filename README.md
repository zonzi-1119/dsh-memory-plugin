# DSH 分层长期记忆插件

给 DeepSeek Harness / QQ 桥接里的角色加一个本地、分层、可睡眠巩固的长期记忆服务。

不依赖云端 embedding，不下载大模型，CPU 占用低：

- SQLite 存记忆
- jieba 中文分词
- BM25 + 时间衰减 + 重要度检索
- 快层便签 → 睡眠巩固成抽屉 → 做梦复习
- HTTP 接口，任何桥接都能接

## 为什么需要

聊天上下文只有最近几十/上百条，几个月前的约定、关系、偏好会被挤掉。这个服务把每条对话持久化，并分成快慢两层：

- **快便签**：白天新内容先记这里
- **抽屉**：睡一觉后整理进慢层，旧抽屉锁住不再改
- 新对话直接检索全部抽屉，能翻出很久之前的记忆

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

## 分层模型

```
新消息
  ↓
快便签 fast_notes
  ↓ 每天 03:00，便签 ≥20 条
睡眠：新建 drawer，旧 drawer 全部 locked
  ↓
慢层 memories（drawer_id 标记归属）
  ↓
做梦：自出复习题，练一遍再测
  ↓
好梦（分数上涨）记录到 dreams
```

对应思路：海马体快层 → 睡眠巩固到新皮层抽屉 → SEAL 式自生成复习材料。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 总条数、便签数 |
| GET | `/layers` | 快层、慢层、抽屉、梦 |
| GET | `/drawers` | 抽屉索引（也写 `drawers.json`） |
| POST | `/add` | 写入一条记忆（慢层 + 快便签） |
| POST | `/search` | 检索抽屉和慢层，不检索未睡便签 |
| POST | `/sleep` | 睡眠巩固：新建抽屉、锁旧抽屉、清空便签 |
| POST | `/dream` | 做梦复习：自出题、练习、测验、记录好梦 |

### 写入

```json
POST /add
{
  "text": "老爹答应带鱼去看海",
  "chat": "private:3439292605",
  "sender": "老爹",
  "kind": "message",
  "importance": 1
}
```

`importance` 越高检索权重越大；长期事实建议 3。

### 检索

```json
POST /search
{"query": "看海", "chat": "private:3439292605", "k": 6}
```

返回结果会带 `layer` 和 `drawer_id`，方便区分来源。

### 睡眠与做梦

```http
POST /sleep
POST /dream
```

睡眠返回新抽屉 id、学习条数、纠错数、清空的便签数；做梦返回练习条数、前后分数、是否好梦。

## 模拟

`simulate_layers.py` 会用临时数据库跑 6 个旧抽屉 + 3 轮学习：

```bash
python simulate_layers.py
```

测试点：

1. 新便签进入新抽屉
2. 旧抽屉锁定，不被覆盖
3. 睡眠后便签清空
4. 能召回最早抽屉里的远期记忆
5. 每轮做梦都有分数变化

## 接入桥接

每条消息：

```js
fetch('http://127.0.0.1:3210/add', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ text, chat: key, sender, kind: 'message', importance: 1 })
});
```

每次唤醒前检索：

```js
const res = await fetch('http://127.0.0.1:3210/search', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ query: lastText, chat: key, k: 6 })
});
```

把结果拼成【长期记忆检索】注入提示词，并注明来源是便签还是抽屉。

## 测试

```bash
python tests/test_memory.py
python simulate_layers.py
```

## 设计取舍

- 不引入 embedding 模型：部署简单、离线可用、无 GPU 依赖
- BM25 对中文短语和关键词效果稳定；语义近似查询不如向量模型
- “睡眠/做梦”是工程模拟：抽屉是 SQLite 分区，教师/学生是问答与纠错流程，不是真的改模型参数

## License

MIT
