# Hook 延迟基线与 daemon 决策

`benchmark-hook` 为每次样本启动一个全新的 Python 进程，执行 Adapter、默认脱敏、SQLite WAL、
Supervisor、Detector、原生响应回译和有界 JSONL telemetry。每次使用独立 session，避免 Loop
Detector 干扰测量；只有显式传入 `--no-telemetry` 才会排除 exporter。

```bash
agent-drift benchmark-hook codex tests/fixtures/codex/pre_tool_use.json \
  --anchors examples/anchors.json \
  --repo-root /project \
  --iterations 30 \
  --warmup 3 \
  --budget-ms 75
```

2026-08-09 使用非 editable 本地安装、每轮 30 次冷进程样本并重复三轮：

| Adapter | p95 第 1 轮 | p95 第 2 轮 | p95 第 3 轮 |
|---|---:|---:|---:|
| Codex | 81.06 ms | 79.25 ms | 79.29 ms |
| Claude Code | 79.48 ms | 79.46 ms | 79.74 ms |

这是一台机器的结果，不是跨机器承诺。加入真实 telemetry 路径后，两者 p95 均略高于 75 ms 预算，
但只高约 6%–8%，仍低于“持续超预算至少 20%”（90 ms）的 daemon 阶段门。当前证据不足以承担
daemon 的进程生命周期、Unix socket 鉴权、协议升级和故障回退成本。

建议在目标机器和真实 fixture 上重复三轮。只有 p95 持续高于目标预算至少 20%，或连续 Hook 已产生
可感知交互延迟时，才进入 daemon 实现。SQLite 继续作为唯一状态源；daemon 只能是可绕过、可重启的
低延迟执行层，不能成为新的正确性依赖。
