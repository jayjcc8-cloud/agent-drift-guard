# 一键 Hook 安装

安装器以项目级配置为边界，保留已有 matcher group、Hook 和其他设置，只管理命令中带
`AGENT_DRIFT_GUARD=1` 标记的 handler。

```bash
agent-drift install-hooks codex --project-root /project --anchors /project/anchors.json
agent-drift install-hooks claude-code --project-root /project --anchors /project/anchors.json

agent-drift hook-status codex --project-root /project
agent-drift uninstall-hooks codex --project-root /project
```

使用 `--dry-run` 可比较配置而不写文件。安装操作会：

1. 更新 Codex 的 `.codex/hooks.json` 或 Claude Code 的 `.claude/settings.local.json`；
2. 把验证后的 anchors 保存到私有 `.agent-drift/anchors.json`；
3. 写入权限为 `0700` 的 `.agent-drift/<platform>-hook` 本机 runner，并配置 SQLite 和 JSONL；
4. 自动把 `.agent-drift/` 和 Claude local settings 加入项目 `.gitignore`；
5. 修改已有配置前在 `.agent-drift/backups/` 保存备份。

可共享的 Hook 配置只通过项目根定位私有 runner，不含绝对可执行文件或项目路径；这些本机信息只在
gitignored runner 内。可用 `--executable` 明确固定另一安装位置。安装器拒绝不存在的项目、不可执行
入口，以及通过符号链接逃出项目根目录的配置/数据路径。当前实现支持 macOS/POSIX，不声明 Windows
兼容性。

Codex 官方文档说明项目 Hook 从 `.codex/hooks.json` 加载，且非托管命令需要在 `/hooks` 中按当前
定义审查并信任；安装器不会绕过此步骤。Claude Code 同样提醒命令 Hook 以用户完整权限执行，应先
检查生成配置。参考：[Codex Hooks](https://developers.openai.com/codex/hooks)、
[Claude Code Hooks](https://code.claude.com/docs/en/hooks)。

卸载只移除 Agent Drift Guard handler，不删除 SQLite、telemetry、备份或 `.gitignore` 保护，以避免
隐式数据销毁。

当前安装矩阵包含 Codex 11 类事件和 Claude Code 14 类事件，包括 `PermissionRequest`；Claude 另外
包含 `TaskCompleted` 与只观察的 `StopFailure`。
