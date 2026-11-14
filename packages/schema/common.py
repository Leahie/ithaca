from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    # I-G7: unknown fields are ignored, never an error.
    model_config = ConfigDict(extra="ignore")
