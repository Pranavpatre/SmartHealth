"""
Footfall Forecast — Module 03
LightGBM regressor predicting next-7-day daily OPD patient count.
Features: day_of_week, month, is_monsoon, disease_calendar_weight,
          festival_flag, facility_bed_capacity, historical_7d_avg,
          historical_30d_avg.

Schema context (001_core.sql):
  facilities(id, bed_capacity, facility_type)
  daily_snapshots(time, facility_id, opd_count, ipd_count, emergency_count,
                  beds_occupied, doctors_present)
  disease_events(district_id, disease_name, start_date, end_date, severity)
  ai_predictions(facility_id, prediction_type='FOOTFALL', predicted_value,
                 confidence, reasoning, recommendation, model_version)
"""

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Indian public holidays / major festival dates that consistently reduce
# OPD attendance.  Format: "MM-DD" (year-agnostic).
# ---------------------------------------------------------------------------
FESTIVAL_DATES: set[str] = {
    "01-14",  # Makar Sankranti
    "01-26",  # Republic Day
    "03-25",  # Holi (approximate; varies by year)
    "03-26",  # Holi 2nd day
    "08-15",  # Independence Day
    "10-02",  # Gandhi Jayanti / Dussehra window
    "10-24",  # Diwali window
    "11-01",  # Diwali 2nd day window
    "11-12",  # Diwali / Bhai Dooj window
}


@dataclass
class FootfallForecast:
    """Result returned by FootfallForecaster.predict().

    Intended to be persisted to ai_predictions with:
        prediction_type = 'FOOTFALL'
        predicted_value = avg_predicted
        recommendation  = staffing_recommendation
        reasoning       = JSON-encoded dict built from the reasoning string
        model_version   = FootfallForecaster.MODEL_VERSION
    """

    facility_id: str
    predicted_daily: list[int]     # next N days
    lower_bound: list[int]         # 85th-percentile lower
    upper_bound: list[int]         # 115th-percentile upper
    avg_predicted: float
    staffing_recommendation: str
    reasoning: str


