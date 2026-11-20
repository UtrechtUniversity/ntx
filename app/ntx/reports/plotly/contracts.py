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


class PlotlyWellOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str


class ProjectReportExperimentMetadataPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    selected_experiment: int
    available_params: list[PlotlyParamOption] = Field(default_factory=list)
    available_wells: list[PlotlyWellOption] = Field(default_factory=list)


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
    # Experiments available for this project-level report
    available_experiments: list[dict[str, object]] = Field(default_factory=list)
    # Set only when exactly one experiment is in scope (always the case for scatter).
    selected_experiment: int | None = None
    # Multi-select filters; omitted (None) means "all".
    selected_experiments: list[int] | None = None
    selected_chemicals: list[str] | None = None
    selected_concentrations: list[str] | None = None
    # Cascading filter options: chemicals -> concentrations (of the selected
    # chemicals) -> experiments (that contain the selected chemical/concentration).
    available_chemicals: list[str] = Field(default_factory=list)
    available_concentrations: list[str] = Field(default_factory=list)
    available_experiment_ids: list[int] = Field(default_factory=list)
    available_wells: list[PlotlyWellOption] = Field(default_factory=list)
    selected_wells: list[str] | None = None
    selected_wells_mode: str | None = None
    activity_comparison_mode: str | None = None
    color_by_experiment: bool | None = None
