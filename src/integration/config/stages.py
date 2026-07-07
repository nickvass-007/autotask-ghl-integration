"""Stage-mapping engine — the Flow-2 translation layer (Spec §10.3).

Autotask stages/statuses are picklists with IDs; GHL stages are free-form per
location. NOTHING here is hardcoded: the YAML (``config/stage_mapping.yaml``)
declares two SEPARATE maps —

- ``sales_pipeline``   : GHL pipeline+stages  ⇄ Autotask **Opportunity** stages
- ``service_pipeline`` : GHL pipeline+stages  ←  Autotask **Ticket** statuses
                          (read-only mirror, AT→GHL, for the whole of v1)

⚠️ One GHL pipeline must never write to BOTH Autotask entities — that is the
primary duplication risk (Spec §10.1). The loader enforces distinct pipeline ids.

``validate_stage_map`` fetches both sides' live picklists/pipelines and confirms
every mapping entry resolves to a real ID (Spec §10.3). An unmapped stage at
runtime routes to approval as "unmapped stage" — the engine NEVER guesses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from ..core.logging import get_logger
from ..db.base import utcnow
from ..db.enums import Direction, Environment
from ..db.models import StageMapping
from .mapping import load_stage_mapping

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StageEntry:
    ghl_stage_id: str
    autotask_status_value: str
    direction: Direction
    active: bool = True


@dataclass(frozen=True, slots=True)
class PipelineMap:
    ghl_pipeline_id: str
    autotask_entity: str                    # "opportunity" | "ticket"
    stages: tuple[StageEntry, ...]
    closed_won_stage_id: str | None = None  # sales only — the Stage-C handoff signal


@dataclass(frozen=True, slots=True)
class StageMap:
    sales: PipelineMap
    service: PipelineMap

    def pipeline_for(self, ghl_pipeline_id: str) -> PipelineMap | None:
        if ghl_pipeline_id == self.sales.ghl_pipeline_id:
            return self.sales
        if ghl_pipeline_id == self.service.ghl_pipeline_id:
            return self.service
        return None

    def autotask_value_for(self, ghl_pipeline_id: str, ghl_stage_id: str) -> str | None:
        pipe = self.pipeline_for(ghl_pipeline_id)
        if pipe is None:
            return None
        entry = next(
            (s for s in pipe.stages if s.ghl_stage_id == ghl_stage_id and s.active), None
        )
        return entry.autotask_status_value if entry else None

    def ghl_stage_for(self, autotask_entity: str, autotask_value: str) -> tuple[str, str] | None:
        """(pipeline_id, stage_id) for an Autotask stage/status value, or None."""
        pipe = self.sales if autotask_entity == "opportunity" else self.service
        entry = next(
            (s for s in pipe.stages if s.autotask_status_value == str(autotask_value) and s.active),
            None,
        )
        return (pipe.ghl_pipeline_id, entry.ghl_stage_id) if entry else None

    def is_closed_won(self, ghl_pipeline_id: str, ghl_stage_id: str) -> bool:
        return (
            ghl_pipeline_id == self.sales.ghl_pipeline_id
            and self.sales.closed_won_stage_id is not None
            and ghl_stage_id == self.sales.closed_won_stage_id
        )


def _pipeline(raw: dict, *, key: str) -> PipelineMap:
    stages = tuple(
        StageEntry(
            ghl_stage_id=str(s["ghl_stage_id"]),
            autotask_status_value=str(s["autotask_status_value"]),
            direction=Direction(s["direction"]),
            active=bool(s.get("active", True)),
        )
        for s in raw.get("stages", [])
    )
    return PipelineMap(
        ghl_pipeline_id=str(raw["ghl_pipeline_id"]),
        autotask_entity=str(raw["autotask_entity"]),
        stages=stages,
        closed_won_stage_id=(
            str(raw["closed_won_stage_id"]) if raw.get("closed_won_stage_id") else None
        ),
    )


@lru_cache
def load_stage_map() -> StageMap:
    raw = load_stage_mapping()
    sales = _pipeline(raw["sales_pipeline"], key="sales_pipeline")
    service = _pipeline(raw["service_pipeline"], key="service_pipeline")
    if sales.autotask_entity != "opportunity" or service.autotask_entity != "ticket":
        raise ValueError(
            "stage_mapping.yaml: sales_pipeline must target 'opportunity' and "
            "service_pipeline must target 'ticket' (Spec §10.1)."
        )
    if sales.ghl_pipeline_id == service.ghl_pipeline_id:
        raise ValueError(
            "stage_mapping.yaml: sales and service pipelines must be DIFFERENT GHL "
            "pipelines — one pipeline writing to both Autotask entities is the "
            "primary duplication risk (Spec §10.1)."
        )
    # Service pipeline is a read-only AT->GHL mirror for the whole of v1 (Spec §10.6).
    for s in service.stages:
        if s.direction is not Direction.AUTOTASK_TO_GHL:
            raise ValueError(
                "stage_mapping.yaml: service_pipeline stages must all be "
                "direction=autotask_to_ghl (Tickets are one-way in v1, Spec §10.2)."
            )
    return StageMap(sales=sales, service=service)


def sync_stage_map_to_db(session, environment: Environment) -> int:
    """Upsert the YAML mapping into the ``stage_mapping`` table so ops surfaces
    (admin UI / Teams bot) can inspect what's live. Returns row count."""
    smap = load_stage_map()
    count = 0
    for pipe in (smap.sales, smap.service):
        for entry in pipe.stages:
            existing = (
                session.query(StageMapping)
                .filter_by(
                    environment=environment,
                    ghl_pipeline_id=pipe.ghl_pipeline_id,
                    ghl_stage_id=entry.ghl_stage_id,
                    direction=entry.direction,
                )
                .one_or_none()
            )
            if existing is None:
                existing = StageMapping(
                    environment=environment,
                    ghl_pipeline_id=pipe.ghl_pipeline_id,
                    ghl_stage_id=entry.ghl_stage_id,
                    direction=entry.direction,
                    autotask_entity=pipe.autotask_entity,
                    autotask_status_value=entry.autotask_status_value,
                )
                session.add(existing)
            existing.autotask_entity = pipe.autotask_entity
            existing.autotask_status_value = entry.autotask_status_value
            existing.active = entry.active
            count += 1
    session.flush()
    return count


