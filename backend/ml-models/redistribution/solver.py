"""
Redistribution Optimizer — Module 04
OR-Tools CP-SAT solver matching surplus facilities to deficit ones.
Objective: minimise Σ(transfer_quantity × distance_km).
Constraints:
  - Donor stays ≥ reorder_level after transfer
  - Receiver reaches reorder_level after receiving
  - Transfer quantity is a positive integer
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from ortools.sat.python import cp_model

log = logging.getLogger(__name__)

URGENCY_THRESHOLDS = {
    "CRITICAL": 1,   # days_until_stockout ≤ 1
    "HIGH": 3,
    "MEDIUM": 7,
    "LOW": 14,
}


@dataclass
class FacilityStock:
    facility_id: str
    facility_name: str
    medicine_id: int
    medicine_name: str
    current_stock: int
    reorder_level: int
    days_until_stockout: int
    lat: float
    lng: float
    unit_cost_inr: float = 10.0  # fallback cost per unit


@dataclass
class TransferRecommendation:
    from_facility_id: str
    from_facility_name: str
    to_facility_id: str
    to_facility_name: str
    medicine_id: int
    medicine_name: str
    quantity: int
    distance_km: float
    urgency: str
    estimated_saving_inr: float
    donor_stock_after: int
    receiver_stock_after: int


@dataclass
class RedistributionPlanResult:
    transfers: list[TransferRecommendation] = field(default_factory=list)
    total_units_moved: int = 0
    total_distance_km: float = 0.0
    total_saving_inr: float = 0.0
    facilities_helped: int = 0
    solver_status: str = "UNKNOWN"


class RedistributionSolver:
    """
    Groups facilities by medicine, finds donors (stock > 150% reorder) and
    receivers (stock < 50% reorder), then solves the assignment optimally.
    """

    DONOR_THRESHOLD = 1.5   # stock > 150% reorder → donor
    RECEIVER_THRESHOLD = 0.5  # stock < 50% reorder → receiver

    def __init__(self, max_distance_km: float = 100.0) -> None:
        self.max_distance_km = max_distance_km

    def solve(self, stocks: list[FacilityStock]) -> RedistributionPlanResult:
        """
        Solve redistribution for a list of FacilityStock entries
        (may cover multiple medicines).
        """
        result = RedistributionPlanResult()

        # Group by medicine
        by_medicine: dict[int, list[FacilityStock]] = {}
        for s in stocks:
            by_medicine.setdefault(s.medicine_id, []).append(s)

        for medicine_id, medicine_stocks in by_medicine.items():
            transfers = self._solve_medicine(medicine_stocks)
            result.transfers.extend(transfers)

        result.total_units_moved = sum(t.quantity for t in result.transfers)
        result.total_distance_km = round(sum(t.distance_km * t.quantity for t in result.transfers), 2)
        result.total_saving_inr = round(sum(t.estimated_saving_inr for t in result.transfers), 2)
        result.facilities_helped = len({t.to_facility_id for t in result.transfers})
        result.solver_status = "OPTIMAL" if result.transfers else "NO_ACTION_NEEDED"
        return result

    def _solve_medicine(self, stocks: list[FacilityStock]) -> list[TransferRecommendation]:
        donors = [
            s for s in stocks
            if s.current_stock > s.reorder_level * self.DONOR_THRESHOLD
        ]
        receivers = [
            s for s in stocks
            if s.current_stock < s.reorder_level * self.RECEIVER_THRESHOLD
        ]

        if not donors or not receivers:
            return []

        # Build distance matrix (haversine)
        dist: dict[tuple[int, int], float] = {}
        for i, d in enumerate(donors):
            for j, r in enumerate(receivers):
                dist[(i, j)] = self._haversine(d.lat, d.lng, r.lat, r.lng)

        # Filter by max distance
        valid_pairs = [(i, j) for (i, j), km in dist.items() if km <= self.max_distance_km]
        if not valid_pairs:
            log.warning("no_valid_pairs_within_distance medicine=%s max_km=%s", stocks[0].medicine_name, self.max_distance_km)
            return []

        # CP-SAT model
        model = cp_model.CpModel()
        SCALE = 10  # multiply floats to integers for CP-SAT

        # Decision variables: transfer[i][j] = units moved from donor[i] to receiver[j]
        transfer_vars: dict[tuple[int, int], cp_model.IntVar] = {}
        for i, j in valid_pairs:
            donor = donors[i]
            max_transfer = donor.current_stock - donor.reorder_level
            transfer_vars[(i, j)] = model.new_int_var(0, max(max_transfer, 0), f"t_{i}_{j}")

        # Constraint: donor stock after transfers ≥ reorder_level
        for i, donor in enumerate(donors):
            outbound = [transfer_vars[(i, j)] for j in range(len(receivers)) if (i, j) in transfer_vars]
            if outbound:
                model.add(sum(outbound) <= donor.current_stock - donor.reorder_level)

        # Constraint: receiver must receive at least enough to reach reorder_level;
        # cap at needed + reorder_level to avoid excessive overstocking.
        for j, receiver in enumerate(receivers):
            inbound = [transfer_vars[(i, j)] for i in range(len(donors)) if (i, j) in transfer_vars]
            if inbound:
                needed = max(receiver.reorder_level - receiver.current_stock, 0)
                model.add(sum(inbound) >= needed)
                model.add(sum(inbound) <= needed + receiver.reorder_level)

        # Objective: minimise Σ(quantity × distance_km × SCALE)
        objective_terms = []
        for (i, j), var in transfer_vars.items():
            distance_scaled = int(dist[(i, j)] * SCALE)
            objective_terms.append(var * distance_scaled)

        model.minimize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 10.0
        solver.parameters.num_search_workers = 2
        status = solver.solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            log.warning("solver_infeasible medicine=%s status=%s", stocks[0].medicine_name, solver.status_name(status))
            return []

        transfers: list[TransferRecommendation] = []
        for (i, j), var in transfer_vars.items():
            qty = solver.value(var)
            if qty <= 0:
                continue
            donor, receiver = donors[i], receivers[j]
            km = dist[(i, j)]
            urgency = self._urgency(receiver.days_until_stockout)
            saving = qty * donor.unit_cost_inr * 1.5  # stockout cost is ~1.5× unit cost
            transfers.append(TransferRecommendation(
                from_facility_id=donor.facility_id,
                from_facility_name=donor.facility_name,
                to_facility_id=receiver.facility_id,
                to_facility_name=receiver.facility_name,
                medicine_id=donor.medicine_id,
                medicine_name=donor.medicine_name,
                quantity=qty,
                distance_km=round(km, 2),
                urgency=urgency,
                estimated_saving_inr=round(saving, 2),
                donor_stock_after=donor.current_stock - qty,
                receiver_stock_after=receiver.current_stock + qty,
            ))

        transfers.sort(key=lambda t: (list(URGENCY_THRESHOLDS.keys()).index(t.urgency), t.distance_km))
        return transfers

    @staticmethod
    def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _urgency(days_until_stockout: int) -> str:
        for label, threshold in URGENCY_THRESHOLDS.items():
            if days_until_stockout <= threshold:
                return label
        return "LOW"