class FootfallForecaster:
    """
    Forecasts daily OPD footfall for a given facility using LightGBM.

    Workflow
    --------
    1. Instantiate with facility_id and bed_capacity (from facilities table).
    2. Call train() with historical daily footfall data.
    3. Call predict() with optional disease_weights for the forecast window.
    4. Persist / reload with save() / load().

    Cold-start handling
    -------------------
    If < 7 rows of history are available, the model uses the historical mean
    as a flat forecast. Confidence bands are widened to ±25 % in this case.
    """

    MODEL_VERSION = "1.0"

    # Feature columns consumed by LightGBM — order is fixed.
    FEATURE_COLS: list[str] = [
        "day_of_week",
        "month",
        "is_monsoon",
        "is_festival",
        "day_of_year",
        "bed_capacity",
        "disease_weight",
        "hist_7d_avg",
        "hist_30d_avg",
    ]

    def __init__(self, facility_id: str, bed_capacity: int = 10) -> None:
        self.facility_id = facility_id
        self.bed_capacity = bed_capacity
        self._model: Optional[LGBMRegressor] = None
        self._is_trained: bool = False
        self._avg_footfall: float = 0.0
        self._cold_start: bool = False   # True when < 7 rows of history

    # ── Feature engineering ───────────────────────────────────────────────

    @staticmethod
    def _is_monsoon(month: int) -> int:
        """Returns 1 if month falls in the Indian monsoon window (Jun–Sep)."""
        return int(month in (6, 7, 8, 9))

    @staticmethod
    def _is_festival(dt: pd.Timestamp) -> int:
        """Returns 1 if the date is a major Indian public holiday / festival."""
        return int(dt.strftime("%m-%d") in FESTIVAL_DATES)

    def _build_features(
        self,
        df: pd.DataFrame,
        disease_weights: Optional[dict[str, float]] = None,
    ) -> pd.DataFrame:
        """
        Adds all model features to a DataFrame with a 'ds' datetime column.
        When 'y' (historical footfall) is present, rolling averages are
        computed from actuals; otherwise self._avg_footfall is used as a
        stand-in for future rows.
        """
        df = df.copy()
        df["day_of_week"] = df["ds"].dt.dayofweek    # 0 = Monday
        df["month"] = df["ds"].dt.month
        df["is_monsoon"] = df["month"].apply(self._is_monsoon)
        df["is_festival"] = df["ds"].apply(self._is_festival)
        df["day_of_year"] = df["ds"].dt.dayofyear
        df["bed_capacity"] = self.bed_capacity

        if disease_weights:
            df["disease_weight"] = (
                df["ds"].dt.strftime("%Y-%m-%d").map(disease_weights).fillna(1.0)
            )
        else:
            df["disease_weight"] = 1.0

        # Rolling averages from actuals where available; fallback to global mean
        if "y" in df.columns:
            df["hist_7d_avg"] = df["y"].rolling(7, min_periods=1).mean()
            df["hist_30d_avg"] = df["y"].rolling(30, min_periods=1).mean()
        else:
            df["hist_7d_avg"] = self._avg_footfall
            df["hist_30d_avg"] = self._avg_footfall

        return df

    # ── Training ──────────────────────────────────────────────────────────

    def train(
        self,
        history: pd.DataFrame,
        disease_weights: Optional[dict[str, float]] = None,
    ) -> float:
        """
        Fit LightGBM on historical daily OPD footfall.

        Parameters
        ----------
        history : pd.DataFrame
            Columns: ``date`` (date or str) and ``footfall`` (int).
            Source: daily_snapshots.opd_count aggregated to calendar day.
        disease_weights : dict[str, float], optional
            Keys are date strings "YYYY-MM-DD", values are multiplicative
            demand multipliers from disease_events (e.g. 1.4 during cholera).

        Returns
        -------
        float
            MAE on last-14-day holdout split. Returns 0.0 for cold-start.
        """
        if history.empty or len(history) < 7:
            self._avg_footfall = (
                float(history["footfall"].mean())
                if not history.empty
                else 50.0
            )
            self._is_trained = True
            self._cold_start = True
            log.warning(
                "cold_start — insufficient footfall history (%d rows)",
                len(history),
                extra={"facility": self.facility_id},
            )
            return 0.0

        df = history.rename(columns={"date": "ds", "footfall": "y"}).copy()
        df["ds"] = pd.to_datetime(df["ds"])
        df = df.sort_values("ds").reset_index(drop=True)

        self._avg_footfall = float(df["y"].mean())
        self._cold_start = False

        df = self._build_features(df, disease_weights)

        X = df[self.FEATURE_COLS].values
        y = df["y"].values

        # Validation split: hold out last 14 days
        split = max(len(X) - 14, int(len(X) * 0.85))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        self._model = LGBMRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=5,
            num_leaves=31,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbose=-1,
        )
        self._model.fit(X_train, y_train)

        mae: float = 0.0
        if len(X_val) > 0:
            mae = float(mean_absolute_error(y_val, self._model.predict(X_val)))

        self._is_trained = True
        log.info(
            "footfall_trained",
            extra={"facility": self.facility_id, "rows": len(df), "mae": round(mae, 2)},
        )
        return mae

    # ── Prediction ────────────────────────────────────────────────────────

    def predict(
        self,
        horizon_days: int = 7,
        disease_weights: Optional[dict[str, float]] = None,
    ) -> FootfallForecast:
        """
        Forecast OPD footfall for the next horizon_days days.

        Parameters
        ----------
        horizon_days : int
            Number of days to forecast (default 7).
        disease_weights : dict[str, float], optional
            Date-keyed multipliers for the forecast window from disease_events.

        Returns
        -------
        FootfallForecast
            Dataclass with daily predictions, confidence bands, staffing
            recommendation, and reasoning string.
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() first.")

        today = pd.Timestamp.today().normalize()
        future_dates = pd.date_range(today, periods=horizon_days, freq="D")
        future_df = pd.DataFrame({"ds": future_dates})
        future_df = self._build_features(future_df, disease_weights)

        if self._model is not None:
            raw_preds = self._model.predict(future_df[self.FEATURE_COLS].values).clip(min=0)
        else:
            # Cold-start: flat forecast from historical mean
            raw_preds = np.full(horizon_days, self._avg_footfall)

        # Uncertainty bands: ±15 % (±25 % for cold-start)
        band = 0.25 if self._cold_start else 0.15
        lower = (raw_preds * (1.0 - band)).clip(min=0).astype(int).tolist()
        upper = (raw_preds * (1.0 + band)).astype(int).tolist()
        daily = [int(p) for p in raw_preds]
        avg = float(np.mean(raw_preds))

        peak = max(daily)
        peak_day = daily.index(peak) + 1
        staffing = self._staffing_rec(peak)

        monsoon_active = any(d.month in (6, 7, 8, 9) for d in future_dates)
        reasoning_parts = [
            f"Avg predicted footfall: {avg:.0f} patients/day over next {horizon_days} days.",
            f"Peak: {peak} patients on day {peak_day}.",
            f"Facility bed capacity: {self.bed_capacity}.",
        ]
        if monsoon_active:
            reasoning_parts.append(
                "Monsoon season active — elevated respiratory and diarrhoeal "
                "cases expected; demand may run 15–25 % above baseline."
            )
        festival_days = [
            future_dates[i].strftime("%b %d")
            for i, d in enumerate(future_dates)
            if self._is_festival(d)
        ]
        if festival_days:
            reasoning_parts.append(
                f"Festival/holiday dates in window ({', '.join(festival_days)}) "
                "may reduce OPD attendance."
            )

        return FootfallForecast(
            facility_id=self.facility_id,
            predicted_daily=daily,
            lower_bound=lower,
            upper_bound=upper,
            avg_predicted=round(avg, 1),
            staffing_recommendation=staffing,
            reasoning=" ".join(reasoning_parts),
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _staffing_rec(self, peak: int) -> str:
        """
        Translates peak daily footfall into a staffing recommendation.
        Ratio is peak patients divided by bed_capacity as a proxy for
        facility throughput pressure.
        """
        ratio = peak / max(self.bed_capacity, 1)
        if ratio > 3:
            return (
                "CRITICAL: Expected peak footfall exceeds 3× bed capacity. "
                "Deploy additional doctor and nurse immediately."
            )
        if ratio > 2:
            return (
                "HIGH: Expected peak footfall exceeds 2× bed capacity. "
                "Consider additional OPD session or extended hours."
            )
        if ratio > 1.5:
            return (
                "ELEVATED: Footfall elevated above 1.5× bed capacity. "
                "Ensure full staffing complement is present."
            )
        return "NORMAL: Predicted footfall within normal capacity range."

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """
        Serialise the trained model to disk.

        Suggested path convention:
            models/footfall/{facility_id}.pkl
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "model": self._model,
                    "avg_footfall": self._avg_footfall,
                    "is_trained": self._is_trained,
                    "cold_start": self._cold_start,
                    "bed_capacity": self.bed_capacity,
                    "facility_id": self.facility_id,
                    "version": self.MODEL_VERSION,
                },
                f,
            )
        log.info("model saved", extra={"path": path})

    def load(self, path: str) -> None:
        """Load a previously saved model from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._model = data["model"]
        self._avg_footfall = data["avg_footfall"]
        self._is_trained = data["is_trained"]
        self._cold_start = data.get("cold_start", False)
        self.bed_capacity = data.get("bed_capacity", self.bed_capacity)
        log.info("model loaded", extra={"path": path, "version": data.get("version")})
