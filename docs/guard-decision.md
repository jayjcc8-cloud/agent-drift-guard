# Guard Decision v0.1

`GuardDecision` 表示 Core 的意图，而不是某个平台的 hook 返回值。

## Action

| Action | 含义 |
|---|---|
| `allow` | 当前行为安全，允许执行 |
| `warn` | 允许执行但向 Agent/用户展示风险 |
| `block` | 阻止当前操作 |
| `reanchor` | 注入目标/约束/计划上下文后重新决策 |
| `retry` | 在有界次数内重试当前操作 |
| `continue` | 阻止过早停止，继续当前任务 |
| `abort` | 终止 session |

`fallback_action` 解决平台能力不对称。例如首选 `reanchor`、fallback 为 `warn`。Adapter 必须：

1. 执行首选 action；或
2. 能力不足时执行 Core 指定的 fallback；或
3. 两者都不支持时返回明确的 adapter error。

Adapter 不能自行把 `block` 改成 `warn`。

## 约束

- `score` 表示漂移强度，`confidence` 表示判断可信度，两者都在 `[0, 1]`，不能混用。
- `allow` 不得附带 drift type；`continue` 通常正是由停止阶段检测到的 drift 触发，因此可以携带。
- `retry` 必须声明 `max_retries`，避免 retry 自身形成 loop drift。
- `retry_after_seconds` 和 `max_retries` 只允许用于 retry。
- `reason` 面向审计；`context` 是可注入 Agent 的恢复上下文。
- 证据只通过 `evidence_ids` 引用，避免决策对象无限膨胀。

示例见 [`examples/reanchor-decision.json`](../examples/reanchor-decision.json)。
