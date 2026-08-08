# 脱敏、保留与迁移

## 写盘前脱敏

`SQLiteStore` 默认启用 `RedactionPolicy`。事件先递归检查 `payload` 和 `extensions`，再写入数据库；
Supervisor 和 Detector 随后使用已经脱敏、但结构不变的事件。原始 Hook JSON 不落盘。

默认规则覆盖两类信息：

- 敏感键名，例如 `password`、`authorization`、`api_key`、`client_secret`、cookie 和各类 token；
- 字符串中的常见 Bearer token、OpenAI/GitHub/AWS 凭据和 PEM 私钥。

`store-stats` 的 `redactions` 是累计替换次数。可通过 JSON 文件扩充或收紧规则：

```bash
agent-drift hook codex - \
  --database ~/.agent-drift/drift.db \
  --anchors /project/anchors.json \
  --redaction-policy /project/examples/redaction-policy.json
```

只有显式配置 `{"enabled": false}` 才会关闭脱敏。自定义正则来自本地受信任配置，应避免复杂、
可能产生灾难性回溯的表达式。脱敏不是匿名化：文件路径、session ID 和不符合规则的业务数据仍可能
具有敏感性，因此数据库权限和备份边界依然必要。

## 数据保留

保留策略使用可信的入库时间，而不是 Adapter 提供的事件时间。默认策略为 30 天、每个 session
最多 5000 个事件，两个条件取并集。外键级联会同步删除 evidence 和 decision，空 session 随后删除。

清理命令默认只预览：

```bash
agent-drift store-prune ~/.agent-drift/drift.db
agent-drift store-prune ~/.agent-drift/drift.db --max-age-days 14 --max-events-per-session 2000
agent-drift store-prune ~/.agent-drift/drift.db --max-age-days 14 --apply
```

可用 `--no-age-limit` 或 `--no-count-limit` 关闭单个边界，但不能同时关闭两者。`--apply` 是实际
删除的显式开关；清理不自动执行 `VACUUM`，以免 Hook 路径出现长时间独占锁。

## Schema migration

当前 SQLite schema 为 v2。打开 v1 数据库时会在单个 `BEGIN IMMEDIATE` 事务中增加：

- `stored_at_epoch`：保留策略使用的入库时间；
- `redaction_count`：每个事件的替换计数。

迁移逐版本注册并在每步完成后更新 `PRAGMA user_version`。失败会回滚，未来版本仍会被拒绝。
v1 历史事件没有原始入库时间，只能以其事件时间初始化；历史内容也不会被追溯脱敏。生产升级前仍应
备份数据库，并在升级后运行 `store-stats` 检查 `integrity=ok`。
