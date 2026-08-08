# Contributing

Agent Drift Guard welcomes focused issues and pull requests that preserve its platform-neutral Core.

## Development

```bash
uv sync --all-extras --no-editable
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

Use fixture-driven tests for platform Hook changes. Never commit real transcripts, credentials, prompts,
absolute home paths, or unredacted tool output. Replay fixtures must pass the built-in redactor and should
contain only the minimum fields needed to reproduce behavior.

## Pull requests

- Explain the behavioral contract being changed.
- Add deterministic tests for protocol, migration, or Adapter changes.
- Keep platform-specific fields inside Adapter code or namespaced extensions.
- Update the changelog when behavior visible to operators changes.
