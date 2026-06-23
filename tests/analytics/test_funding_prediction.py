from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from crypcodile.analytics.funding_prediction import XGBoostFundingPredictor


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

    # Check rolling mean with window_size=5, min_periods=1:
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


@patch("crypcodile.analytics.funding_prediction.XGBOOST_AVAILABLE", True)
def test_predictor_xgboost_path_df() -> None:
    # Mock the xgboost module and regressor
    mock_xgb = MagicMock()
    mock_regressor = MagicMock()
    mock_xgb.XGBRegressor.return_value = mock_regressor
    mock_regressor.predict.return_value = [0.012, 0.024]

    with patch("crypcodile.analytics.funding_prediction.xgb", mock_xgb):
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


@patch("crypcodile.analytics.funding_prediction.XGBOOST_AVAILABLE", True)
def test_predictor_xgboost_path_dict() -> None:
    # Mock the xgboost module and regressor
    mock_xgb = MagicMock()
    mock_regressor = MagicMock()
    mock_xgb.XGBRegressor.return_value = mock_regressor
    mock_regressor.predict.return_value = [0.015]

    with patch("crypcodile.analytics.funding_prediction.xgb", mock_xgb):
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


@patch("crypcodile.analytics.funding_prediction.XGBOOST_AVAILABLE", True)
def test_predictor_fallback_on_inference_exception() -> None:
    # Mock the xgboost module and regressor, but raise an exception during predict
    mock_xgb = MagicMock()
    mock_regressor = MagicMock()
    mock_xgb.XGBRegressor.return_value = mock_regressor
    mock_regressor.predict.side_effect = Exception("XGBoost prediction error")

    with patch("crypcodile.analytics.funding_prediction.xgb", mock_xgb):
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



@patch("crypcodile.analytics.funding_prediction.XGBOOST_AVAILABLE", False)
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
