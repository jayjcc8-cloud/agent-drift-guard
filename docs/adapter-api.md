# Codex / Claude Code Adapter API

## 目标

Adapter 是唯一知道平台 Hook 字段和输出形状的模块。Core 只接受 `AgentEvent`，所有平台字段留在
命名空间扩展中。实现依据于 2026-08-09 查询到的官方规范：

- [Codex Hooks](https://developers.openai.com/codex/hooks)
- [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks)

本地开发机检测到 `codex-cli 0.147.0` 与 Claude Code `2.1.98`。契约 fixture 采用官方文档当前 wire
schema，并已让两个真实 CLI 加载安装器生成的项目 Hook。Codex 完成了真实命令会话；Claude 真实
触发启动、提交、结束和凭据失败路径，模型请求受本机可用凭据限制。

## 输入规范化

两边共有的生命周期映射如下：

| Native Hook | Unified event |
|---|---|
| `SessionStart` | `session.start` |
| `UserPromptSubmit` | `prompt.submit` |
| `PermissionRequest` | `permission.request` |
| `PreToolUse` | `tool.before` |
| `PostToolUse` | `tool.after` |
| `PreCompact` / `PostCompact` | `compaction.before` / `compaction.after` |
| `SubagentStart` / `SubagentStop` | `subagent.start` / `subagent.stop` |
| `Stop` | `agent.stop` |
| `SessionEnd` | `session.end` |

Claude 的 `PostToolUseFailure` 额外映射为 `tool.error`，`TaskCompleted` 映射为 `task.completed`，
`StopFailure` 映射为只观察的 `agent.error`。Codex 的非零 Bash 结果仍从
`PostToolUse` 到达，因此保留为 `tool.after`，并通过规范化的 `payload.outcome` 表示成功/失败；
不能伪造一个平台没有发出的 Hook 名。

三个新增统一事件使用 Event Protocol v0.2；Supervisor 继续接受 v0.1 历史事件，避免切断既有 replay。
Decision 与 Capabilities 仍使用各自的 v0.1 contract。

工具名也进行最小语义归一化，例如两边的 `Bash` 都成为 `shell`。原始工具名保存在
`codex.tool_name` 或 `claude-code.tool_name`。

## 决策回译

回译不是简单字符串替换，而是 `(event_type, action, platform)` 三元映射：

- `BLOCK + tool.before`：两边均返回现代 `permissionDecision: deny`；
- `BLOCK + permission.request`：返回原生 deny；`ALLOW` 安全地回译为 defer，不代表自动批准；
- `CONTINUE/BLOCK + task.completed`：Claude 用 exit 2 和 stderr 阻止完成并反馈原因；
- `StopFailure`：平台定义为只观察，不伪造可执行控制动作；
- `CONTINUE + agent.stop`：返回 `decision: block`，让 Agent 继续；
- `BLOCK + compaction.before`：Codex 使用 `continue: false`，Claude 使用 `decision: block`；
- `RETRY`：两个 Adapter 当前都不宣称原生支持，只有 Core 明确给出 fallback 才能降级；
- `SessionEnd`：两边都是 advisory，不能回译成阻止 session 结束。

`HookResponse` 同时保留 stdout JSON、stderr、exit code 和最终实际动作，契约测试无需启动平台进程
即可验证真实命令 Hook 行为。

## 能力声明边界

两个 Adapter 均满足 v0.1 Full Guard baseline，但这不是安全隔离承诺。Codex 官方说明 hosted tool
及部分 specialized path 可能绕过默认本地工具 Hook；Claude Stop 也有连续阻止上限。限制写入
`PlatformCapabilities.notes`，doctor/日志不得隐藏。

## CLI

```bash
agent-drift adapt-hook codex native-hook.json --repo-root /project
agent-drift adapt-hook claude-code native-hook.json --repo-root /project
agent-drift adapter-capabilities codex --platform-version 0.147.0
agent-drift render-hook codex event.json decision.json
```
