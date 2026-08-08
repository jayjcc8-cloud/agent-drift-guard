# 可观测性 Exporter

`ObservationEnvelope` v0.1 是平台无关的运行结果信封：

- `observed_at`：观察完成时间；
- `processing_duration_ms`：Adapter、Supervisor 和决策回译耗时；
- `supervision`：规范化事件、Detector evidence 和抽象 decision；
- `response`：实际应用到平台的动作和原生 Hook 输出。

当前提供 `JsonlExporter` 和 `CompositeExporter`。API 使用示例：

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
或反过来。JSONL 作为 v0.5 的本地事实源，先用于 replay 和运行审计；OTLP/HTTP 映射将在 envelope
经真实长会话验证后加入，避免过早冻结错误的 span/log 语义。
