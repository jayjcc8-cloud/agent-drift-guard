# 真实长会话 Replay

Replay 输入是经过写盘前脱敏的 JSONL，支持三种逐行记录：

- `ObservationEnvelope`：实时 Hook telemetry，包含原决策；
- `ReplayCase`：`event` 加可选 `expected_action`；
- 单独的规范化 `AgentEvent`。

## 从真实 Hook 持续采集

`install-hooks` 默认把每次完成的监督结果追加到 `.agent-drift/observations.jsonl`。手工配置 Hook 时可加：

```bash
agent-drift hook codex - \
  --database .agent-drift/drift.db \
  --anchors .agent-drift/anchors.json \
  --telemetry-jsonl .agent-drift/observations.jsonl
```

JSONL exporter 使用单次 append write，支持独立 Hook 进程共享文件。文件和目录请求使用 `0600`、
`0700` 权限。Telemetry 写入失败不会改变 Guard 决策，但 Hook stderr 会报告失败。

## 从 SQLite 历史导出

已有 session 可以直接导出，不需要重新运行平台：

```bash
agent-drift store-events .agent-drift/drift.db SESSION_ID --limit 10
agent-drift store-export-replay \
  .agent-drift/drift.db SESSION_ID .agent-drift/replays/session.jsonl
```

导出内容继承 SQLite 中的脱敏结果，并把已持久化 decision 作为期望值。v1 中未脱敏的历史记录不会
因为导出而自动修复，发布 fixture 前仍需人工检查。

## 回放和回归判断

```bash
agent-drift replay .agent-drift/replays/session.jsonl \
  --anchors .agent-drift/anchors.json \
  --output .agent-drift/replays/session-report.json \
  --fail-on-mismatch
```

Replay 按原顺序重建有界 session 历史并运行当前 Detector/Policy。报告包含决策分布、证据分布、逐事件
比较、不一致数量，以及忽略随机 evidence/decision ID 的语义 SHA-256 指纹。`--fail-on-mismatch`
在任何原决策与当前决策不同时返回退出码 1，适合作为回归门禁；`--summary-only` 可抑制长条目列表。

真实会话内容默认永远不应提交到 Git。贡献者只应提交最小化、再次人工审查过的 replay fixture。
