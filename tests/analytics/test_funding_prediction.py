from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from crocodile.crypto.analytics.funding_prediction import (
    XGBoostFundingPredictor,
    predict_next_funding,
)


def test_predictor_fallback_rolling_mean_untrained_df() -> None:
    # Model is untrained, should fall back to rolling mean of features_df["funding_rate"]
    predictor = XGBoostFundingPredictor(window_size=5)
    df = pl.DataFrame({
        "funding_rate": [0.01, 0.02, 0.03, 0.04, 0.05],
        "feature1": [1.0, 2.0, 3.0, 4.0, 5.0],
    })

    preds = predictor.predict(df)
    assert isinstance(preds, pl.Series)
    assert len(preds) == 5

    # Check rolling mean with window_size=5, min_samples=1:
    # index 0: mean([0.01]) = 0.01
    # index 1: mean([0.01, 0.02]) = 0.015
    # index 2: mean([0.01, 0.02, 0.03]) = 0.02
    assert abs(preds[0] - 0.01) < 1e-9
    assert abs(preds[1] - 0.015) < 1e-9
    assert abs(preds[2] - 0.02) < 1e-9


def test_predictor_fallback_rolling_mean_untrained_dict() -> None:
    predictor = XGBoostFundingPredictor(window_size=3)
    
    # 1. Fallback using explicit "recent_funding_rates" key in dict
    features_with_history = {
        "feature1": 1.5,
        "recent_funding_rates": [0.01, 0.02, 0.03],
    }
    pred = predictor.predict(features_with_history)
    assert isinstance(pred, float)
    assert abs(pred - 0.02) < 1e-9

    # 2. Fallback using instance history (populated during training)
    train_df = pl.DataFrame({
        "funding_rate": [0.01, 0.02, 0.03, 0.04, 0.05],
    })
    predictor.train(train_df)
    # The last 3 (window_size) rates are [0.03, 0.04, 0.05] -> mean is 0.04
    pred = predictor.predict({"feature1": 1.5})
    assert isinstance(pred, float)
    assert abs(pred - 0.04) < 1e-9

    # 3. Predictor updates instance history when predicting with target_col present
    # Current rates: [0.03, 0.04, 0.05]
    # Predict with new funding_rate = 0.06 -> history becomes [0.04, 0.05, 0.06] -> mean 0.05
    pred = predictor.predict({"funding_rate": 0.06, "feature1": 1.5})
    assert isinstance(pred, float)
    assert abs(pred - 0.05) < 1e-9


def test_predictor_fallback_constant_mean_without_target_col() -> None:
    predictor = XGBoostFundingPredictor()
    # Train with a target column to establish the fallback average
    train_df = pl.DataFrame({
        "funding_rate": [0.02, 0.04],
        "feature1": [1.0, 2.0],
    })
    predictor.train(train_df)

    # Predict with a DataFrame lacking "funding_rate"
    predict_df = pl.DataFrame({
        "feature1": [1.0, 2.0, 3.0],
    })

    preds = predictor.predict(predict_df)
    # The mean of train_df["funding_rate"] is 0.03
    assert preds.to_list() == [0.03, 0.03, 0.03]

    # Predict with a dict lacking "funding_rate"
    pred = predictor.predict({"feature1": 1.5})
    assert pred == 0.03


@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", True)
def test_predictor_xgboost_path_df() -> None:
    # Mock the xgboost module and regressor
    mock_xgb = MagicMock()
    mock_regressor = MagicMock()
    mock_xgb.XGBRegressor.return_value = mock_regressor
    mock_regressor.predict.return_value = [0.012, 0.024]

    with patch("crocodile.crypto.analytics.funding_prediction.xgb", mock_xgb):
        predictor = XGBoostFundingPredictor(feature_cols=["feature1"])
        
        train_df = pl.DataFrame({
            "funding_rate": [0.01, 0.02],
            "feature1": [1.0, 2.0],
        })
        
        predictor.train(train_df)
        assert predictor._is_trained is True

        predict_df = pl.DataFrame({
            "feature1": [1.5, 2.5],
        })
        preds = predictor.predict(predict_df)
        
        assert preds.to_list() == [0.012, 0.024]
        mock_regressor.fit.assert_called_once()
        mock_regressor.predict.assert_called_once()


