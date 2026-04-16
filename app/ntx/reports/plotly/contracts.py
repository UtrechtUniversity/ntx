from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlotlyFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[dict[str, Any]] = Field(default_factory=list)
    layout: dict[str, Any] = Field(default_factory=dict)
    frames: list[dict[str, Any]] | None = None


class PlotlyCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    type: Literal["plotly"] = "plotly"

    id: str
    title: str
    subtitle: str | None = None

    figure: PlotlyFigure | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    meta: dict[str, Any] = Field(default_factory=dict)


class PlotlyParamOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    section: str


class ProjectReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    cards: list[PlotlyCard] = Field(default_factory=list)
    available_params: list[PlotlyParamOption] = Field(default_factory=list)
    default_selected_params: list[str] = Field(default_factory=list)
    selected_params: list[str] = Field(default_factory=list)
    param_selection_mode: str | None = None
    x_axis: str | None = None
    y_axis: str | None = None
