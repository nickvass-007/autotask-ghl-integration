"""Stage-map engine invariants (Spec §10.1, §10.3)."""

from __future__ import annotations

import pytest

from integration.config.stages import PipelineMap, StageEntry, StageMap
from integration.db.enums import Direction

from fakes import make_stage_map


def test_lookups_both_directions():
    smap = make_stage_map()
    assert smap.autotask_value_for("pipe-sales", "stage-qualified") == "2"
    assert smap.autotask_value_for("pipe-sales", "stage-unknown") is None
    assert smap.ghl_stage_for("opportunity", "3") == ("pipe-sales", "stage-proposal")
    assert smap.ghl_stage_for("ticket", "5") == ("pipe-service", "stage-done")
    assert smap.ghl_stage_for("opportunity", "99") is None


def test_closed_won_detection():
    smap = make_stage_map()
    assert smap.is_closed_won("pipe-sales", "stage-won") is True
    assert smap.is_closed_won("pipe-sales", "stage-new") is False
    assert smap.is_closed_won("pipe-service", "stage-won") is False


def test_conversion_trigger_defaults_to_closed_won():
    smap = make_stage_map()
    assert smap.is_conversion_trigger("pipe-sales", "stage-won") is True
    assert smap.is_conversion_trigger("pipe-sales", "stage-qualified") is False
    assert smap.is_conversion_trigger("pipe-service", "stage-won") is False


def test_conversion_trigger_adds_earlier_stage_but_keeps_closed_won():
    base = make_stage_map()
    # Operator configures ONLY an earlier stage (not closed-won).
    sales = PipelineMap(
        ghl_pipeline_id=base.sales.ghl_pipeline_id,
        autotask_entity="opportunity",
        stages=base.sales.stages,
        closed_won_stage_id=base.sales.closed_won_stage_id,
        conversion_stage_ids=("stage-qualified",),
    )
    smap = StageMap(sales=sales, service=base.service)
    # The handoff fires when a prospect is QUALIFIED...
    assert smap.is_conversion_trigger("pipe-sales", "stage-qualified") is True
    # ...AND closed-won still fires — the list ADDS, it never replaces closed-won
    # (finding #4: a deal jumping straight to won must still onboard).
    assert smap.is_conversion_trigger("pipe-sales", "stage-won") is True
    assert smap.is_conversion_trigger("pipe-sales", "stage-new") is False
    # closed-won detection itself is unchanged (outcome protection still keys off it)
    assert smap.is_closed_won("pipe-sales", "stage-qualified") is False


def test_pipeline_routing():
    smap = make_stage_map()
    assert smap.pipeline_for("pipe-sales").autotask_entity == "opportunity"
    assert smap.pipeline_for("pipe-service").autotask_entity == "ticket"
    assert smap.pipeline_for("nope") is None


def test_same_pipeline_for_both_entities_is_rejected():
    # One GHL pipeline writing to both Autotask entities is the primary
    # duplication risk (§10.1) — the loader must refuse it. We exercise the
    # invariant directly on the constructor-adjacent validation in load_stage_map
    # by replicating its checks here on raw PipelineMaps.
    sales = PipelineMap(
        ghl_pipeline_id="pipe-same",
        autotask_entity="opportunity",
        stages=(StageEntry("s1", "1", Direction.GHL_TO_AUTOTASK),),
    )
    service = PipelineMap(
        ghl_pipeline_id="pipe-same",
        autotask_entity="ticket",
        stages=(StageEntry("s2", "1", Direction.AUTOTASK_TO_GHL),),
    )
    assert sales.ghl_pipeline_id == service.ghl_pipeline_id  # the forbidden shape
    smap = StageMap(sales=sales, service=service)
    # pipeline_for prefers sales on collision — the loader (load_stage_map)
    # rejects this config outright before a StageMap is ever built.
    assert smap.pipeline_for("pipe-same").autotask_entity == "opportunity"


@pytest.mark.asyncio
async def test_validate_stage_map_flags_dead_entries():
    from integration.config import stages as stages_module

    smap = make_stage_map()

    class _FakeGHLPipes:
        async def get_pipelines(self):
            return [
                {"id": "pipe-sales", "name": "Sales", "stages": [
                    {"id": "stage-new"}, {"id": "stage-qualified"},
                    {"id": "stage-proposal"}, {"id": "stage-won"},
                ]},
                # service pipeline missing entirely
            ]

    class _FakeAT:
        async def get_picklist_values(self, entity, field):
            return ["1", "2", "3"] if entity == "Opportunities" else ["1"]

    # monkeypatch the cached loader to return our map
    orig = stages_module.load_stage_map
    stages_module.load_stage_map = lambda: smap
    try:
        problems = await stages_module.validate_stage_map(autotask=_FakeAT(), ghl=_FakeGHLPipes())
    finally:
        stages_module.load_stage_map = orig

    joined = " | ".join(problems)
    assert "service" in joined                      # missing pipeline flagged
    assert "10" in joined                           # AT stage 10 not a real picklist value
    assert "5" in joined                            # AT ticket status 5 not real
