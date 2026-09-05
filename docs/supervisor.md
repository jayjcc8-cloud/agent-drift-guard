# Supervisor 与确定性 Detector

## 处理顺序

```text
native JSON -> Adapter -> AgentEvent -> Detectors -> DriftEvidence -> Policy -> GuardDecision
                                                                  -> Adapter -> native response
```

Supervisor 不读取 Codex/Claude 字段，也不生成平台决策。它为缺少 sequence 的事件分配 session 内
单调序号，保存有界历史，执行全部 Detector，再由 Policy 生成一个统一决策。

`AgentDriftRuntime` 是外层组合器：持有一个 Adapter 和一个 Supervisor，将 native JSON 一次完成
规范化、检测、决策和原生 Hook 回译。它适合嵌入 daemon、IDE host 或测试进程，同时保持 Core
对平台模块的单向依赖边界。`mode="enforce"` 是低层 API 的历史默认值；`mode="observe"` 保留
`SupervisionResult.decision` 作为 proposed decision，但把实际 `HookResponse.applied_action` 记为
`observe` 并不输出任何原生控制字段。

## 当前 Detector

| Detector | 确定性依据 | 默认动作 |
|---|---|---|
| Constraint | 工具或命令命中显式 forbidden 规则 | `BLOCK` |
| Scope | 写入路径不匹配 `allowed_write_paths` | `BLOCK` |
| Loop | 连续五次相同调用、连续五次相同失败，或 A/B 振荡三轮 | `BLOCK` / `REANCHOR` |
| Validation | 写代码后停止，但没有成功测试 | `CONTINUE` |
| State | 声称完成，但最近验证明确失败 | `CONTINUE` |

每个判断输出独立的 `DriftEvidence`，包含 detector、drift type、severity、score、confidence、facts
和 source event。`GuardDecision.evidence_ids` 只引用证据，不复制整个对象。

## 保守边界

- 空的 `allowed_write_paths` 表示未配置范围约束，不是禁止所有写入。
- Shell 可能间接写文件，但没有可靠路径证据时 Scope Detector 不猜测。
- 未识别的测试输出标记为 `unknown`，不能当成成功。
- 成功验证必须发生在最后一次已知写入之后。
- 检测历史在同一 session 的有界窗口内按 platform、repo 和 `agent_id` 过滤；父子 Agent 的写入与
  验证不能互证，但当前事件上的共同 forbidden 约束仍对每个 actor 生效。
- 宿主未提供可靠 actor 时 Adapter 记录 `unknown`，不会猜成 main；多个缺失 actor 的来源仍无法
  进一步区分。历史截断外的事实属于 coverage limitation，不通过无界扫描补齐。
- Goal/Plan/Decision drift 尚未用关键字硬判；这些需要显式 plan signal 或语义 Judge。

## 持久化模式

未传 Store 时，历史存放在 Supervisor 实例内并受 `history_limit` 限制。传入 `SQLiteStore` 后，
Supervisor 会先原子持久化当前事件，再读取该 sequence 之前的有界历史完成检测，最后事务写入证据
与决策。`agent-drift hook` 默认采用这一模式，因此不同 Hook 进程之间可以恢复同一 session 状态。

daemon 仍可用于减少 Python 启动和 SQLite 连接开销，但不再承担唯一状态副本。
