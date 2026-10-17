"""Only passive capture and stop requests are currently supported."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["capture", "stop"]
    channel: str | None
    sample_count: int | None = Field(ge=1000, le=100000)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def check_parameters(self):
        if self.action == "capture" and (not self.channel or self.sample_count is None):
            raise ValueError("capture requires channel and sample_count")
        if self.action == "stop" and (self.channel is not None or self.sample_count is not None):
            raise ValueError("stop parameters must be null")
        return self


def validate_action(value, report):
    request = NextAction.model_validate(value)
    if request.action == "capture":
        if request.channel != report["signal"]["channel"]["name"]:
            raise ValueError("capture channel must match the analyzed channel")
    return request
