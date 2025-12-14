from __future__ import annotations

import pytest

from .analysis.dtos import Observation
from .analysis.stats import fit_dose_response, p_value_to_stars, run_tukey_pairwise_against_control


def test_p_value_to_stars_thresholds():
    assert p_value_to_stars(None) == ""
    assert p_value_to_stars(1.0) == ""
    assert p_value_to_stars(0.049) == "*"
    assert p_value_to_stars(0.009) == "**"
    assert p_value_to_stars(0.0009) == "***"


def test_tukey_pairwise_requires_control_label_present():
    observations = [
        Observation(
            experiment_id=1,
            div=0,
            condition_label="A",
            param="mean_firing_rate",
            well="A1",
            value=1.0,
            is_control=False,
            is_knockout=False,
            is_inactive=False,
            is_excluded=False,
        )
    ]
    with pytest.raises(ValueError, match="control_label"):
        run_tukey_pairwise_against_control(observations, control_label="Control")


def test_fit_dose_response_linear_model():
    fit = fit_dose_response(
        [0.1, 1.0, 10.0],
        [2.0, 3.0, 4.0],
        models=("linear",),
        fit_points=10,
    )
    assert fit.model == "linear"
    assert len(fit.x_fit) == 10
    assert len(fit.y_fit) == 10


def test_fit_dose_response_weighted():
    # Points: (1, 1), (2, 2.1), (3, 2.9) -> almost y=x
    # But point (2, 2.1) has huge error, (1,1) and (3,3) have small error.
    # Unweighted fit might be pulled by 2.1.
    # Weighted fit should ignore 2.1 and be closer to y=x.
    x = [1.0, 2.0, 3.0]
    y = [1.0, 3.0, 3.0]
    errors = [0.1, 10.0, 0.1]  # Middle point is very uncertain

    # With high error on middle point, line should go through (1,1) and (3,3) mostly.
    # Slope should be ~1, Intercept ~0.
    fit = fit_dose_response(x, y, sigma=errors, models=("linear",), fit_points=2)

    m = fit.params["m"]
    c = fit.params["c"]

    # y = 1x + 0
    assert 0.9 <= m <= 1.1
    assert -0.2 <= c <= 0.2
