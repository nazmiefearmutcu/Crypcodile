from __future__ import annotations

from typing import Any

import polars as pl

XGBOOST_AVAILABLE = False
try:
    import xgboost
    XGBOOST_AVAILABLE = True
except BaseException:
    pass

xgb = None


def is_xgboost_available() -> bool:
    return XGBOOST_AVAILABLE


def predict_next_funding(
    historical: pl.DataFrame | list[float] | tuple[float, ...],
    *,
    window_size: int = 5,
    target_col: str = "funding_rate",
) -> dict[str, Any]:
    """Predict the next-period funding rate from pure offline history.

    Accepts either a Polars DataFrame with a ``funding_rate`` (or
    ``target_col``) column, or a sequence of floats. Trains
    :class:`XGBoostFundingPredictor` when XGBoost is available; otherwise
    uses the built-in rolling-mean heuristic (no network, no data lake).

    Returns a dict with:
      - ``predicted_funding_rate`` (float)
      - ``method`` (``"xgboost"`` | ``"rolling_mean"``)
      - ``window_size`` (int)
      - ``n_history`` (int)
      - ``xgboost_available`` (bool)
    """
    if window_size < 1:
        raise ValueError("window_size must be >= 1")

    if isinstance(historical, (list, tuple)):
        rates = [float(r) for r in historical]
        if not rates:
            raise ValueError("historical rates list is empty")
        df = pl.DataFrame({target_col: rates})
    elif isinstance(historical, pl.DataFrame):
        df = historical
        if target_col not in df.columns:
            raise ValueError(
                f"historical DataFrame must contain column {target_col!r}"
            )
        if len(df) == 0:
            raise ValueError("historical DataFrame is empty")
    else:
        raise TypeError("historical must be a polars DataFrame or sequence of floats")

    predictor = XGBoostFundingPredictor(
        target_col=target_col,
        window_size=window_size,
    )
    predictor.train(df)

    # Build next-step features from the last rows (lag columns + last extras).
    features: dict[str, Any] = {}
    rates = [float(r) for r in df[target_col].drop_nulls().to_list()]
    if predictor.feature_cols:
        for col in predictor.feature_cols:
            if col.startswith(f"{target_col}_lag"):
                try:
                    lag = int(col.rsplit("lag", 1)[1])
                except ValueError:
                    continue
                if len(rates) >= lag:
                    features[col] = rates[-lag]
            elif col in df.columns:
                vals = df[col].drop_nulls()
                if len(vals) > 0:
                    features[col] = float(vals[-1])

    can_use_xgb = (
        is_xgboost_available()
        and predictor._is_trained
        and predictor.model is not None
        and predictor.feature_cols is not None
        and bool(predictor.feature_cols)
        and all(c in features for c in predictor.feature_cols)
    )

    if can_use_xgb:
        try:
            import numpy as np

            vals = [features[c] for c in predictor.feature_cols]  # type: ignore[union-attr]
            preds = predictor.model.predict(np.array([vals], dtype=np.float64))
            predicted = float(preds[0])
            method = "xgboost"
        except Exception:
            # Empty dict -> rolling mean of recent_rates / fallback mean.
            predicted = float(predictor.predict({}))
            method = "rolling_mean"
    else:
        predicted = float(predictor.predict({}))
        method = "rolling_mean"

    return {
        "predicted_funding_rate": float(predicted),
        "method": method,
        "window_size": window_size,
        "n_history": len(df),
        "xgboost_available": is_xgboost_available(),
    }


