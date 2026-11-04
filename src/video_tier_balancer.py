"""SNS video generation tier selector capping L3/L4 fallback ratio (#21)."""
from __future__ import annotations
from typing import Sequence


class VideoTierBalancer:
    def __init__(self, max_low_tier_ratio: float = 0.25) -> None:
        self.max_low_tier_ratio = max_low_tier_ratio
        self.total_dispatched = 0
        self.low_tier_dispatched = 0

    def can_use_fallback_tier(self) -> bool:
        if self.total_dispatched == 0:
            return False
        return (self.low_tier_dispatched / self.total_dispatched) < self.max_low_tier_ratio

    def select_tier(self, available_tiers: Sequence[str]) -> str:
        for high in ("L1", "L2"):
            if high in available_tiers:
                self.total_dispatched += 1
                return high
        if self.can_use_fallback_tier():
            for low in ("L3", "L4"):
                if low in available_tiers:
                    self.total_dispatched += 1
                    self.low_tier_dispatched += 1
                    return low
        return available_tiers[0] if available_tiers else "L1"
