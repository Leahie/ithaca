from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    # I-G7: unknown fields are ignored, never an error.
    model_config = ConfigDict(extra="ignore")


class Vec3(Model):
    """A point or vector in meters. The frame is stated by the field that uses it."""

    x: float
    y: float
    z: float
