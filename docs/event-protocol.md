# Unified Agent Event Protocol v0.1

## 信封

`AgentEvent` 是 Adapter 发给 Core 的唯一事件入口。必填字段只有协议版本、事件类型、平台、
session、agent、时间和 JSON payload；其他字段用于可靠的顺序及关联。

| 字段 | 作用 |
|---|---|
| `protocol_version` | wire contract 版本，不等于包版本 |
| `event_id` | 全局去重/引用，未传入时自动生成 UUID |
| `event_type` | 标准生命周期类型 |
| `platform` | 平台稳定标识，如 `codex` |
| `session_id` | 平台 session 映射后的稳定标识 |
| `sequence` | Adapter 在一个 session 内生成的单调序号 |
| `parent_event_id` | 因果父事件，例如 tool result 指向 tool before |
| `trace_id` | 可选的跨进程/OTEL 关联标识 |
| `payload` | 已标准化 JSON 数据 |
| `extensions` | 平台专属 JSON，key 必须带命名空间 |

## v0.1 事件类型

```text
session.start
prompt.submit
tool.before
tool.after
tool.error
compaction.before
compaction.after
subagent.start
subagent.stop
agent.stop
session.end
```

Core 字段采用严格校验，未知字段会报错；这是为了尽早发现 Adapter 拼写错误。平台新字段放入
`extensions`，例如 `claude.hook_input`。标准事件类型只随协议版本扩展，不能用任意字符串绕过。

## 排序规则

同一个 session 优先按 `sequence` 排序。缺失 sequence 时才使用 timestamp；timestamp 必须带
时区。并发 subagent 的全局顺序不能只由时间推断，应通过 `parent_event_id`/`trace_id` 保留因果关系。

## payload 约束

v0.1 只冻结信封，没有过早冻结十一种事件的全部 payload。payload 必须是 JSON object。进入真实
Adapter 阶段后，从 Codex/Claude 的共同语义提炼 payload profiles；平台原始 hook input 仍留在
extensions，不能直接成为 Core 依赖。

示例见 [`examples/tool-before.json`](../examples/tool-before.json)。

