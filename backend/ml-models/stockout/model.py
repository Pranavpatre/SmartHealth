"""
Stockout Prediction — Module 01
Uses Prophet for trend decomposition + XGBoost on residuals.
Features: day_of_week, is_monsoon (Jun–Sep), disease_calendar_weight, lead_time_days,
          expiry_pressure (vials expiring < 30d / total).
Cold-start: < 14 days of history → use district-level aggregate prior.

Schema context (001_core.sql):
  medicines(id, name, category, unit, reorder_level, lead_time_days)
  stock_batches(facility_id, medicine_id, quantity, expiry_date)
  daily_snapshots(time, facility_id, opd_count, ...)  — consumption derived externally
  disease_events(district_id, disease_name, start_date, end_date, severity)
  ai_predictions(facility_id, medicine_id, prediction_type='STOCKOUT', predicted_value,
                 confidence, reasoning, recommendation, model_version)
"""

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from prophet import Prophet
from sklearn.metrics import mean_absolute_error
from xgboost import XGBRegressor

log = logging.getLogger(__name__)


@dataclass
class StockoutPrediction:
    """Result returned by StockoutPredictor.predict().

    Intended to be persisted to ai_predictions with:
        prediction_type = 'STOCKOUT'
        predicted_value = days_until_stockout
        confidence      = confidence
        recommendation  = recommended_action
        reasoning       = JSON-encoded dict built from the reasoning string
        model_version   = StockoutPredictor.MODEL_VERSION
    """

    facility_id: str
    medicine_id: int
    days_until_stockout: int
    confidence: float                          # 0.0 – 1.0
    current_stock: int
    avg_daily_consumption: float
    recommended_action: str
    reasoning: str
    predicted_daily_consumption: list[float]   # next 7 days