class XGBoostFundingPredictor:
    """Predicts next-period funding rates using an XGBoost regressor,
    with a rolling historical average fallback in case XGBoost is missing
    or untrained.
    """

    def __init__(
        self,
        target_col: str = "funding_rate",
        feature_cols: list[str] | None = None,
        window_size: int = 5,
    ) -> None:
        self.target_col = target_col
        self.feature_cols = feature_cols
        self.window_size = window_size
        self.model = None
        self._is_trained = False
        self._fallback_mean = 0.0
        self.recent_rates: list[float] = []

    def train(self, historical_data: pl.DataFrame) -> None:
        """Extracts features and trains an xgboost.XGBRegressor model to predict
        the next-period funding rate.
        """
        global xgb
        # Save recent rates and fallback mean from the historical data
        if self.target_col in historical_data.columns:
            rates = historical_data[self.target_col].drop_nulls()
            self._fallback_mean = float(rates.mean()) if len(rates) > 0 else 0.0
            self.recent_rates = [float(r) for r in rates.tail(self.window_size).to_list()]
        else:
            self._fallback_mean = 0.0
            self.recent_rates = []

        if not is_xgboost_available():
            self._is_trained = False
            return

        if xgb is None:
            import xgboost as xgb

        # Prepare feature columns if not specified
        df = historical_data.clone()
        lag_features = []
        if self.target_col in df.columns:
            for lag in [1, 2, 3]:
                lag_name = f"{self.target_col}_lag{lag}"
                if lag_name not in df.columns:
                    df = df.with_columns(pl.col(self.target_col).shift(lag).alias(lag_name))
                lag_features.append(lag_name)

        if self.feature_cols is None:
            exclude = {self.target_col, "symbol", "exchange", "local_ts", "date", "funding_ts", "interval_hours"}
            self.feature_cols = [
                col
                for col in df.columns
                if col not in exclude
                and df[col].dtype
                in (
                    pl.Float32,
                    pl.Float64,
                    pl.Int8,
                    pl.Int16,
                    pl.Int32,
                    pl.Int64,
                )
            ]
            for lf in lag_features:
                if lf in df.columns and lf not in self.feature_cols:
                    self.feature_cols.append(lf)

        if not self.feature_cols:
            self._is_trained = False
            return

        # Drop nulls for training
        train_df = df.select(self.feature_cols + [self.target_col]).drop_nulls()
        if len(train_df) == 0:
            self._is_trained = False
            return

        try:
            X = train_df.select(self.feature_cols).to_numpy()
            y = train_df[self.target_col].to_numpy()
            self.model = xgb.XGBRegressor(
                n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42
            )
            self.model.fit(X, y)
            self._is_trained = True
        except Exception:
            self._is_trained = False

    def predict(self, current_features: dict[str, Any] | pl.DataFrame) -> float | pl.Series:
        """Predicts the next-period funding rate.

        If current_features is a dictionary, returns a float.
        If current_features is a Polars DataFrame, returns a Polars Series.
        If XGBoost is not available, untrained, or fails during inference,
        falls back gracefully to a heuristic rolling mean of recent historical rates.
        """
        global xgb
        # Handle dict-based prediction (single prediction -> float)
        if isinstance(current_features, dict):
            # Try XGBoost
            if is_xgboost_available() and self._is_trained and self.model is not None:
                if xgb is None:
                    import xgboost as xgb
                try:
                    if self.feature_cols is None:
                        raise ValueError("feature_cols not initialized")
                    
                    vals = []
                    for col in self.feature_cols:
                        if col not in current_features:
                            raise ValueError(f"Missing feature: {col}")
                        vals.append(current_features[col])
                    
                    import numpy as np
                    X = np.array([vals], dtype=np.float64)
                    preds = self.model.predict(X)
                    
                    # Update recent history
                    if self.target_col in current_features:
                        self.recent_rates.append(float(current_features[self.target_col]))
                        if len(self.recent_rates) > self.window_size:
                            self.recent_rates.pop(0)
                    
                    return float(preds[0])
                except Exception:
                    pass

            # Fallback path for dictionary input
            # Check if there is explicit history provided
            recent = current_features.get("recent_funding_rates")
            if recent is not None:
                try:
                    rates_list = [float(r) for r in recent if r is not None]
                    if rates_list:
                        return sum(rates_list) / len(rates_list)
                except Exception:
                    pass

            # Add current target value to running history if provided
            if self.target_col in current_features:
                try:
                    self.recent_rates.append(float(current_features[self.target_col]))
                    if len(self.recent_rates) > self.window_size:
                        self.recent_rates.pop(0)
                except Exception:
                    pass

            if self.recent_rates:
                return sum(self.recent_rates) / len(self.recent_rates)
            return self._fallback_mean

        # Handle DataFrame-based prediction (multiple predictions -> pl.Series)
        elif isinstance(current_features, pl.DataFrame):
            if is_xgboost_available() and self._is_trained and self.model is not None:
                if xgb is None:
                    import xgboost as xgb
                try:
                    if self.feature_cols is None:
                        raise ValueError("feature_cols not initialized")
                    
                    for col in self.feature_cols:
                        if col not in current_features.columns:
                            raise ValueError(f"Missing feature: {col}")
                    
                    X = current_features.select(self.feature_cols).to_numpy()
                    preds = self.model.predict(X)
                    return pl.Series("predicted_funding_rate", preds, dtype=pl.Float64)
                except Exception:
                    pass

            # Fallback path for DataFrame input
            if self.target_col in current_features.columns:
                try:
                    rolling_mean = current_features[self.target_col].rolling_mean(
                        window_size=self.window_size, min_samples=1
                    )
                    if rolling_mean.null_count() > 0:
                        rolling_mean = rolling_mean.fill_null(self._fallback_mean)
                    return rolling_mean.alias("predicted_funding_rate")
                except Exception:
                    return pl.Series(
                        "predicted_funding_rate",
                        [self._fallback_mean] * len(current_features),
                        dtype=pl.Float64,
                    )
            else:
                return pl.Series(
                    "predicted_funding_rate",
                    [self._fallback_mean] * len(current_features),
                    dtype=pl.Float64,
                )
        
        else:
            raise TypeError("current_features must be dict or pl.DataFrame")
