from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlotlyFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[dict[str, Any]] = Field(default_factory=list)
    layout: dict[str, Any] = Field(default_factory=dict)
    frames: list[dict[str, Any]] | None = None


class PlotlyCardError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] | None = None


class PlotlyCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    type: Literal["plotly"] = "plotly"

    id: str
    title: str
    subtitle: str | None = None

    status: Literal["ok", "error"] = "ok"
    error: PlotlyCardError | None = None

    figure: PlotlyFigure | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    meta: dict[str, Any] = Field(default_factory=dict)


class ProjectReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    cards: list[PlotlyCard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
