"""Typed loader for config/weights.yaml (the machine-readable half of the hybrid config)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from harness.travel.config.settings import get_settings


class FlightWeights(BaseModel):
    # The user's LOCAL/convenience airport (a small field near home) vs a major HUB used for price
    # comparison. The local airport gets a heavy convenience bonus + leads the search; the hub is the
    # deep-market price/frequency reference. Both config-driven — leave `home_airport` empty to query
    # only the hub. `home_airport_note` is the tagline shown next to it (e.g. "the close-in option").
    home_airport: str = ""  # convenience/local IATA (leads + earns the bonus); "" → hub-only search
    comparison_airport: str = ""  # the hub IATA for the deep-market price comparison
    home_airport_note: str = ""  # convenience tagline for the local airport (e.g. "the close-in option")
    home_airport_bonus: float = 25.0  # convenience thumb-on-the-scale for local-airport offers (NOT a filter)
    home_airport_served_iata: list[str] = Field(default_factory=list)  # dest IATAs the local airport serves
    hour_soft_cap: float = 4.0
    over_cap_penalty_per_hour: float = 8.0
    connection_penalty: dict[int, float] = Field(default_factory=dict)
    airline_avoid_iata: list[str] = Field(default_factory=list)
    price_component_max: float = 20.0

    def connection_penalty_for(self, stops: int) -> float:
        if not self.connection_penalty:
            return 0.0
        capped = min(stops, max(self.connection_penalty))
        return self.connection_penalty.get(capped, 0.0)

    def query_origins(self, home_served: bool) -> list[str]:
        """Origins to query for a destination: the hub always; the local/home airport too when it
        serves that destination (and one is configured). Config-driven — no hardcoded airport codes."""
        origins = [self.comparison_airport] if self.comparison_airport else []
        if home_served and self.home_airport:
            origins = [self.home_airport, *origins]
        return origins


class ScreenAxisWeights(BaseModel):
    in_screen_penalty: float = 15.0
    hard_no_advisory_level: int | None = None


class ScreenWeights(BaseModel):
    geological: ScreenAxisWeights = Field(default_factory=ScreenAxisWeights)
    social_crime: ScreenAxisWeights = Field(default_factory=ScreenAxisWeights)


class LodgingWeights(BaseModel):
    # "best-in-class for the area" — a relative preference, NOT a hard star floor.
    prefer_best_available_in_area: bool = True
    top_tier_available_bonus: float = 6.0
    multiroom_suite_soft_ceiling: bool = True


class FollowedTeam(BaseModel):
    """A sports team the user follows (centerpiece-tier). Drives the static reference-almanac parser:
    a schedule section whose heading contains `section_match` is parsed as this team's games. Fully
    config-driven so the parser carries no hardcoded team names / cities. `home_venue` labels home
    games; `home_only` marks a schedule that lists home games only (no H/A column → all treated home)."""

    name: str  # display name used in the surfaced event ("{name} vs {opponent}")
    section_match: str  # case-insensitive substring identifying this team's schedule section
    home_venue: str = ""  # city/venue label for home games (e.g. "Capital City, ST")
    home_only: bool = False  # the schedule lists HOME games only (no H/A column)
    league: str = "NFL"  # subgenre tag (drives centerpiece tiering)
    sport: str = "Football"  # genre tag


class EventWeights(BaseModel):
    """Sports/event-interest tiering. The `centerpiece_subgenres` (default NBA + NFL) are the leagues
    a game can justify building a trip around; every other league/event (MLB, soccer, F1, concerts,
    theatre) is perk-tier: surfaced + a mild positive signal on an otherwise-good destination, NEVER
    the anchor. Configurable — set it to the leagues the user actually plans trips around. Matched
    against a Ticketmaster classification subGenre (case-insensitive). `followed_teams` config-drives
    the reference-almanac schedule parser (the teams whose static schedules get surfaced proactively)."""

    centerpiece_subgenres: list[str] = Field(default_factory=lambda: ["NBA", "NFL"])
    followed_teams: list[FollowedTeam] = Field(default_factory=list)

    def tier_for(self, subgenre: str) -> Literal["centerpiece", "perk"]:
        tags = {s.strip().upper() for s in self.centerpiece_subgenres}
        return "centerpiece" if subgenre.strip().upper() in tags else "perk"


class DestinationScreen(BaseModel):
    geological: str = "clean"
    social_crime: str = "clean"
    calibration_notes: list[str] = Field(default_factory=list)


class ConditionsThresholds(BaseModel):
    """The Travel Watchman conditions-watch alert bar. All tunable; quiet days stay quiet."""

    heat_high_f: float = 82.0  # forecast high >= this -> heat flag (a personal comfort threshold)
    aqi: int = 101  # US AQI >= this -> smoke flag (Unhealthy-for-Sensitive; wildfire season)
    wet_day_hours: float = 6.0  # precip_hours >= this -> wet_day (duration, "most of the day")
    wet_day_sum_in: float = 0.3  # OR precip_sum >= this (inches) -> wet_day (a soaking)
    # snow: ANY snowfall_sum > 0 flags (any snowfall at the home locale is notable) — no threshold field.


class ConditionsWeights(BaseModel):
    """Travel Watchman conditions-watch config. `home` is the standing primary scope;
    finalized trips arm their destination separately."""

    home: str = ""  # the user's home locale, geocoded by the weather sense (config-required; "" → off)
    horizon_days: int = 3  # today + N: flag a near-window crossing (heads-up on what's coming)
    # a trip GRADUATES to watched when its `trip.status` is in this set ("finalized" + the existing
    # booked/active) — then its destination is armed once within `arm_days` of the start.
    arm_statuses: list[str] = Field(default_factory=lambda: ["finalized", "booked", "active"])
    arm_days: int = 14  # arm a finalized trip's destination this many days before its start
    thresholds: ConditionsThresholds = Field(default_factory=ConditionsThresholds)


class WeightConfig(BaseModel):
    synced_from: str = "preferences.md"
    synced_date: str | None = None
    flight: FlightWeights = Field(default_factory=FlightWeights)
    screen: ScreenWeights = Field(default_factory=ScreenWeights)
    lodging: LodgingWeights = Field(default_factory=LodgingWeights)
    events: EventWeights = Field(default_factory=EventWeights)
    conditions: ConditionsWeights = Field(default_factory=ConditionsWeights)
    destination_airports: dict[str, str] = Field(default_factory=dict)
    destination_cities: dict[str, str] = Field(default_factory=dict)
    destination_screens: dict[str, DestinationScreen] = Field(default_factory=dict)


def load_weights(path: Path | None = None) -> WeightConfig:
    # Default to the active weight pack's travel weights (else the packaged default) via the lane
    # settings, so a loaded pack drives ranking too — pack-aware by construction. An explicit path
    # still wins (the no-pack default of `weights_path` is the packaged file, so callers passing
    # nothing are unchanged when no pack is loaded).
    path = path or get_settings().weights_path
    data = yaml.safe_load(path.read_text())
    return WeightConfig.model_validate(data)