@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", True)
def test_predictor_xgboost_path_dict() -> None:
    # Mock the xgboost module and regressor
    mock_xgb = MagicMock()
    mock_regressor = MagicMock()
    mock_xgb.XGBRegressor.return_value = mock_regressor
    mock_regressor.predict.return_value = [0.015]

    with patch("crocodile.crypto.analytics.funding_prediction.xgb", mock_xgb):
        predictor = XGBoostFundingPredictor(feature_cols=["feature1"])
        
        train_df = pl.DataFrame({
            "funding_rate": [0.01, 0.02],
            "feature1": [1.0, 2.0],
        })
        
        predictor.train(train_df)
        assert predictor._is_trained is True

        pred = predictor.predict({"feature1": 1.5})
        assert isinstance(pred, float)
        assert abs(pred - 0.015) < 1e-9
        mock_regressor.predict.assert_called_once()


@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", True)
def test_predictor_fallback_on_inference_exception() -> None:
    # Mock the xgboost module and regressor, but raise an exception during predict
    mock_xgb = MagicMock()
    mock_regressor = MagicMock()
    mock_xgb.XGBRegressor.return_value = mock_regressor
    mock_regressor.predict.side_effect = Exception("XGBoost prediction error")

    with patch("crocodile.crypto.analytics.funding_prediction.xgb", mock_xgb):
        predictor = XGBoostFundingPredictor(feature_cols=["feature1"], window_size=3)
        
        train_df = pl.DataFrame({
            "funding_rate": [0.01, 0.02, 0.03],
            "feature1": [1.0, 2.0, 3.0],
        })
        
        predictor.train(train_df)
        assert predictor._is_trained is True

        # Even though model is trained, predict raises an exception.
        # It must fall back gracefully to the rolling mean of recent rates.
        # Train rates last 3: [0.01, 0.02, 0.03] -> mean 0.02
        pred = predictor.predict({"feature1": 1.5})
        assert pred == 0.02



@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", False)
def test_predictor_fallback_when_xgboost_missing() -> None:
    predictor = XGBoostFundingPredictor(feature_cols=["feature1"], window_size=3)
    
    train_df = pl.DataFrame({
        "funding_rate": [0.01, 0.02, 0.03],
        "feature1": [1.0, 2.0, 3.0],
    })
    
    predictor.train(train_df)
    assert predictor._is_trained is False
    assert predictor.model is None

    # Predict should fall back to mean of recent rates [0.01, 0.02, 0.03] -> 0.02
    pred = predictor.predict({"feature1": 1.5})
    assert pred == 0.02


def test_no_blanket_xgboost_stub_is_installed_for_the_session() -> None:
    """The conftest line whose removal these tests depend on, asserted rather than trusted.

    ``sys.modules["xgboost"] = MagicMock()`` sat in ``tests/conftest.py`` as a safeguard
    against a missing libomp. It made ``XGBOOST_AVAILABLE`` true on every machine, so
    ``predict_next_funding`` took its xgboost branch, ``MagicMock.__float__`` returned 1.0,
    and the two tests below guarded their arithmetic behind ``if method == "rolling_mean"``
    — a branch nothing could reach. Re-adding it would restore that silently, because
    everything would still pass. This is what makes it not pass.

    A real xgboost is fine and is what a machine with libomp will have; a stand-in for the
    whole module, installed for the whole session, is not.
    """
    stub = sys.modules.get("xgboost")
    assert not isinstance(stub, MagicMock), (
        "a session-wide MagicMock is installed for xgboost; it does not stand in for the "
        "library, it decides which branch of the code under test runs. Patch "
        "XGBOOST_AVAILABLE in the test that wants a given branch instead."
    )


