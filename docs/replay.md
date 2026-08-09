# 真实长会话 Replay

Replay 输入是经过写盘前脱敏的 JSONL，支持三种逐行记录：

- `ObservationEnvelope`：实时 Hook telemetry，包含原决策；
- `ReplayCase`：`event` 加可选 `expected_action`、语义指纹和人工 `expected_drift_types` 标签；
- 单独的规范化 `AgentEvent`。

## 从真实 Hook 持续采集

`install-hooks` 默认把每次完成的监督结果追加到 `.agent-drift/observations.jsonl`。手工配置 Hook 时可加：

```bash
agent-drift hook codex - \
  --database .agent-drift/drift.db \
  --anchors .agent-drift/anchors.json \
  --telemetry-jsonl .agent-drift/observations.jsonl
```

JSONL exporter 使用进程锁、完整写入和大小轮转，支持独立 Hook 进程共享文件。默认单文件上限
32 MiB、保留 3 个轮转文件、单条记录上限 1 MiB；文件和目录请求使用 `0600`、`0700` 权限。
Telemetry 写入失败不会改变 Guard 决策，但 Hook stderr 和持久化 failure counter 会暴露失败。

## 从 SQLite 历史导出

已有 session 可以直接导出，不需要重新运行平台：

```bash
agent-drift store-events .agent-drift/drift.db SESSION_ID --limit 10
agent-drift store-export-replay \
  .agent-drift/drift.db SESSION_ID .agent-drift/replays/session.jsonl
```

导出内容继承 SQLite 中的脱敏结果，并把已持久化 decision action 和完整确定性语义指纹作为期望值。
结果显式报告 session 总事件数、导出数、首尾 sequence 和 `truncated`，避免默认上限静默丢失历史。
v1 中未脱敏的历史记录不会因为导出而自动修复，发布 fixture 前仍需人工检查。

## 回放和回归判断

```bash
agent-drift replay .agent-drift/replays/session.jsonl \
  --anchors .agent-drift/anchors.json \
  --output .agent-drift/replays/session-report.json \
  --fail-on-mismatch
```

CLI 逐行读取 Replay，不会先把长会话全部载入内存；它按原顺序重建有界 session 历史并运行当前
Detector/Policy。v0.2 报告记录 anchors 指纹、历史上限、协议版本、决策/证据分布、action 与完整
确定性语义的比较，以及忽略随机 evidence/decision ID 的总语义 SHA-256 指纹。
`--fail-on-mismatch` 在 action 或语义不一致时返回退出码 1；`--summary-only` 同时避免在内存和报告中
保留逐事件条目。

人工标注过的 case 会额外计算 exact match、clean false-positive rate、TP/FP/FN、precision、recall 和
F1；标签不从旧 decision/evidence 自动推导，避免把历史模型输出误当成真值。对已经人工确认无漂移的
本地语料，可显式使用：

```bash
agent-drift replay .agent-drift/replays/clean-session.jsonl \
  --anchors .agent-drift/anchors.json \
  --assume-clean --summary-only --fail-on-mismatch
```

`--assume-clean` 会把每个事件标为零漂移，只适合已逐条审查的会话，不能替代语料标注。

真实会话内容默认永远不应提交到 Git。贡献者只应提交最小化、再次人工审查过的 replay fixture。
