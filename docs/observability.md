# 可观测性 Exporter

`ObservationEnvelope` v0.1 是平台无关的运行结果信封：

- `observed_at`：观察完成时间；
- `processing_duration_ms`：Adapter、Supervisor 和决策回译耗时；
- `supervision`：规范化事件、Detector evidence 和抽象 decision；
- `response`：实际应用到平台的动作和原生 Hook 输出。

在 observe 模式中，`supervision.decision` 继续记录 Policy 原本提出的动作，`response` 则明确记录
`applied_action="observe"`、空 stdout/stderr 与 exit 0。两者不能合并成一批伪造的 `ALLOW`。observe
路径可捕获的配置、输入、存储或 exporter 故障只向操作日志写入固定、脱敏的 unavailable/failed
诊断；它不输出权限决定或继续指令，也不把观测失败报告成“没有漂移”。

当前提供 `JsonlExporter` 和 `CompositeExporter`。JSONL 默认最多使用约 128 MiB（32 MiB 当前文件加
3 个备份），单条记录上限 1 MiB；跨 Hook 进程轮转由文件锁串行化。API 使用示例：

```python
runtime = AgentDriftRuntime(
    CodexAdapter(),
    Supervisor(anchors, store=SQLiteStore(".agent-drift/drift.db")),
    exporter=JsonlExporter(".agent-drift/observations.jsonl"),
)
```

CLI Hook 使用 SQLite 时，Exporter 接收到的是已经脱敏的持久化事件。直接在无 Store 的 API Runtime
上启用 Exporter 时，调用方必须先保证事件不包含敏感内容。

Exporter 是 best-effort：导出异常记录在 `RuntimeOutcome.export_error`，不会把 `allow` 改成 `block`
或反过来。失败次数、最近失败时间与截断后的错误保存在私有 health 文件，可运行：

```bash
agent-drift telemetry-status .agent-drift/observations.jsonl
```

手工配置 `agent-drift hook` 时可用 `--telemetry-max-bytes`、`--telemetry-backups` 和
`--telemetry-max-record-bytes` 调整边界。一键安装器当前使用上述默认值。JSONL 继续作为本地事实源；
OTLP/HTTP 映射将在真实语料质量门通过后加入，避免过早冻结错误的 span/log 语义。
