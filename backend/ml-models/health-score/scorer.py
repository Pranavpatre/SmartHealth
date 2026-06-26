"""
Facility Health Score — Module 05
Composite 0–100 score written to the facility_health_scores TimescaleDB hypertable.

Weights:
  25%  Medicine coverage      — avg(min(stock / reorder_level, 1.5)) for top 15 medicines
  15%  Diagnostics coverage   — same for diagnostic kits
  20%  Doctor attendance      — attendance_rate from audit_log
  20%  Footfall capacity ratio — 1 - max(0, (predicted_footfall / bed_capacity - 1) / 2)
  10%  Anomaly penalty        — subtract anomaly_score × 10
  10%  Alert history          — 1 - min(unresolved_alerts_7d / 5, 1)
"""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

WEIGHTS = {
    "medicine_coverage": 0.25,
    "diagnostics_coverage": 0.15,
    "doctor_attendance": 0.20,
    "footfall_capacity_ratio": 0.20,
    "anomaly_penalty": 0.10,
    "alert_history": 0.10,
}


@dataclass
class HealthScoreInput:
    facility_id: str
    # Medicine: list of (current_stock, reorder_level)
    medicine_stocks: list[tuple[int, int]]
    # Diagnostics: list of (current_stock, reorder_level)
    diagnostic_stocks: list[tuple[int, int]]
    # Attendance: fraction 0–1 (days doctors present / days in period)
    doctor_attendance_rate: float
    # Footfall
    predicted_footfall_next_day: float
    bed_capacity: int
    # Anomaly score 0–1 from Module 06
    anomaly_score: float
    # Unresolved alerts in last 7 days
    unresolved_alerts_7d: int


@dataclass
class HealthScoreResult:
    facility_id: str
    score: float          # 0–100
    medicine_coverage: float
    diagnostics_coverage: float
    doctor_attendance: float
    footfall_capacity_ratio: float
    anomaly_penalty: float
    alert_penalty: float
    breakdown: dict
    traffic_light: str    # GREEN / YELLOW / RED


class FacilityHealthScorer:
    """Stateless scorer — call score() with a HealthScoreInput."""

    def score(self, inp: HealthScoreInput) -> HealthScoreResult:
        # Medicine coverage: avg(min(stock/reorder, 1.5)), normalised to 0–1
        med_cov = self._coverage_score(inp.medicine_stocks)
        # Diagnostics coverage
        diag_cov = self._coverage_score(inp.diagnostic_stocks)
        # Doctor attendance already 0–1
        doc_att = max(0.0, min(1.0, inp.doctor_attendance_rate))
        # Footfall capacity: 1 when footfall ≤ capacity, degrades beyond
        fc_ratio = self._footfall_score(inp.predicted_footfall_next_day, inp.bed_capacity)
        # Anomaly: 0 score component when anomaly_score = 1
        anomaly_pen = max(0.0, min(1.0, inp.anomaly_score))
        # Alert history
        alert_pen = min(inp.unresolved_alerts_7d / 5.0, 1.0)

        raw = (
            WEIGHTS["medicine_coverage"] * med_cov
            + WEIGHTS["diagnostics_coverage"] * diag_cov
            + WEIGHTS["doctor_attendance"] * doc_att
            + WEIGHTS["footfall_capacity_ratio"] * fc_ratio
            + WEIGHTS["anomaly_penalty"] * (1.0 - anomaly_pen)
            + WEIGHTS["alert_history"] * (1.0 - alert_pen)
        )

        score = round(raw * 100, 1)
        traffic_light = "GREEN" if score >= 70 else ("YELLOW" if score >= 45 else "RED")

        log.info("scored", facility=inp.facility_id, score=score, traffic_light=traffic_light)

        return HealthScoreResult(
            facility_id=inp.facility_id,
            score=score,
            medicine_coverage=round(med_cov, 3),
            diagnostics_coverage=round(diag_cov, 3),
            doctor_attendance=round(doc_att, 3),
            footfall_capacity_ratio=round(fc_ratio, 3),
            anomaly_penalty=round(anomaly_pen, 3),
            alert_penalty=round(alert_pen, 3),
            traffic_light=traffic_light,
            breakdown={
                "medicine_coverage": {"raw": round(med_cov, 3), "weighted": round(WEIGHTS["medicine_coverage"] * med_cov, 4)},
                "diagnostics_coverage": {"raw": round(diag_cov, 3), "weighted": round(WEIGHTS["diagnostics_coverage"] * diag_cov, 4)},
                "doctor_attendance": {"raw": round(doc_att, 3), "weighted": round(WEIGHTS["doctor_attendance"] * doc_att, 4)},
                "footfall_capacity_ratio": {"raw": round(fc_ratio, 3), "weighted": round(WEIGHTS["footfall_capacity_ratio"] * fc_ratio, 4)},
                "anomaly_penalty": {"raw": round(anomaly_pen, 3), "weighted": round(WEIGHTS["anomaly_penalty"] * (1 - anomaly_pen), 4)},
                "alert_history": {"raw": round(alert_pen, 3), "weighted": round(WEIGHTS["alert_history"] * (1 - alert_pen), 4)},
            },
        )

    @staticmethod
    def _coverage_score(stocks: list[tuple[int, int]]) -> float:
        if not stocks:
            return 0.5  # neutral when no data
        scores = []
        for current, reorder in stocks:
            if reorder == 0:
                scores.append(1.0)
                continue
            scores.append(min(current / reorder, 1.5) / 1.5)
        return float(sum(scores) / len(scores))

    @staticmethod
    def _footfall_score(predicted: float, capacity: int) -> float:
        if capacity <= 0:
            return 0.5
        ratio = predicted / capacity
        if ratio <= 1.0:
            return 1.0
        # Linear degradation: score = 1 - (ratio - 1) / 2, floored at 0
        return max(0.0, 1.0 - (ratio - 1.0) / 2.0)
