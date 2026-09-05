# 一键 Hook 安装

安装器以项目级配置为边界，保留已有 matcher group、Hook 和其他设置，只管理命令中带
`AGENT_DRIFT_GUARD=1` 标记的 handler。

```bash
agent-drift install-hooks codex --project-root /project --anchors /project/anchors.json --mode observe
agent-drift install-hooks claude-code --project-root /project --anchors /project/anchors.json --mode observe

agent-drift hook-status codex --project-root /project
agent-drift uninstall-hooks codex --project-root /project
```

使用 `--dry-run` 可比较配置而不写文件。安装操作会：

1. 更新 Codex 的 `.codex/hooks.json` 或 Claude Code 的 `.claude/settings.local.json`；
2. 把验证后的 anchors 保存到私有 `.agent-drift/anchors.json`；
3. 强制 `.agent-drift/` 内所有目录为 `0700`、runner 为 `0700`，配置及其余运行文件为 `0600`；
4. 自动把 `.agent-drift/` 和 Claude local settings 加入项目 `.gitignore`；
5. 修改已有配置前在 `.agent-drift/backups/` 保存备份。

新安装的私有 runner 总会显式包含 `hook ... --mode observe`。`observe` 仍运行 Adapter、五个 Detector、
Policy、SQLite 和 exporter，并在 observation 中保存 proposed `GuardDecision`；实际 Hook 响应固定为空
stdout、空 stderr、exit 0，不批准或拒绝权限、不改写工具输入、不注入上下文，也不阻止 Stop、
SubagentStop 或 TaskCompleted。`enforce` 保留原有原生决策回译。

兼容规则是“新装 observe，旧装保持”：既有 runner 没有 `--mode` 时状态显示 `legacy`，其历史有效
语义仍是 enforce；显式 `--mode enforce` 的安装也不会因重入而改变。只有再次安装时明确传入 mode
才会切换。runner 缺失或 mode 无法识别时，`hook-status` 报告不健康，修复命令必须明确给出
`--mode observe` 或 `--mode enforce`，不会猜测后落入 enforce。

可共享的 Hook 配置只通过项目根定位私有 runner，不含绝对可执行文件或项目路径；这些本机信息只在
gitignored runner 内。可用 `--executable` 明确固定另一安装位置。安装器拒绝不存在的项目、不可执行
入口，以及通过符号链接逃出项目根目录的配置/数据路径。Codex 的可共享命令依赖 Git 根目录定位；
安装目标必须位于 Git worktree 内，仓库子目录会使用相对 Git 根目录的 runner 路径，非 Git 目标会
在写配置前失败。当前实现支持 macOS/POSIX，不声明 Windows 兼容性。

`hook-status` 会同时检查所有预期 handler 的完整定义与唯一性、runner 是否存在且可执行、runner
内容是否对应当前安装、anchors 是否有效、所有本地运行文件权限，以及必需的 `.gitignore` 条目。
结果中的 `mode` 显示 `observe`、`enforce`、`legacy`、`unknown` 或缺失 runner 时的空值；`healthy`
为 `false` 时会列出 `health_issues` 并返回退出码 1，便于脚本区分“配置中有标记”和“安装可实际
运行”。再次执行 `install-hooks` 会替换异常 handler，并修复权限与忽略规则；模式不明确的缺失或
损坏 runner 需要显式 mode 才会修复。

新生成的默认 anchors 同时识别 pytest、Python `unittest`、Node test scripts、Cargo、Go 和 .NET
测试，并识别 `uv run --locked pytest`、环境变量前缀和 Python unittest。默认规则只从 shell 命令
起点或显式 `&&`、`||`、`;` 命令边界识别验证，排除引号、注释和后台启动中的伪命令。任意
`scripts/verify.py` 不会自动获得验证含义；项目必须通过 `RepoAnchor.validation_command_patterns`
显式配置该入口。升级时只自动迁移与旧版生成默认值完全一致的 anchors；任何用户定制过的 task、
constraint 或 repo 模式均保持原样。

Codex 官方文档说明项目 Hook 从 `.codex/hooks.json` 加载，且非托管命令需要在 `/hooks` 中按当前
定义审查并信任；安装器不会绕过此步骤。Claude Code 同样提醒命令 Hook 以用户完整权限执行，应先
检查生成配置。参考：[Codex Hooks](https://developers.openai.com/codex/hooks)、
[Claude Code Hooks](https://code.claude.com/docs/en/hooks)。

卸载只移除 Agent Drift Guard handler，不删除 SQLite、telemetry、备份或 `.gitignore` 保护，以避免
隐式数据销毁。

当前安装矩阵包含 Codex 11 类事件和 Claude Code 14 类事件，包括 `PermissionRequest`；Claude 另外
包含 `TaskCompleted` 与只观察的 `StopFailure`。
