"""
Anomaly Detection — Module 06
pyod IsolationForest on multivariate facility telemetry.
Features: (footfall, stock_consumption_delta, diagnostic_kit_usage, doctor_count).
Per-facility rolling 30-day baseline. Score compared to baseline distribution.
CRITICAL anomalies (score > 0.85) trigger immediate alert regardless of stockout thresholds.
"""

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from pyod.models.iforest import IForest

log = logging.getLogger(__name__)

SEVERITY_THRESHOLDS = {
    "CRITICAL": 0.85,
    "HIGH": 0.70,
    "MEDIUM": 0.50,
    "LOW": 0.30,
}


@dataclass
class AnomalyResult:
    facility_id: str
    is_anomaly: bool
    anomaly_score: float        # 0–1, higher = more anomalous
    severity: str               # LOW / MEDIUM / HIGH / CRITICAL
    contributing_features: list[str]
    reasoning: str


class AnomalyDetector:
    """
    Trains an IsolationForest per facility on 30-day rolling multivariate telemetry.
    """

    MODEL_VERSION = "1.0"
    FEATURE_COLS = ["footfall", "stock_consumption_delta", "diagnostic_kit_usage", "doctor_count"]

    def __init__(self, facility_id: str, contamination: float = 0.05) -> None:
        self.facility_id = facility_id
        self.contamination = contamination
        self._model: Optional[IForest] = None
        self._feature_means: dict[str, float] = {}
        self._feature_stds: dict[str, float] = {}
        self._is_trained = False

    def train(self, history: pd.DataFrame) -> None:
        """
        Args:
            history: DataFrame with columns:
                date, footfall, stock_consumption_delta, diagnostic_kit_usage, doctor_count
        """
        if history.empty or len(history) < 10:
            log.warning("anomaly_insufficient_data facility=%s rows=%d", self.facility_id, len(history))
            self._is_trained = False
            return

        df = history[self.FEATURE_COLS].copy().fillna(0)

        self._feature_means = df.mean().to_dict()
        self._feature_stds = df.std().to_dict()

        X = df.values

        self._model = IForest(
            contamination=self.contamination,
            n_estimators=100,
            random_state=42,
            behaviour="new",
        )
        self._model.fit(X)
        self._is_trained = True
        log.info("anomaly_trained", facility=self.facility_id, rows=len(df))

    def score(
        self,
        footfall: float,
        stock_consumption_delta: float,
        diagnostic_kit_usage: float,
        doctor_count: float,
    ) -> AnomalyResult:
        """
        Score a single observation.
        """
        if not self._is_trained or self._model is None:
            return AnomalyResult(
                facility_id=self.facility_id,
                is_anomaly=False,
                anomaly_score=0.0,
                severity="LOW",
                contributing_features=[],
                reasoning="Anomaly model not yet trained — insufficient history.",
            )

        X = np.array([[footfall, stock_consumption_delta, diagnostic_kit_usage, doctor_count]])

        raw_score = self._model.decision_function(X)[0]
        # IForest decision_function: lower = more anomalous; normalise to 0–1
        normalised = float(1.0 / (1.0 + np.exp(raw_score)))

        is_anomaly = bool(normalised > SEVERITY_THRESHOLDS["LOW"])
        severity = self._classify_severity(normalised)
        contributing = self._contributing_features(footfall, stock_consumption_delta, diagnostic_kit_usage, doctor_count)
        reasoning = self._build_reasoning(normalised, severity, contributing, footfall, doctor_count)

        return AnomalyResult(
            facility_id=self.facility_id,
            is_anomaly=is_anomaly,
            anomaly_score=round(normalised, 4),
            severity=severity,
            contributing_features=contributing,
            reasoning=reasoning,
        )

    def _classify_severity(self, score: float) -> str:
        for label, threshold in SEVERITY_THRESHOLDS.items():
            if score >= threshold:
                return label
        return "NORMAL"

    def _contributing_features(
        self,
        footfall: float,
        stock_consumption_delta: float,
        diagnostic_kit_usage: float,
        doctor_count: float,
    ) -> list[str]:
        """Z-score each feature against its training distribution, return top outliers."""
        values = {
            "footfall": footfall,
            "stock_consumption_delta": stock_consumption_delta,
            "diagnostic_kit_usage": diagnostic_kit_usage,
            "doctor_count": doctor_count,
        }
        z_scores: dict[str, float] = {}
        for feat, val in values.items():
            mean = self._feature_means.get(feat, 0.0)
            std = self._feature_stds.get(feat, 1.0)
            z_scores[feat] = abs((val - mean) / (std + 1e-6))

        return [feat for feat, z in sorted(z_scores.items(), key=lambda x: -x[1]) if z > 2.0]

    def _build_reasoning(
        self,
        score: float,
        severity: str,
        contributing: list[str],
        footfall: float,
        doctor_count: float,
    ) -> str:
        if severity == "NORMAL":
            return "All metrics within expected range."
        parts = [f"Anomaly detected (score: {score:.2f}, severity: {severity})."]
        if contributing:
            parts.append(f"Key signals: {', '.join(contributing)}.")
        if "footfall" in contributing:
            mean_ff = self._feature_means.get("footfall", 0)
            parts.append(f"Footfall {footfall:.0f} vs baseline {mean_ff:.0f} — possible outbreak or mass casualty.")
        if "doctor_count" in contributing and doctor_count == 0:
            parts.append("Zero doctors logged — possible mass absence or attendance data missing.")
        return " ".join(parts)

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "model": self._model,
                "feature_means": self._feature_means,
                "feature_stds": self._feature_stds,
                "is_trained": self._is_trained,
                "version": self.MODEL_VERSION,
            }, f)

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._model = data["model"]
        self._feature_means = data["feature_means"]
        self._feature_stds = data["feature_stds"]
        self._is_trained = data["is_trained"]
