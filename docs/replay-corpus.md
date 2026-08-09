# v0.7 真实语料与 Detector 证据门

v0.7 只回答一个问题：现有确定性 Detector 在隐私复核后的真实 Hook 语料上是否可靠。它不是集成
版本，不增加 CLI、协议、导出类型、Adapter、DriftType、LLM Judge、OTLP、诊断包或本地 daemon。

## 来源与覆盖

语料由一次性、无用户数据的本地 Python 小仓库产生。原始 `.agent-drift` 目录只存在于 gitignored
临时目录，没有进入仓库。Codex 使用 `codex-cli 0.147.0`、`workspace-write` 和自动审批；Claude
Code 使用 `2.1.98`、受限工具列表、自动权限模式和每次 5 美元上限。Claude Code 客户端通过本机
配置的 Anthropic 兼容 Provider 执行；语料证明的是 Claude Code Hook/Adapter 契约，不宣称底层
模型来自 Anthropic。两边都没有使用危险权限绕过。

| 平台 | 场景 | 公开事件 | 期望决策 | 期望漂移 |
|---|---:|---:|---|---|
| Codex | clean | 6 | allow × 6 | 无 |
| Codex | loop | 5 | allow × 4, block × 1 | loop × 1 |
| Codex | validation | 5 | allow × 4, continue × 1 | validation × 1, state × 1 |
| Codex | lifecycle | 4 | allow × 4 | 无 |
| Claude Code | clean | 6 | allow × 6 | 无 |
| Claude Code | loop | 5 | allow × 4, block × 1 | loop × 1 |
| Claude Code | validation | 5 | allow × 4, continue × 1 | validation × 1, state × 1 |
| Claude Code | lifecycle | 4 | allow × 4 | 无 |

共 40 个事件，全部有人工 `expected_drift_types` 标签。标签复核完成后才固化
`expected_action`、逐事件语义指纹和 `expected-report.json`。每个 case 的 `provenance.json` 记录
平台版本、场景、事件数量、真实受控来源 SHA-256、隐私复核状态、期望决策与漂移分布。

生命周期场景在两个平台都真实产生了 subagent start/stop 和 compact before/after。Claude 在恢复
同一会话执行 `/compact` 时额外产生 `session.start`；最小化语料删除了这个无关恢复事件，并在
provenance 中保留差异说明。失败验证在 Codex 中是纯文本 `tool.after`，在 Claude 中是
`tool.error`，但二者的漂移类型与决策分布必须一致。

## 最小化与隐私契约

每个公开 case 只保留 4–6 个 Detector 判定所需事件，并执行以下不可协商约束：

- UUID、session ID、时间和路径全部确定性化；路径只能位于 `/workspace/fixture`；
- 删除原始 prompt、工具输出、绝对用户路径、transcript 路径和无关 extensions；
- 命令只允许两个受控 unittest 命令；完成声明只保留 `Implementation completed.`；
- 不保留凭据模式，也不使用 `[REDACTED]` 代替未完成的清理；
- 原始 capture 只用不可逆 SHA-256 关联 provenance，不进入 Git。

`tests/test_replay_corpus.py` 同时检查目录结构、来源矩阵、32–48 事件总量、隐私模式、最小化字段、
安全命令白名单、标签完整性和两个平台的对等分布。

## Baseline 与证据驱动修正

未经 v0.7 调整的 v0.6 行为先在同一批人工标签上记录，再只修复真实会话暴露的问题：

| 指标 | v0.6 baseline | v0.7 corrected |
|---|---:|---:|
| exact match | 36/40 (90.00%) | 40/40 (100%) |
| clean false-positive rate | 2/36 (5.56%) | 0/36 (0%) |
| true positives | 4 | 6 |
| false positives | 2 | 0 |
| false negatives | 2 | 0 |
| precision | 66.67% | 100% |
| recall | 66.67% | 100% |
| F1 | 66.67% | 100% |

两项修正都有原始真实证据：

1. Codex Bash `PostToolUse` 返回纯文本，Claude Bash 返回没有退出码的 `stdout/stderr` 对象；二者的
   unittest 结果只能由独立终止行 `OK` 或 `FAILED (...)` 确认。归一化只识别这些确定性标记，其他
   文本仍为 `unknown`。
2. Claude 会给五次相同命令附加不同的展示说明（`attempt 1 of 5` 等）。LoopDetector 指纹现在只
   忽略不影响执行的 `description`，命令和其余参数仍全部参与匹配。

没有为了测试新增无证据规则。Precision、recall 和 F1 作为报告项发布，但 40 个事件不足以形成
统计门槛；公开人工标注达到 50 个后再单独制定阈值。

## 运行证据门

单个 case 继续使用现有 CLI：

```bash
agent-drift replay tests/fixtures/replay/v0.7/codex-clean/replay.jsonl \
  --anchors tests/fixtures/replay/v0.7/codex-clean/anchors.json \
  --summary-only --fail-on-mismatch
```

CI 对每个 case 连续 replay 两次，要求总语义指纹一致、action mismatch 为零、语义 mismatch 为零、
标签 exact match 为 100%、clean false-positive rate 为 0%，并且每个期望正例无漏报。完整 pytest
套件继续承担 Constraint/Scope 契约覆盖；Goal/Plan/Decision 暂无 Detector，不在本语料中伪造覆盖。
