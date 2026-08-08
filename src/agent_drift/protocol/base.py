"""Shared model behavior for wire contracts."""

from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    """A strict model that serializes predictably across adapter boundaries."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        use_enum_values=False,
    )
