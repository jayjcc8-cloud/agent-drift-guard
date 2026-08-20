# SQLite Store 与持久化 Hook

## 为什么先做 Store

Codex 和 Claude Code 的 command Hook 通常每次启动一个新进程。仅依赖内存 Supervisor 会丢失前一次
工具调用，Validation、State 和 Loop Detector 无法跨 Hook 工作。SQLite Store 让每个进程通过同一
数据库恢复状态，同时避免过早引入 daemon 的生命周期、端口、鉴权和升级复杂度。

## 持久化模型

| 表 | 内容 | 关键约束 |
|---|---|---|
| `sessions` | session 的下一个 sequence | `session_id` 唯一 |
| `events` | 完整规范化 `AgentEvent` | `event_id`、`(session_id, sequence)` 唯一 |
| `evidence` | 每个 Detector 的独立证据 | `evidence_id`、`(event_id, ordinal)` 唯一 |
| `decisions` | 最终统一决策 | 每个 event 最多一个 decision |
| `maintenance` | 自动保留等维护任务时间 | 每个维护 key 唯一 |

数据库使用 schema `user_version=3`、foreign keys、WAL、`synchronous=NORMAL` 和 busy timeout。首次创建
时目录权限请求为 `0700`、数据库文件请求为 `0600`；重新打开已有数据库时也会把数据库文件修复为
`0600`。项目 Hook 安装器另外统一检查和修复 `.agent-drift/` 内的数据库 sidecar、telemetry、health、
lock 与备份文件权限。

## 原子处理语义

一次 Hook 处理分为：

1. `BEGIN IMMEDIATE` 锁定 sequence 分配，为 session 原子递增并写入事件；
2. 读取当前 sequence 之前的有界历史；
3. Supervisor 生成 evidence 和 decision；
4. 在一个事务内写入全部 evidence 和唯一 decision。

相同 `event_id` 和相同内容重复提交会返回既有记录；相同 ID 对应不同内容或 sequence 冲突会明确
报错。多个进程同时完成同一 event 时，以第一个已提交 decision 为准。

## CLI

初始化与检查：

```bash
agent-drift store-init ~/.agent-drift/drift.db
agent-drift store-stats ~/.agent-drift/drift.db
agent-drift store-events ~/.agent-drift/drift.db SESSION_ID --limit 100
agent-drift store-prune ~/.agent-drift/drift.db
```

作为原生 Hook 运行：

```bash
agent-drift hook codex - \
  --database ~/.agent-drift/drift.db \
  --anchors /absolute/path/anchors.json \
  --repo-root /absolute/path/project

agent-drift hook claude-code - \
  --database ~/.agent-drift/drift.db \
  --anchors /absolute/path/anchors.json \
  --repo-root /absolute/path/project
```

命令从 stdin 读取平台 Hook JSON，仅向 stdout 输出平台原生响应，因此可以直接放入 Hook handler。
规范化事件、证据和决策留在 SQLite 中供审计。

## Codex 配置片段

```json
{
  "hooks": {
    "PreToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "agent-drift hook codex - --database ~/.agent-drift/drift.db --anchors /project/anchors.json --repo-root /project"
      }]
    }],
    "PostToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "agent-drift hook codex - --database ~/.agent-drift/drift.db --anchors /project/anchors.json --repo-root /project"
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "agent-drift hook codex - --database ~/.agent-drift/drift.db --anchors /project/anchors.json --repo-root /project"
      }]
    }]
  }
}
```

Claude Code 使用相同命令结构，将 platform 参数改为 `claude-code` 并放入
`.claude/settings.local.json`。
生产配置还应覆盖 `UserPromptSubmit`、compaction、subagent 和 session 生命周期事件。

## 隐私与运维边界

数据库保存写盘前脱敏后的 prompt、命令参数、工具结果和 Agent 最终消息。默认规则不能保证覆盖所有
业务敏感信息，仍应把数据库放在用户私有目录、排除版本控制并限制备份范围。`store-stats` 会运行
`PRAGMA integrity_check`，但不会删除或修复数据。策略配置、保留清理和迁移语义见
[脱敏、保留与迁移](privacy-retention.md)。

默认 30 天/每 session 5000 事件的策略在 Hook 打开 Store 时至多每日自动运行一次，不需要本地
daemon。只读/维护 CLI 不触发打开时清理；显式 `store-prune` 仍可用于预览、临时收紧或立即执行。

Adapter 当前为每次 native delivery 生成新的随机 `event_id`。只有上层重试复用同一个 AgentEvent 时
才获得 ID 级幂等；平台重复投递同一原始 Hook 仍可能记录为两个事件。未来可在平台提供稳定 delivery
ID 时加入去重键。
