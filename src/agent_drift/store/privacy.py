"""Secure-by-default redaction and retention policies for durable stores."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from pydantic import Field, JsonValue, model_validator

from agent_drift.protocol.base import WireModel
from agent_drift.protocol.events import AgentEvent

DEFAULT_SENSITIVE_KEYS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)

DEFAULT_VALUE_PATTERNS = (
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
    r"\bsk-[A-Za-z0-9_-]{16,}\b",
    r"\bgh[opusr]_[A-Za-z0-9]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----[\s\S]*?-----END(?: [A-Z]+)? PRIVATE KEY-----",
)


class RedactionPolicy(WireModel):
    """Rules applied to normalized events immediately before persistence."""

    enabled: bool = True
    replacement: str = Field(default="[REDACTED]", min_length=1, max_length=128)
    sensitive_keys: tuple[str, ...] = DEFAULT_SENSITIVE_KEYS
    value_patterns: tuple[str, ...] = DEFAULT_VALUE_PATTERNS

    @model_validator(mode="after")
    def validate_patterns(self) -> RedactionPolicy:
        for pattern in self.value_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid redaction pattern {pattern!r}: {exc}") from exc
        return self


class RetentionPolicy(WireModel):
    """Bound durable state by ingestion age and per-session event count."""

    max_age: timedelta | None = Field(default=timedelta(days=30), gt=timedelta(0))
    max_events_per_session: int | None = Field(default=5000, ge=1)

    @model_validator(mode="after")
    def require_bound(self) -> RetentionPolicy:
        if self.max_age is None and self.max_events_per_session is None:
            raise ValueError("retention policy must define at least one bound")
        return self


class PruneResult(WireModel):
    matched_events: int
    deleted_events: int
    deleted_sessions: int
    dry_run: bool


class EventRedactor:
    """Recursively redact sensitive keys and recognizable credential values."""

    def __init__(self, policy: RedactionPolicy | None = None) -> None:
        self.policy = policy or RedactionPolicy()
        self._sensitive_keys = {self._normalize_key(key) for key in self.policy.sensitive_keys}
        self._patterns = tuple(re.compile(pattern) for pattern in self.policy.value_patterns)

    @staticmethod
    def _normalize_key(key: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = self._normalize_key(key)
        tokens = set(normalized.split("_"))
        return (
            normalized in self._sensitive_keys
            or bool(tokens & self._sensitive_keys)
            or any(normalized.endswith(f"_{key}") for key in self._sensitive_keys)
        )

    def _redact_string(self, value: str) -> tuple[str, int]:
        redacted = value
        count = 0
        for pattern in self._patterns:
            redacted, replacements = pattern.subn(self.policy.replacement, redacted)
            count += replacements
        return redacted, count

    def _redact_value(self, value: JsonValue) -> tuple[JsonValue, int]:
        if isinstance(value, dict):
            output: dict[str, JsonValue] = {}
            count = 0
            for key, child in value.items():
                if self._is_sensitive_key(key):
                    output[key] = self.policy.replacement
                    count += 1
                else:
                    output[key], child_count = self._redact_value(child)
                    count += child_count
            return output, count
        if isinstance(value, list):
            output_list: list[JsonValue] = []
            count = 0
            for child in value:
                redacted, child_count = self._redact_value(child)
                output_list.append(redacted)
                count += child_count
            return output_list, count
        if isinstance(value, str):
            return self._redact_string(value)
        return value, 0

    def redact_event(self, event: AgentEvent) -> tuple[AgentEvent, int]:
        if not self.policy.enabled:
            return event, 0
        payload, payload_count = self._redact_value(event.payload)
        extensions, extensions_count = self._redact_value(event.extensions)
        update: dict[str, Any] = {"payload": payload, "extensions": extensions}
        for field_name in ("repo_root", "cwd", "turn_id", "trace_id"):
            value = getattr(event, field_name)
            if isinstance(value, str):
                update[field_name], field_count = self._redact_string(value)
                payload_count += field_count
        return event.model_copy(update=update), payload_count + extensions_count