class StockoutPredictor:
    """
    Predicts days until stockout for a given (facility, medicine) pair.

    Workflow
    --------
    1. Instantiate with facility_id and medicine_id.
    2. Call train() with historical daily consumption data.
    3. Call predict() with current stock level and optional contextual inputs.
    4. Persist the trained model with save() / reload with load().

    Cold-start handling
    -------------------
    If < 14 rows of history are available, the model falls back to the
    district_prior avg consumption rate and skips Prophet/XGBoost fitting.
    Confidence is capped at 0.5 in this case.
    """

    MODEL_VERSION = "1.0"

    def __init__(self, facility_id: str, medicine_id: int) -> None:
        self.facility_id = facility_id
        self.medicine_id = medicine_id
        self._prophet: Optional[Prophet] = None
        self._xgb: Optional[XGBRegressor] = None
        self._avg_consumption: float = 0.0
        self._is_trained: bool = False

    # ── Feature engineering ───────────────────────────────────────────────

    @staticmethod
    def _is_monsoon(dt: pd.Timestamp) -> int:
        """Returns 1 if the date falls in the Indian monsoon window (Jun–Sep)."""
        return int(dt.month in (6, 7, 8, 9))

    @staticmethod
    def _build_features(df: pd.DataFrame) -> pd.DataFrame:
        """
        Adds calendar-derived features to a DataFrame that has a 'ds' datetime column.
        Does not mutate the original; returns a new DataFrame.
        """
        df = df.copy()
        df["day_of_week"] = df["ds"].dt.dayofweek          # 0 = Monday
        df["month"] = df["ds"].dt.month
        df["is_monsoon"] = df["ds"].apply(StockoutPredictor._is_monsoon)
        df["day_of_year"] = df["ds"].dt.dayofyear
        return df

    # ── Training ──────────────────────────────────────────────────────────

    def train(
        self,
        history: pd.DataFrame,
        disease_weights: Optional[dict[str, float]] = None,
        district_prior: Optional[float] = None,
    ) -> float:
        """
        Fit Prophet + XGBoost on historical daily consumption.

        Parameters
        ----------
        history : pd.DataFrame
            Columns: ``date`` (date or str) and ``consumption`` (int/float).
            Represents daily units dispensed for this (facility, medicine) pair.
            Maps to consumption derived from daily_snapshots / stock_batches diffs.
        disease_weights : dict[str, float], optional
            Keys are date strings "YYYY-MM-DD", values are multiplicative weights
            (e.g. 1.3 during a dengue outbreak) derived from disease_events.
        district_prior : float, optional
            Fallback avg daily consumption for cold-start (<14 rows). If not
            supplied and history is also empty, defaults to 1.0.

        Returns
        -------
        float
            MAE on leave-last-7-days validation split. Returns 0.0 for
            cold-start cases.
        """
        if history.empty:
            self._avg_consumption = district_prior if district_prior is not None else 1.0
            self._is_trained = True
            log.warning(
                "cold_start — empty history",
                extra={"facility": self.facility_id, "medicine": self.medicine_id},
            )
            return 0.0

        df = history.rename(columns={"date": "ds", "consumption": "y"}).copy()
        df["ds"] = pd.to_datetime(df["ds"])
        df = df.sort_values("ds").reset_index(drop=True)

        # Cold-start: fewer than 14 observations → skip Prophet/XGBoost
        if len(df) < 14:
            self._avg_consumption = float(df["y"].mean()) if not df.empty else (district_prior or 1.0)
            self._is_trained = True
            log.warning(
                "cold_start — insufficient history (%d rows)",
                len(df),
                extra={"facility": self.facility_id, "medicine": self.medicine_id},
            )
            return 0.0

        self._avg_consumption = float(df["y"].mean())

        # ── Stage 1: Prophet on raw daily consumption ──────────────────
        prophet_df = df[["ds", "y"]].copy()
        self._prophet = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=False,
            seasonality_mode="multiplicative",
            changepoint_prior_scale=0.05,
            interval_width=0.80,
        )
        self._prophet.fit(prophet_df)

        # ── Stage 2: XGBoost on Prophet residuals ─────────────────────
        prophet_pred = self._prophet.predict(prophet_df[["ds"]])
        df = df.merge(prophet_pred[["ds", "yhat"]], on="ds", how="left")
        df["residual"] = df["y"] - df["yhat"]
        df = self._build_features(df)

        # Attach disease-calendar weights (from disease_events table)
        if disease_weights:
            df["disease_weight"] = (
                df["ds"].dt.strftime("%Y-%m-%d").map(disease_weights).fillna(1.0)
            )
        else:
            df["disease_weight"] = 1.0

        feature_cols = ["day_of_week", "month", "is_monsoon", "day_of_year", "disease_weight"]
        X = df[feature_cols].values
        y = df["residual"].values

        # Validation split: hold out last 7 days
        split = max(len(X) - 7, int(len(X) * 0.8))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        self._xgb = XGBRegressor(
            n_estimators=100,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
        self._xgb.fit(X_train, y_train)

        mae: float = 0.0
        if len(X_val) > 0:
            val_pred = self._xgb.predict(X_val)
            mae = float(mean_absolute_error(y_val, val_pred))

        self._is_trained = True
        log.info(
            "trained",
            extra={
                "facility": self.facility_id,
                "medicine": self.medicine_id,
                "rows": len(df),
                "mae": round(mae, 3),
            },
        )
        return mae

    # ── Prediction ────────────────────────────────────────────────────────

    def predict(
        self,
        current_stock: int,
        horizon_days: int = 7,
        lead_time_days: int = 7,
        expiry_pressure: float = 0.0,
        disease_weights: Optional[dict[str, float]] = None,
        medicine_name: str = "medicine",
        reorder_level: int = 0,
    ) -> StockoutPrediction:
        """
        Predict days until stockout given current stock on hand.

        Parameters
        ----------
        current_stock : int
            Total units currently available; use facility_stock.total_quantity.
        horizon_days : int
            How many days ahead to forecast consumption (default 7).
        lead_time_days : int
            Supplier lead time in days; from medicines.lead_time_days.
        expiry_pressure : float
            Fraction of stock batches expiring within 30 days:
            stock_batches.batches_expiring_soon / total_batches.
        disease_weights : dict[str, float], optional
            Date-keyed multipliers from disease_events for the forecast window.
        medicine_name : str
            Human-readable name for reasoning text; from medicines.name.
        reorder_level : int
            Minimum stock threshold triggering a reorder; medicines.reorder_level.

        Returns
        -------
        StockoutPrediction
            Dataclass ready for insertion into ai_predictions.
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        today = pd.Timestamp.today().normalize()
        future_dates = pd.date_range(today, periods=horizon_days, freq="D")
        future_df = pd.DataFrame({"ds": future_dates})

        # ── Stage 1: Prophet baseline forecast ────────────────────────
        if self._prophet is not None:
            prophet_future = self._prophet.predict(future_df)
            yhat_base = prophet_future["yhat"].clip(lower=0).values
        else:
            yhat_base = np.full(horizon_days, self._avg_consumption)

        # ── Stage 2: XGBoost residual correction ──────────────────────
        if self._xgb is not None:
            feat_df = self._build_features(future_df)
            if disease_weights:
                feat_df["disease_weight"] = (
                    future_df["ds"].dt.strftime("%Y-%m-%d").map(disease_weights).fillna(1.0)
                )
            else:
                feat_df["disease_weight"] = 1.0
            feature_cols = ["day_of_week", "month", "is_monsoon", "day_of_year", "disease_weight"]
            residuals = self._xgb.predict(feat_df[feature_cols].values)
            daily_consumption = (yhat_base + residuals).clip(min=0)
        else:
            daily_consumption = yhat_base

        # ── Rolling stock depletion simulation ────────────────────────
        cumulative = np.cumsum(daily_consumption)
        days_until_stockout = horizon_days  # default: no stockout within horizon
        for i, c in enumerate(cumulative):
            if c >= current_stock:
                days_until_stockout = i + 1
                break

        avg_consumption = float(daily_consumption.mean())
        confidence = self._estimate_confidence(daily_consumption)

        reasoning = self._build_reasoning(
            days_until_stockout=days_until_stockout,
            current_stock=current_stock,
            avg_consumption=avg_consumption,
            expiry_pressure=expiry_pressure,
            medicine_name=medicine_name,
            lead_time_days=lead_time_days,
        )
        recommended_action = self._recommend(
            days_until_stockout=days_until_stockout,
            reorder_level=reorder_level,
            current_stock=current_stock,
            lead_time_days=lead_time_days,
        )

        return StockoutPrediction(
            facility_id=self.facility_id,
            medicine_id=self.medicine_id,
            days_until_stockout=days_until_stockout,
            confidence=round(confidence, 3),
            current_stock=current_stock,
            avg_daily_consumption=round(avg_consumption, 2),
            recommended_action=recommended_action,
            reasoning=reasoning,
            predicted_daily_consumption=[round(float(v), 2) for v in daily_consumption],
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _estimate_confidence(self, daily_consumption: np.ndarray) -> float:
        """
        Derives a confidence score from forecast variance.
        High coefficient-of-variation → lower confidence.
        Cold-start (no Prophet) is capped at 0.5.
        """
        if self._prophet is None:
            return 0.5
        cv = float(np.std(daily_consumption) / (np.mean(daily_consumption) + 1e-6))
        return max(0.4, min(0.98, 1.0 - cv * 0.5))

    def _build_reasoning(
        self,
        days_until_stockout: int,
        current_stock: int,
        avg_consumption: float,
        expiry_pressure: float,
        medicine_name: str,
        lead_time_days: int,
    ) -> str:
        """Assembles a human-readable reasoning string for the alert body."""
        parts = [
            f"Current stock: {current_stock} units of {medicine_name}.",
            f"Predicted avg daily consumption: {avg_consumption:.1f} units/day.",
            f"Days until stockout: {days_until_stockout}.",
        ]
        today = pd.Timestamp.today()
        if any(
            self._is_monsoon(today + pd.Timedelta(days=i)) for i in range(7)
        ):
            parts.append("Monsoon season active — consumption elevated (+15–20%).")
        if expiry_pressure > 0.2:
            parts.append(
                f"Expiry pressure: {expiry_pressure:.0%} of stock expires within 30 days."
            )
        if days_until_stockout <= lead_time_days:
            parts.append(
                f"WARNING: Stockout before reorder can arrive (lead time: {lead_time_days} days)."
            )
        return " ".join(parts)

    def _recommend(
        self,
        days_until_stockout: int,
        reorder_level: int,
        current_stock: int,
        lead_time_days: int,
    ) -> str:
        """
        Maps urgency to a recommended action string stored in
        ai_predictions.recommendation and surfaced in alerts.body.
        """
        if days_until_stockout <= lead_time_days:
            return (
                "URGENT_TRANSFER: Initiate redistribution from nearest surplus "
                "facility immediately."
            )
        if days_until_stockout <= lead_time_days + 3:
            return "REORDER_NOW: Place procurement order with district store today."
        if reorder_level > 0 and current_stock < reorder_level * 1.2:
            return (
                "MONITOR: Stock approaching reorder level. "
                "Place order within 48 hours."
            )
        return "OK: Stock level adequate for forecast period."

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """
        Serialise the trained model to disk.

        Suggested path convention:
            models/stockout/{facility_id}/{medicine_id}.pkl
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "prophet": self._prophet,
                    "xgb": self._xgb,
                    "avg_consumption": self._avg_consumption,
                    "is_trained": self._is_trained,
                    "facility_id": self.facility_id,
                    "medicine_id": self.medicine_id,
                    "version": self.MODEL_VERSION,
                },
                f,
            )
        log.info("model saved", extra={"path": path})

    def load(self, path: str) -> None:
        """Load a previously saved model from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._prophet = data["prophet"]
        self._xgb = data["xgb"]
        self._avg_consumption = data["avg_consumption"]
        self._is_trained = data["is_trained"]
        log.info("model loaded", extra={"path": path, "version": data.get("version")})
