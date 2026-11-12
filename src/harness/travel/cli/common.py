"""What every `hn travel` verb module shares: the console and the service constructor."""

from __future__ import annotations

from rich.console import Console

from harness.travel.service import TravelService

console = Console()


def travel_service(provider: str) -> TravelService:
    return TravelService(flight_provider_name=provider)