@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", False)
def test_predict_next_funding_from_list() -> None:
    """The rolling-mean branch with the arithmetic actually checked.

    Three rates could not train a lag-1/2/3 model anyway, so this used to read as
    environment-independent — but only the *method* was. The number was guarded.
    """
    result = predict_next_funding([0.01, 0.02, 0.03], window_size=3)
    assert result["method"] == "rolling_mean"
    assert result["n_history"] == 3
    assert result["window_size"] == 3
    assert result["xgboost_available"] is False
    # The whole history is one window: mean([0.01, 0.02, 0.03]).
    assert abs(result["predicted_funding_rate"] - 0.02) < 1e-9


@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", False)
def test_predict_next_funding_from_dataframe() -> None:
    df = pl.DataFrame({"funding_rate": [0.01, 0.02, 0.03, 0.04, 0.05]})
    result = predict_next_funding(df, window_size=5)
    assert result["method"] == "rolling_mean"
    assert result["n_history"] == 5
    assert abs(result["predicted_funding_rate"] - 0.03) < 1e-9


@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", False)
def test_predict_next_funding_averages_only_the_window_it_was_given() -> None:
    """``window_size`` has to bite on the fallback too, or it is a parameter in name only."""
    df = pl.DataFrame({"funding_rate": [0.01, 0.02, 0.03, 0.04, 0.05]})
    result = predict_next_funding(df, window_size=2)
    assert result["method"] == "rolling_mean"
    # The last two rates, not all five: mean([0.04, 0.05]).
    assert abs(result["predicted_funding_rate"] - 0.045) < 1e-9


class _WeightedSumRegressor:
    """A regressor stand-in that computes, so an assertion about it can be arithmetic.

    ``predict`` returns ``1·x₀ + 2·x₁ + 3·x₂`` per row. The weights are distinct on purpose:
    a mean would pass whatever order the features arrived in, and the order is the thing
    ``predict_next_funding`` assembles by hand out of ``funding_rate_lagN`` column names.
    A ``MagicMock`` here returns 1.0 for any input and would assert none of that.
    """

    def __init__(self, **_kwargs: object) -> None:
        self.fit_calls: list[tuple[int, int]] = []

    def fit(self, X: object, y: object) -> None:
        self.fit_calls.append((len(X), len(X[0])))  # type: ignore[arg-type,index]

    def predict(self, X: object) -> list[float]:
        return [sum(w * v for w, v in zip((1.0, 2.0, 3.0), row)) for row in X]  # type: ignore[attr-defined]


@patch("crocodile.crypto.analytics.funding_prediction.XGBOOST_AVAILABLE", True)
def test_predict_next_funding_hands_the_model_the_lags_newest_first() -> None:
    """The xgboost branch, with a model whose answer is derivable by hand.

    Five rates leave two trainable rows after lag-1/2/3 shifting, which is what makes this
    the branch taken. The features ``predict_next_funding`` builds for the next step are
    ``lag1=0.05, lag2=0.04, lag3=0.03`` — the tail of the history, newest first — so the
    stand-in's weighted sum is ``1(0.05) + 2(0.04) + 3(0.03) = 0.22``. Any other ordering,
    or a history read from the wrong end, gives a different number.
    """
    model = _WeightedSumRegressor()
    fake_xgb = MagicMock()
    fake_xgb.XGBRegressor.return_value = model

    with patch("crocodile.crypto.analytics.funding_prediction.xgb", fake_xgb):
        result = predict_next_funding([0.01, 0.02, 0.03, 0.04, 0.05], window_size=5)

    assert result["method"] == "xgboost"
    assert result["xgboost_available"] is True
    assert abs(result["predicted_funding_rate"] - 0.22) < 1e-9
    # Two rows survive the lag shift, three lag columns wide; a model fitted on anything
    # else was fed a different frame than the one the prediction claims to come from.
    assert model.fit_calls == [(2, 3)]


def test_predict_next_funding_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        predict_next_funding([])
    with pytest.raises(ValueError, match="empty"):
        predict_next_funding(pl.DataFrame({"funding_rate": []}))
    with pytest.raises(ValueError, match="funding_rate"):
        predict_next_funding(pl.DataFrame({"other": [1.0]}))
    with pytest.raises(ValueError, match="window_size"):
        predict_next_funding([0.01], window_size=0)
