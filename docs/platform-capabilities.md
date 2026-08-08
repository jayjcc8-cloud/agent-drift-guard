# Platform Capabilities v0.1

能力模型用于启动期协商，不用于宣称某个平台永久具备某项能力。能力由具体 Adapter 版本在运行时
上报，因为平台版本、配置、hook 安装状态都可能改变结果。

## 能力命名

- `observe.*`：只能观察；
- `control.*`：可以改变执行结果。

能力集合天然支持差集：Core 给出某个 policy 所需能力，Adapter 返回 supported/missing/coverage。

## Protection Level

| 等级 | 定义 |
|---|---|
| `none` | 没有任何能力 |
| `audit` | 至少能观察，但不能控制 |
| `partial` | 至少有一个控制能力，但不满足 Full Guard baseline |
| `full` | 满足 `FULL_GUARD_REQUIREMENTS` 的全部能力 |

Full Guard baseline 当前要求观察 prompt/tool/result/compaction/subagent/stop，并能 block tool、注入
context、block stop。它是 v0.1 的产品级 baseline，并不表示每个 Detector 都需要全部能力。具体
policy 应调用 `assess(required=...)` 使用自己的最小能力集。

示例：

```bash
agent-drift capabilities examples/codex-capabilities.json
```

输出中包含 `protection_level` 和完整的 `assessment`，缺失能力不会被隐藏。

