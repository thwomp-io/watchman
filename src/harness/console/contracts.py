"""The JSON contracts the console reads, exported as one JSON Schema for TypeScript generation.

`bus-app/src/generated/contracts.ts` is generated from this schema (`npm run gen:types`), so a
field added here and not in the console's types is a test failure, not a silently dropped key.
Add a contract by naming its model below; tests pin both the schema and the generated file.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from harness.career.campaign import CampaignReport
from harness.career.roles import RolesReport
from harness.finance.models import BarsReport, CorrelationReport, GateItem, GatesBoard
from harness.finance.providers.global_provider import GlobalQuote
from harness.pocket.models import PocketSnapshot
from harness.travel.bank import PlanningCalendar, TripBanks, VisitBank
from harness.travel.schedule import VisitSchedule

CONTRACTS: dict[str, Any] = {
    "GlobalQuote": GlobalQuote,  # one rail row of `hn finance global --json`
    "GateItem": GateItem,
    "GatesBoard": GatesBoard,
    "BarsReport": BarsReport,  # `hn finance bars --json` — the price chart
    "CorrelationReport": CorrelationReport,  # `hn finance correlate --json` — the heatmap
    "VisitBank": VisitBank,  # `hn travel bank --json` — the Visits tab tiles
    "VisitSchedule": VisitSchedule,  # `hn travel schedule --json` — the timeline
    "TripBanks": TripBanks,  # `hn travel banks --json` — the Visits/Trips picker
    "PlanningCalendar": PlanningCalendar,  # `hn travel upcoming --json` — Planning's calendar cards
    "RolesReport": RolesReport,  # `hn career roles --json` — the Openings tab
    "CampaignReport": CampaignReport,  # `hn career campaign --json` — the Campaign tab
    "PocketSnapshot": PocketSnapshot,  # the door's `pocket_state` — the Chat tab
}


def _tighten(node: Any) -> None:
    """Every field the CLI emits is present in its JSON (model_dump / asdict write them all), so
    each property is required; property titles are dropped so the TS inlines `string | null`
    instead of minting aliases (`type Date`, `type Symbol`) that shadow the JS globals."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            node["required"] = sorted(props)
            for prop in props.values():
                if isinstance(prop, dict):
                    prop.pop("title", None)
        for v in node.values():
            _tighten(v)
    elif isinstance(node, list):
        for v in node:
            _tighten(v)


def export_schema() -> dict[str, Any]:
    defs: dict[str, Any] = {}
    for name, model in CONTRACTS.items():
        schema = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}", mode="serialization")
        defs.update(schema.pop("$defs", {}))
        defs[name] = schema
    _tighten(defs)
    return {
        "title": "Contracts",
        "type": "object",
        "properties": {name: {"$ref": f"#/$defs/{name}"} for name in CONTRACTS},
        "$defs": dict(sorted(defs.items())),
    }


def main() -> None:
    print(json.dumps(export_schema(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
