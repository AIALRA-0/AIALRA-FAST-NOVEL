"""Property tests for the map evidence publication gate."""

from __future__ import annotations

from hypothesis import given, strategies as st

from app.atlas import _validate_map_constraints


@given(
    horizontal=st.floats(min_value=-2000, max_value=2000, allow_nan=False, allow_infinity=False),
    vertical=st.floats(min_value=20, max_value=2000, allow_nan=False, allow_infinity=False),
)
def test_north_constraint_accepts_only_the_evidence_aligned_side(horizontal: float, vertical: float) -> None:
    relation = [{
        "id": 1,
        "source_entity_id": 10,
        "target_entity_id": 20,
        "relative_position": "north",
    }]
    summary, failures = _validate_map_constraints(
        {10: (horizontal, -vertical), 20: (0.0, 0.0)},
        relation,
        [],
        {"same_level_overlap_pairs": 0, "pairs": []},
        [],
        0,
    )
    expected_alignment = vertical / max(1.0, (horizontal * horizontal + vertical * vertical) ** 0.5)
    assert (summary["direction"]["passed"] == 1) is (expected_alignment >= 0.15)
    assert bool(failures) is (expected_alignment < 0.15)


@given(distance=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False))
def test_near_constraint_requires_an_evidence_region_and_bounded_distance(distance: float) -> None:
    relation = [{
        "id": 2,
        "source_entity_id": 10,
        "target_entity_id": 20,
        "relative_position": "near",
    }]
    region = [{
        "id": "proximity-1",
        "kind": "evidence_proximity",
        "node_ids": [10, 20],
        "evidence_ids": [2],
    }]
    summary, failures = _validate_map_constraints(
        {10: (0.0, 0.0), 20: (distance, 0.0)},
        relation,
        region,
        {"same_level_overlap_pairs": 0, "pairs": []},
        [],
        0,
    )
    assert (summary["proximity"]["passed"] == 1) is (distance <= 280.0)
    assert bool(failures) is (distance > 280.0)


def test_overlap_and_geometry_failures_block_publication_even_without_relations() -> None:
    summary, failures = _validate_map_constraints(
        {10: (0.0, 0.0)},
        [],
        [],
        {"same_level_overlap_pairs": 1, "pairs": [{"left_region_id": "a", "right_region_id": "b"}]},
        ["a"],
        0,
    )
    assert summary["geometry_failure_count"] == 1
    assert summary["unrelated_overlap_pair_count"] == 1
    assert {item["reason"] for item in failures} == {"region_geometry_failed", "unrelated_region_overlap"}
