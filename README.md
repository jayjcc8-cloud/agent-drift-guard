# Agent Drift Guard

Agent Drift Guard 是一个平台无关的 AI Agent Runtime Supervisor。它把 Codex、Claude Code、
Gemini CLI 等平台的生命周期事件转换成统一协议，再由独立的检测、策略和恢复模块判断是否发生
目标漂移、约束遗忘、状态误判、计划偏离或循环失控。

当前实现包含 v0.1 协议基线、两个原生 Hook Adapter、确定性监督器和可运维的本地运行时：

- `AgentEvent`：平台无关、可排序、可关联的事件信封；
- `GuardDecision`：不泄漏平台术语的抽象决策；
- `PlatformCapabilities`：显式描述 Adapter 能观察和控制什么。
- `CodexAdapter` / `ClaudeCodeAdapter`：把官方 Hook JSON 转成同一事件，并回译原生决策；
- `Supervisor`：执行约束、范围、循环、验证和状态确定性检测。
- `SQLiteStore`：跨独立 Hook 进程保存事件、证据、决策和 session sequence，并提供默认脱敏、
  保留清理和事务化 schema migration。
- `JsonlExporter` / replay：采集脱敏真实长会话、重跑当前策略并比较决策回归。
- Hook installer：幂等安装、检查或卸载 Codex 与 Claude Code 项目 Hook。

这三个契约刻意不依赖 Detector、LLM Judge、数据库或具体平台 SDK。后续模块只能依赖它们，
不能反向把平台细节带入 Core。

## 快速开始

```bash
uv sync --no-editable
uv run agent-drift validate-event examples/tool-before.json
uv run agent-drift capabilities examples/codex-capabilities.json
uv run agent-drift adapt-hook codex tests/fixtures/codex/pre_tool_use.json --repo-root /project
uv run agent-drift adapter-capabilities claude-code
uv run agent-drift store-init .agent-drift/drift.db
uv run agent-drift install-hooks codex --project-root . --anchors examples/anchors.json
uv run agent-drift benchmark-hook codex tests/fixtures/codex/pre_tool_use.json \
  --anchors examples/anchors.json --iterations 30
uv run pytest
```

也可以从标准输入读取 JSON：

```bash
cat examples/tool-before.json | uv run agent-drift validate-event -
```

## 设计入口

- [架构与边界](docs/architecture.md)
- [Event Protocol v0.1](docs/event-protocol.md)
- [Guard Decision v0.1](docs/guard-decision.md)
- [Platform Capabilities v0.1](docs/platform-capabilities.md)
- [真实 Adapter API 与契约](docs/adapter-api.md)
- [Supervisor 与确定性 Detector](docs/supervisor.md)
- [SQLite Store 与持久化 Hook](docs/sqlite-store.md)
- [脱敏、保留与迁移](docs/privacy-retention.md)
- [Hook 延迟基线与 daemon 决策](docs/hook-performance.md)
- [真实长会话 Replay](docs/replay.md)
- [可观测性 Exporter](docs/observability.md)
- [一键 Hook 安装](docs/hook-installation.md)

## 当前范围

已实现协议模型、严格 JSON 校验、能力协商、Codex/Claude Code Adapter、决策回译、Supervisor、
五个确定性 Detector、SQLite WAL Store、默认写盘前脱敏、保留清理、v1→v2 迁移、持久化 Hook CLI、
真实长会话 JSONL/replay、可观测性 envelope、幂等 Hook 安装和跨平台契约测试均已实现。尚未实现
LLM Judge 和 OTLP/HTTP exporter。daemon 已从正确性前置条件降级为有性能证据后才启用的可选部署方式。
