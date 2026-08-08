# v0.1 架构与边界

## 决策摘要

首版不实现八种 Detector，也不绑定 Codex/Claude Hook schema。先冻结 Core 与 Adapter 之间的三个
契约，并用真实 Adapter 的契约测试验证它们。这样平台变化只影响 Adapter，不会复制检测逻辑。

```text
platform hook -> adapter -> AgentEvent -> supervisor
                                      -> GuardDecision -> adapter -> platform action
platform <---------------- PlatformCapabilities -----------------> core
```

## 对参考方案的关键优化

1. **协议版本是数据，不是包版本。** 每个 wire model 都携带 `protocol_version`；`0.x` 的每个
   minor 均视为不兼容，`1.0` 后才允许同 major 的向后兼容读取。
2. **事件有身份和顺序。** `event_id`、`sequence`、`parent_event_id`、`trace_id` 支持去重、重放、
   跨 subagent 因果关联；仅靠 timestamp 无法可靠还原并发历史。
3. **扩展与核心字段隔离。** 核心模型禁止未知字段，平台原始数据只能进入带命名空间的
   `extensions`。这同时防止静默拼写错误和平台字段污染协议。
4. **决策带可降级语义。** `fallback_action` 让 Core 明确表示首选动作不可执行时如何退化，避免
   Adapter 私自改变安全策略。
5. **能力使用集合而非布尔字段。** 新增能力不会改变模型结构；能力可被集合运算、协商和覆盖率
   统计。`FULL` 是公开的 baseline requirement，而不是模糊标签。
6. **Wire models 不承载运行时对象。** payload 只允许 JSON value，不放 `Path`、异常对象、bytes
   或 SDK 类型，保证 CLI、SQLite、OTLP 和跨语言 bridge 使用同一份数据。

## 依赖规则

允许：

```text
adapters -> protocol <- core/detectors/policies/recovery
exporters -> protocol
store -> protocol
```

禁止：

```text
protocol -> adapters/detectors/store/exporters
core -> platform SDK
detector -> platform hook schema
```

## 下一阶段的进入条件

当前阶段已经完成 Codex/Claude Adapter、双平台等价 fixture、决策回译和确定性 Supervisor。
SQLite 跨进程状态已经完成。进入语义判断与长期运行前，仍应完成：

- 在真实 Codex/Claude 会话中采集脱敏 replay fixture，作为官方文档 fixture 的补充；
- Adapter 决策映射矩阵扩展到 PermissionRequest、TaskCompleted 等协议 v0.2 事件；
- Detector 阈值配置、抑制机制和长 session 基准测试。
- 在真实长期会话中验证 v2 保留和脱敏策略，并补充可观测性 exporter。
- 仅在目标机器基准持续显著超出延迟预算时实现可选 Unix socket daemon。