async def validate_stage_map(*, autotask, ghl) -> list[str]:
    """Confirm every mapping entry resolves to a real ID on each side (Spec §10.3).

    Returns a list of human-readable problems (empty = fully valid). Callers
    surface problems in the admin UI / Teams bot; entries that no longer resolve
    route to approval at runtime rather than being guessed."""
    smap = load_stage_map()
    problems: list[str] = []

    # GHL side: pipelines + stages must exist in the location.
    pipelines = {p["id"]: p for p in await ghl.get_pipelines()}
    for label, pipe in (("sales", smap.sales), ("service", smap.service)):
        gp = pipelines.get(pipe.ghl_pipeline_id)
        if gp is None:
            problems.append(f"{label}: GHL pipeline {pipe.ghl_pipeline_id!r} not found")
            continue
        stage_ids = {str(s["id"]) for s in gp.get("stages", [])}
        for entry in pipe.stages:
            if entry.ghl_stage_id not in stage_ids:
                problems.append(
                    f"{label}: GHL stage {entry.ghl_stage_id!r} not in pipeline "
                    f"{gp.get('name', pipe.ghl_pipeline_id)!r}"
                )
        if pipe.closed_won_stage_id and pipe.closed_won_stage_id not in stage_ids:
            problems.append(
                f"{label}: closed_won_stage_id {pipe.closed_won_stage_id!r} not in pipeline"
            )

    # Autotask side: stage/status values must be real picklist values.
    opp_values = set(await autotask.get_picklist_values("Opportunities", "stage"))
    ticket_values = set(await autotask.get_picklist_values("Tickets", "status"))
    for entry in smap.sales.stages:
        if entry.autotask_status_value not in opp_values:
            problems.append(
                f"sales: Autotask Opportunity stage {entry.autotask_status_value!r} "
                "is not a real picklist value"
            )
    for entry in smap.service.stages:
        if entry.autotask_status_value not in ticket_values:
            problems.append(
                f"service: Autotask Ticket status {entry.autotask_status_value!r} "
                "is not a real picklist value"
            )

    if problems:
        log.warning("Stage map validation: %d problem(s): %s", len(problems), problems)
    return problems
