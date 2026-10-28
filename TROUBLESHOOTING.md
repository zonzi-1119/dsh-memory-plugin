# 故障排查

## 卡巴斯基把记忆服务报成恶意软件

### 现象

- 运行 `python memory_service.py` 后，卡巴斯基弹窗拦截
- 进程被终止，`127.0.0.1:3210` 连不上
- 文件可能被隔离或删除

### 原因

这是**启发式误报**，不是服务真的有问题。触发点通常是：

- Python 脚本在本机开 HTTP 端口（`3210`）
- 直接读写 SQLite 数据库
- 没有数字签名

卡巴斯基对"脚本 + 本地监听端口 + 数据库"的组合比较敏感。本项目源码完全公开，可以自行审阅 `memory_service.py` 和 `backfill.py`。

### 方案一：加排除项（推荐）

卡巴斯基 → 设置 → 安全 → 威胁与排除 → 管理排除项：

1. 添加文件夹：
   - `D:\AI模型\dsh-memory-plugin`
   - `D:\AI模型\memory-service`（如果数据库放这里）
2. 添加进程：
   - `D:\AI模型\memory-env\Scripts\python.exe`
3. 保存后重新启动服务

### 方案二：只加端口/行为排除

如果不想整个文件夹排除，可以在防火墙/行为监控里放行：

- 本地回环 `127.0.0.1:3210`
- `python.exe` 的子进程网络访问

### 方案三：卸载卡巴斯基

如果确认不需要它，可以用官方清除工具：

1. 下载 `kavremvr.exe`：
   https://media.kaspersky.com/utilities/ConsumerUtilities/kavremvr.exe
2. 校验签名应为 `CN=AO Kaspersky Lab`
3. 管理员运行 → 接受协议 → 输入验证码 → 选择卡巴斯基 → Remove
4. 重启电脑

> 注意：卸载安全软件会降低整机防护，请自行评估。本项目不推荐为了跑一个脚本就卸载杀毒软件。

### 验证服务是否恢复

```bash
curl http://127.0.0.1:3210/health
```

正常返回：

```json
{"ok": true, "count": 904}
```

### 环境记录

- 卡巴斯基版本：21.26.4.406
- 系统：Windows
- 记忆服务：Python 3.12 + jieba + SQLite
