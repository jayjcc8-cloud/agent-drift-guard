# Hook 延迟基线与 daemon 决策

`benchmark-hook` 为每次样本启动一个全新的 Python 进程，执行 Adapter、默认脱敏、SQLite WAL、
Supervisor、Detector 和原生响应回译。每次使用独立 session，避免 Loop Detector 干扰测量。

```bash
agent-drift benchmark-hook codex tests/fixtures/codex/pre_tool_use.json \
  --anchors examples/anchors.json \
  --repo-root /project \
  --iterations 30 \
  --warmup 3 \
  --budget-ms 75
```

2026-08-08 在当前本地开发环境的 30 次冷进程样本：

| Adapter | median | p95 | max |
|---|---:|---:|---:|
| Codex | 72.50 ms | 73.12 ms | 73.61 ms |
| Claude Code | 72.94 ms | 74.61 ms | 77.95 ms |

这是使用非 editable 发布安装得到的单机基线，不是跨机器承诺。两者 p95 均低于 75 ms 预算，尚不足
以承担 daemon 的进程生命周期、Unix socket 鉴权、协议升级和故障回退成本。

建议在目标机器和真实 fixture 上重复三轮。只有 p95 持续高于目标预算至少 20%，或连续 Hook 已产生
可感知交互延迟时，才进入 daemon 实现。SQLite 继续作为唯一状态源；daemon 只能是可绕过、可重启的
低延迟执行层，不能成为新的正确性依赖。
