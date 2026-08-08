"""Protocol version parsing and compatibility rules."""

from __future__ import annotations

import re
from functools import total_ordering
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

PROTOCOL_VERSION = "0.1"
_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@total_ordering
class ProtocolVersion(str):
    """A `major.minor` wire version with explicit compatibility semantics."""

    _parts: tuple[int, int]

    def __new__(cls, value: str) -> ProtocolVersion:
        match = _VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("protocol version must use 'major.minor', for example '0.1'")
        instance = super().__new__(cls, value)
        instance._parts = (int(match.group(1)), int(match.group(2)))
        return instance

    @property
    def major(self) -> int:
        return self._parts[0]

    @property
    def minor(self) -> int:
        return self._parts[1]

    def is_compatible_with(self, supported: str | ProtocolVersion = PROTOCOL_VERSION) -> bool:
        """Return whether a consumer can safely read this version.

        Before 1.0, every minor is treated as breaking. From 1.0 onward, a consumer can
        read the same major version up to the minor it implements.
        """

        other = supported if isinstance(supported, ProtocolVersion) else ProtocolVersion(supported)
        if self.major == 0 or other.major == 0:
            return self == other
        return self.major == other.major and self.minor <= other.minor

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, (str, ProtocolVersion)):
            return NotImplemented
        candidate = other if isinstance(other, ProtocolVersion) else ProtocolVersion(other)
        return self._parts < candidate._parts

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        del source_type, handler
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(pattern=_VERSION_PATTERN.pattern),
            serialization=core_schema.to_string_ser_schema(),
        )
