"""Rollout designer: plan measurement before go-live, when it is cheapest and strongest."""

from probative.rollout.power import (
    NoiseModel,
    PowerResult,
    estimate_noise,
    noise_from_units,
    power_table,
    simulate_power,
)
from probative.rollout.prereg import RegistrationStatus, check_registration, lock_registration, registration_content
from probative.rollout.waves import plan_waves

__all__ = [
    "NoiseModel",
    "PowerResult",
    "RegistrationStatus",
    "check_registration",
    "estimate_noise",
    "lock_registration",
    "noise_from_units",
    "plan_waves",
    "power_table",
    "registration_content",
    "simulate_power",
]
