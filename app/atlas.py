"""Evidence-preserving semantic atlas projection for 2D and 3D views."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict, deque
from typing import Any

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import triangulate, unary_union


LAYOUT_VERSION = "semantic-atlas-v2.9.7-constraint-gate1"
_DIRECTION_VECTORS = {
    "north": (0.0, -1.0), "south": (0.0, 1.0),
    "east": (1.0, 0.0), "west": (-1.0, 0.0),
    "northeast": (0.72, -0.72), "northwest": (-0.72, -0.72),
    "southeast": (0.72, 0.72), "southwest": (-0.72, 0.72),
    "upstream": (0.0, -0.8), "downstream": (0.0, 0.8),
}


def _stable_unit(book_id: int, entity_id: int, axis: str) -> float:
    digest = hashlib.sha256(f"{book_id}:{entity_id}:{axis}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(origin: tuple[float, float], left: tuple[float, float], right: tuple[float, float]) -> float:
        return (left[0] - origin[0]) * (right[1] - origin[1]) - (left[1] - origin[1]) * (right[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _expanded_hull(points: list[tuple[float, float]], padding: float = 54.0) -> list[dict[str, float]]:
    hull = _convex_hull(points)
    if not hull:
        return []
    center_x = sum(point[0] for point in hull) / len(hull)
    center_y = sum(point[1] for point in hull) / len(hull)
    expanded: list[dict[str, float]] = []
    for x, y in hull:
        distance = max(1.0, math.hypot(x - center_x, y - center_y))
        expanded.append({
            "x": round(x + (x - center_x) / distance * padding, 2),
            "y": round(y + (y - center_y) / distance * padding, 2),
        })
    return expanded


def _region_boundary(points: list[tuple[float, float]], padding: float = 54.0) -> list[dict[str, float]]:
    """Return a drawable semantic boundary even for one or two contained places."""

    if not points:
        return []
    if len(points) == 1:
        x, y = points[0]
        return [
            {"x": round(x - padding, 2), "y": round(y - padding * 0.72, 2)},
            {"x": round(x + padding, 2), "y": round(y - padding * 0.72, 2)},
            {"x": round(x + padding, 2), "y": round(y + padding * 0.72, 2)},
            {"x": round(x - padding, 2), "y": round(y + padding * 0.72, 2)},
        ]
    if len(points) == 2:
        min_x = min(point[0] for point in points) - padding
        max_x = max(point[0] for point in points) + padding
        min_y = min(point[1] for point in points) - padding * 0.72
        max_y = max(point[1] for point in points) + padding * 0.72
        return [
            {"x": round(min_x, 2), "y": round(min_y, 2)},
            {"x": round(max_x, 2), "y": round(min_y, 2)},
            {"x": round(max_x, 2), "y": round(max_y, 2)},
            {"x": round(min_x, 2), "y": round(max_y, 2)},
        ]
    return _expanded_hull(points, padding)


def _label_width(label: str) -> float:
    """Approximate the SVG label width without depending on browser font metrics."""

    width = sum(12.0 if ord(character) > 127 else 7.2 for character in label)
    return round(max(68.0, min(190.0, width + 24.0)), 2)


def _label_lines(label: str, max_width: float = 166.0, max_lines: int = 2) -> list[str]:
    """Wrap mixed Chinese and Latin labels with the same approximate SVG metrics."""

    text = " ".join(str(label or "").split())
    if not text:
        return [""]
    lines: list[str] = []
    remaining = text
    while remaining and len(lines) < max_lines:
        width = 0.0
        split_at = 0
        last_break = 0
        for index, character in enumerate(remaining):
            width += 12.0 if ord(character) > 127 else 7.2
            if character in " -—/·":
                last_break = index + 1
            if width > max_width:
                split_at = last_break or max(1, index)
                break
        if split_at == 0:
            lines.append(remaining.strip())
            remaining = ""
        else:
            lines.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
    if remaining:
        last = lines[-1].rstrip("…")
        while last and sum(12.0 if ord(character) > 127 else 7.2 for character in last + "…") > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _node_label_placements(
    places: list[dict[str, Any]],
    positions: dict[int, tuple[float, float]],
    story_locations: list[int],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, float]]]:
    """Place every map label once so the browser and region geometry share one footprint."""

    if not positions:
        return {}, {}
    values = list(positions.values())
    limits = {
        "left": min(point[0] for point in values) - 90.0,
        "right": max(point[0] for point in values) + 90.0,
        "top": min(point[1] for point in values) - 70.0,
        "bottom": max(point[1] for point in values) + 70.0,
    }
    occupied = [
        {
            "min_x": point[0] - 25.0, "max_x": point[0] + 25.0,
            "min_y": point[1] - 25.0, "max_y": point[1] + 25.0,
        }
        for point in positions.values()
    ]
    first_visit: dict[int, int] = {}
    for index, node_id in enumerate(story_locations):
        first_visit.setdefault(node_id, index)
    ordered = sorted(
        places,
        key=lambda item: (
            -float(item.get("importance") or 0.0),
            first_visit.get(int(item["id"]), 1_000_000),
            int(item["id"]),
        ),
    )
    placements: dict[int, dict[str, Any]] = {}
    footprints: dict[int, dict[str, float]] = {}
    for place in ordered:
        node_id = int(place["id"])
        point_x, point_y = positions[node_id]
        lines = _label_lines(str(place["name"]), 150.0, 2)
        width = max(52.0, max(_label_width(line) for line in lines))
        height = 24.0 + max(0, len(lines) - 1) * 15.0
        candidates = [
            (point_x, point_y + 47.0, "middle"),
            (point_x, point_y - 40.0, "middle"),
            (point_x + 34.0, point_y + 4.0, "start"),
            (point_x - 34.0, point_y + 4.0, "end"),
            (point_x + 32.0, point_y - 31.0, "start"),
            (point_x - 32.0, point_y - 31.0, "end"),
            (point_x + 32.0, point_y + 35.0, "start"),
            (point_x - 32.0, point_y + 35.0, "end"),
        ]
        selected: tuple[float, float, str, dict[str, float]] | None = None
        selected_score = float("inf")
        for x, y, anchor in candidates:
            left = x - width / 2 if anchor == "middle" else x if anchor == "start" else x - width
            label_box = {
                "min_x": left - 8.0, "max_x": left + width + 8.0,
                "min_y": y - 21.0, "max_y": y + height - 12.0,
            }
            outside = (
                label_box["min_x"] < limits["left"] or label_box["max_x"] > limits["right"]
                or label_box["min_y"] < limits["top"] or label_box["max_y"] > limits["bottom"]
            )
            collisions = sum(1 for item in occupied if _boxes_intersect(label_box, item))
            score = collisions * 1000 + (100 if outside else 0)
            if score < selected_score:
                selected = (x, y, anchor, label_box)
                selected_score = score
            if score == 0:
                break
        assert selected is not None
        x, y, anchor, label_box = selected
        occupied.append(label_box)
        placements[node_id] = {
            "x": round(x, 2), "y": round(y, 2), "anchor": anchor,
            "width": round(width, 2), "height": round(height, 2), "lines": lines,
            "visible": selected_score == 0,
            "bbox": {key: round(value, 2) for key, value in label_box.items()},
        }
        footprints[node_id] = {
            "min_x": round(min(point_x - 25.0, label_box["min_x"]), 2),
            "max_x": round(max(point_x + 25.0, label_box["max_x"]), 2),
            "min_y": round(min(point_y - 25.0, label_box["min_y"]), 2),
            "max_y": round(max(point_y + 25.0, label_box["max_y"]), 2),
        }
    return placements, footprints


def _polygon_points(polygon: Polygon) -> list[dict[str, float]]:
    return [
        {"x": round(float(x), 2), "y": round(float(y), 2)}
        for x, y in list(polygon.exterior.coords)[:-1]
    ]


def _footprint_polygon(footprint: dict[str, float]) -> Polygon:
    return box(
        float(footprint["min_x"]), float(footprint["min_y"]),
        float(footprint["max_x"]), float(footprint["max_y"]),
    )


def _minimum_connection_lines(geometries: list[Polygon]) -> list[LineString]:
    """Connect disjoint footprints with a deterministic minimum-distance tree."""

    if len(geometries) < 2:
        return []
    connected = {0}
    remaining = set(range(1, len(geometries)))
    result: list[LineString] = []
    while remaining:
        distance, left_index, right_index = min(
            (
                geometries[left].distance(geometries[right]), left, right
            )
            for left in connected for right in remaining
        )
        left_point, right_point = geometries[left_index].representative_point(), geometries[right_index].representative_point()
        if distance > 0:
            result.append(LineString([(left_point.x, left_point.y), (right_point.x, right_point.y)]))
        connected.add(right_index)
        remaining.remove(right_index)
    return result


def _region_geometry(
    member_ids: list[int],
    footprints: dict[int, dict[str, float]],
    padding: float,
    child_hulls: list[list[dict[str, float]]] | None = None,
) -> tuple[Polygon, list[dict[str, float]], list[list[dict[str, float]]], dict[str, Any]]:
    """Build and verify a region around node circles, labels, and child regions."""

    required: list[Polygon] = [
        _footprint_polygon(footprints[node_id])
        for node_id in member_ids if node_id in footprints
    ]
    for hull in child_hulls or []:
        if len(hull) >= 3:
            required.append(Polygon([(float(point["x"]), float(point["y"])) for point in hull]).buffer(0))
    if not required:
        return Polygon(), [], [], {"valid": False, "reason": "empty_region", "missing_node_ids": member_ids}
    connectors = [line.buffer(14.0, cap_style="round", join_style="round") for line in _minimum_connection_lines(required)]
    region = unary_union([*required, *connectors]).buffer(padding, join_style="round")
    if region.geom_type == "MultiPolygon":
        region = region.convex_hull
    region = region.simplify(2.2, preserve_topology=True).buffer(0)
    missing = [
        node_id for node_id in member_ids
        if node_id in footprints and not region.covers(_footprint_polygon(footprints[node_id]))
    ]
    child_missing = [
        index for index, hull in enumerate(child_hulls or [])
        if len(hull) >= 3 and not region.covers(Polygon([(point["x"], point["y"]) for point in hull]).buffer(0))
    ]
    if missing or child_missing or not region.is_valid:
        region = unary_union(required).convex_hull.buffer(padding, join_style="round")
        missing = [
            node_id for node_id in member_ids
            if node_id in footprints and not region.covers(_footprint_polygon(footprints[node_id]))
        ]
        child_missing = [
            index for index, hull in enumerate(child_hulls or [])
            if len(hull) >= 3 and not region.covers(Polygon([(point["x"], point["y"]) for point in hull]).buffer(0))
        ]
    triangles = []
    for triangle in triangulate(region):
        if not region.covers(triangle.representative_point()):
            continue
        triangles.append(_polygon_points(triangle))
    validation = {
        "valid": bool(region.is_valid and not missing and not child_missing),
        "reason": "verified" if region.is_valid and not missing and not child_missing else "containment_failed",
        "missing_node_ids": missing,
        "missing_child_regions": child_missing,
        "footprint_count": len(required),
    }
    return region, _polygon_points(region), triangles, validation


def _boxes_intersect(left: dict[str, float], right: dict[str, float], padding: float = 0.0) -> bool:
    return not (
        left["max_x"] + padding <= right["min_x"]
        or right["max_x"] + padding <= left["min_x"]
        or left["max_y"] + padding <= right["min_y"]
        or right["max_y"] + padding <= left["min_y"]
    )


def _region_label_geometry(
    region_id: str,
    label: str,
    hull: list[dict[str, float]],
    member_ids: list[int],
    positions: dict[int, tuple[float, float]],
    occupied: list[dict[str, float]],
    node_footprints: dict[int, dict[str, float]],
    region_hulls: dict[str, list[dict[str, float]]],
    allowed_hull_ids: set[str],
    child_region_ids: set[str],
) -> tuple[dict[str, Any], dict[str, float]]:
    """Place a region title in a free title pocket or a side label lane."""

    lines = _label_lines(label, 166.0, 3)
    width = max(_label_width(line) for line in lines)
    height = 28.0 + max(0, len(lines) - 1) * 15.0
    min_x = min(float(point["x"]) for point in hull)
    max_x = max(float(point["x"]) for point in hull)
    min_y = min(float(point["y"]) for point in hull)
    max_y = max(float(point["y"]) for point in hull)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    own_polygon = Polygon([(float(point["x"]), float(point["y"])) for point in hull]).buffer(0)
    node_boxes = list(node_footprints.values())
    child_polygons = [
        Polygon([(float(point["x"]), float(point["y"])) for point in region_hulls[child_id]]).buffer(0)
        for child_id in child_region_ids if len(region_hulls.get(child_id, [])) >= 3
    ]
    internal_candidates = [
        (min_x + width / 2 + 16.0, min_y + height + 14.0),
        (max_x - width / 2 - 16.0, min_y + height + 14.0),
        (min_x + width / 2 + 16.0, max_y - 14.0),
        (max_x - width / 2 - 16.0, max_y - 14.0),
    ]
    ranked: list[tuple[float, float, float, dict[str, float], str]] = []
    for index, (x, y) in enumerate(internal_candidates):
        box = {
            "min_x": x - width / 2,
            "max_x": x + width / 2,
            "min_y": y - height + 5.0,
            "max_y": y + 5.0,
        }
        collisions = sum(1 for item in node_boxes if _boxes_intersect(box, item, 5.0))
        collisions += sum(3 for item in occupied if _boxes_intersect(box, item, 8.0))
        box_polygon = _footprint_polygon(box)
        if not own_polygon.covers(box_polygon):
            collisions += 20
        collisions += sum(6 for child in child_polygons if child.intersects(box_polygon))
        distance = math.hypot(x - center_x, y - center_y)
        ranked.append((collisions * 10_000.0 + distance + index * 0.01, x, y, box, "internal_title"))

    external_hulls = [
        Polygon([(float(point["x"]), float(point["y"])) for point in other_hull]).buffer(0)
        for other_id, other_hull in region_hulls.items()
        if other_id not in allowed_hull_ids and len(other_hull) >= 3
    ]
    lane_offsets = [0.0, -38.0, 38.0, -76.0, 76.0, -114.0, 114.0, -152.0, 152.0]
    for side_index, x in enumerate((min_x - width / 2 - 22.0, max_x + width / 2 + 22.0)):
        for offset_index, offset in enumerate(lane_offsets):
            y = center_y + offset
            box = {
                "min_x": x - width / 2,
                "max_x": x + width / 2,
                "min_y": y - height + 5.0,
                "max_y": y + 5.0,
            }
            box_polygon = _footprint_polygon(box)
            collisions = sum(1 for item in node_boxes if _boxes_intersect(box, item, 5.0))
            collisions += sum(3 for item in occupied if _boxes_intersect(box, item, 8.0))
            collisions += sum(6 for other in external_hulls if other.intersects(box_polygon))
            if own_polygon.intersects(box_polygon):
                collisions += 4
            distance = math.hypot(x - center_x, y - center_y)
            ranked.append((
                collisions * 10_000.0 + distance + side_index * 0.1 + offset_index * 0.01,
                x, y, box, "side_lane",
            ))
    score, x, y, selected_box, placement_mode = min(ranked, key=lambda item: item[0])
    occupied.append(selected_box)
    connector_target = min(
        hull,
        key=lambda point: math.hypot(float(point["x"]) - x, float(point["y"]) - y),
    )
    anchor = {
        "x": round(x, 2),
        "y": round(y, 2),
        "text_anchor": "middle",
        "lines": lines,
        "width": round(width, 2),
        "height": round(height, 2),
        "bbox": {key: round(value, 2) for key, value in selected_box.items()},
        "placement_mode": placement_mode,
        "quality_state": "clear" if score < 10_000 else "best_available",
    }
    connector = {
        "x1": round(float(connector_target["x"]), 2),
        "y1": round(float(connector_target["y"]), 2),
        "x2": round(x, 2),
        "y2": round(y - 7.0, 2),
        "hidden": placement_mode == "internal_title",
    }
    return anchor, connector


def _region_overlap_summary(regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure avoidable region overlap while allowing evidence-backed nesting."""

    overlaps: list[dict[str, Any]] = []
    maximum = 0.0
    for index, left in enumerate(regions):
        left_hull = left.get("hull") or []
        if len(left_hull) < 3:
            continue
        left_nodes = {int(node_id) for node_id in left.get("node_ids", [])}
        left_polygon = Polygon([(float(point["x"]), float(point["y"])) for point in left_hull]).buffer(0)
        left_area = max(1.0, float(left_polygon.area))
        for right in regions[index + 1:]:
            right_hull = right.get("hull") or []
            if len(right_hull) < 3:
                continue
            right_nodes = {int(node_id) for node_id in right.get("node_ids", [])}
            nested_by_membership = bool(left_nodes and right_nodes and (left_nodes <= right_nodes or right_nodes <= left_nodes))
            nested_by_parent = left.get("parent_region_id") == right.get("id") or right.get("parent_region_id") == left.get("id")
            shared_evidence_membership = bool(
                left_nodes.intersection(right_nodes)
                and str(left.get("kind", "")).startswith("evidence_")
                and str(right.get("kind", "")).startswith("evidence_")
            )
            if nested_by_membership or nested_by_parent or shared_evidence_membership:
                continue
            right_polygon = Polygon([(float(point["x"]), float(point["y"])) for point in right_hull]).buffer(0)
            overlap_area = float(left_polygon.intersection(right_polygon).area)
            if overlap_area <= 0:
                continue
            right_area = max(1.0, float(right_polygon.area))
            ratio = overlap_area / min(left_area, right_area)
            maximum = max(maximum, ratio)
            overlaps.append({
                "left_region_id": left["id"],
                "right_region_id": right["id"],
                "left_region_kind": left.get("kind"),
                "right_region_kind": right.get("kind"),
                "overlap_ratio_percent": round(ratio * 100, 2),
            })
    return {
        "same_level_overlap_pairs": len(overlaps),
        "maximum_overlap_ratio_percent": round(maximum * 100, 2),
        "pairs": overlaps,
    }


def _validate_map_constraints(
    positions: dict[int, tuple[float, float]],
    relations: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    overlap_summary: dict[str, Any],
    geometry_failure_ids: list[str],
    route_count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reject attractive layouts that contradict explicit geographic evidence."""

    failed: list[dict[str, Any]] = []
    checked = Counter()
    passed = Counter()
    evidence_regions = [region for region in regions if str(region.get("kind", "")).startswith("evidence_")]
    for relation in relations:
        relation_id = int(relation["id"])
        source = int(relation["source_entity_id"])
        target = int(relation["target_entity_id"])
        relative_position = str(relation["relative_position"])
        if source not in positions or target not in positions:
            continue
        if relative_position in {"inside", "contains"}:
            checked["containment"] += 1
            matching = any(
                relation_id in {int(item) for item in region.get("evidence_ids", [])}
                and source in {int(item) for item in region.get("node_ids", [])}
                and target in {int(item) for item in region.get("node_ids", [])}
                for region in evidence_regions
                if region.get("kind") == "evidence_containment"
            )
            if matching:
                passed["containment"] += 1
            else:
                failed.append({
                    "relation_id": relation_id,
                    "source_entity_id": source,
                    "target_entity_id": target,
                    "relative_position": relative_position,
                    "reason": "missing_containment_region",
                })
        elif relative_position == "near":
            checked["proximity"] += 1
            distance = math.dist(positions[source], positions[target])
            matching = any(
                relation_id in {int(item) for item in region.get("evidence_ids", [])}
                and source in {int(item) for item in region.get("node_ids", [])}
                and target in {int(item) for item in region.get("node_ids", [])}
                for region in evidence_regions
                if region.get("kind") == "evidence_proximity"
            )
            if matching and distance <= 280.0:
                passed["proximity"] += 1
            else:
                failed.append({
                    "relation_id": relation_id,
                    "source_entity_id": source,
                    "target_entity_id": target,
                    "relative_position": relative_position,
                    "distance": round(distance, 2),
                    "reason": "proximity_too_far" if matching else "missing_proximity_region",
                })
        elif relative_position in _DIRECTION_VECTORS:
            checked["direction"] += 1
            expected_x, expected_y = _DIRECTION_VECTORS[relative_position]
            actual_x = positions[source][0] - positions[target][0]
            actual_y = positions[source][1] - positions[target][1]
            denominator = max(1.0, math.hypot(actual_x, actual_y) * math.hypot(expected_x, expected_y))
            alignment = (actual_x * expected_x + actual_y * expected_y) / denominator
            if alignment >= 0.15:
                passed["direction"] += 1
            else:
                failed.append({
                    "relation_id": relation_id,
                    "source_entity_id": source,
                    "target_entity_id": target,
                    "relative_position": relative_position,
                    "alignment": round(alignment, 3),
                    "reason": "direction_contradiction",
                })
    geometry_failed = len(geometry_failure_ids)
    overlap_failed = int(overlap_summary.get("same_level_overlap_pairs", 0))
    summary = {
        "priority_order": ["explicit_evidence", "story_route", "visual_clustering"],
        "explicit_relation_count": sum(checked.values()),
        "passed_relation_count": sum(passed.values()),
        "failed_relation_count": len(failed),
        "containment": {"checked": checked["containment"], "passed": passed["containment"]},
        "proximity": {"checked": checked["proximity"], "passed": passed["proximity"]},
        "direction": {"checked": checked["direction"], "passed": passed["direction"]},
        "story_route_count": route_count,
        "geometry_failure_count": geometry_failed,
        "unrelated_overlap_pair_count": overlap_failed,
    }
    if geometry_failed:
        failed.extend({"region_id": region_id, "reason": "region_geometry_failed"} for region_id in geometry_failure_ids)
    if overlap_failed:
        failed.extend({"reason": "unrelated_region_overlap", **pair} for pair in overlap_summary.get("pairs", []))
    return summary, failed


def _containment_depths(node_ids: list[int], relations: list[dict[str, Any]]) -> tuple[dict[int, int], dict[int, int]]:
    parent: dict[int, int] = {}
    for relation in relations:
        source = int(relation["source_entity_id"])
        target = int(relation["target_entity_id"])
        if relation["relative_position"] == "inside":
            parent[source] = target
        elif relation["relative_position"] == "contains":
            parent[target] = source
    depths: dict[int, int] = {}
    for node_id in node_ids:
        seen = {node_id}
        current = node_id
        depth = 0
        while current in parent and parent[current] not in seen and depth < 12:
            current = parent[current]
            seen.add(current)
            depth += 1
        depths[node_id] = depth
    return depths, parent


def _connected_components(node_ids: list[int], edges: list[tuple[int, int]]) -> list[list[int]]:
    graph: dict[int, set[int]] = defaultdict(set)
    for source, target in edges:
        if source == target:
            continue
        graph[source].add(target)
        graph[target].add(source)
    remaining = set(node_ids)
    components: list[list[int]] = []
    while remaining:
        seed = min(remaining)
        queue = deque([seed])
        remaining.remove(seed)
        component: list[int] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in sorted(graph[current]):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    return sorted(components, key=lambda item: (-len(item), item[0]))


def _semantic_region_groups(
    node_ids: list[int],
    edges: list[tuple[int, int]],
    story_locations: list[int],
) -> list[list[int]]:
    """Split large connected worlds into stable route-neighborhood regions."""

    graph: dict[int, set[int]] = defaultdict(set)
    for source, target in edges:
        if source != target:
            graph[source].add(target)
            graph[target].add(source)
    story_rank: dict[int, int] = {}
    for index, node_id in enumerate(story_locations):
        story_rank.setdefault(node_id, index)
    groups: list[list[int]] = []
    for component in _connected_components(node_ids, edges):
        if len(component) <= 8:
            groups.append(component)
            continue
        remaining = set(component)
        target_size = max(5, min(8, round(math.sqrt(len(component)) * 1.45)))
        while remaining:
            seed = min(
                remaining,
                key=lambda value: (story_rank.get(value, 1_000_000), -len(graph[value]), value),
            )
            queue = deque([seed])
            queued = {seed}
            group: list[int] = []
            while queue and len(group) < target_size:
                current = queue.popleft()
                if current not in remaining:
                    continue
                remaining.remove(current)
                group.append(current)
                neighbors = sorted(
                    (item for item in graph[current] if item in remaining and item not in queued),
                    key=lambda value: (story_rank.get(value, 1_000_000), value),
                )
                queue.extend(neighbors)
                queued.update(neighbors)
            groups.append(sorted(group))
    if len(groups) > 1 and len(groups[-1]) < 3:
        groups[-2].extend(groups.pop())
        groups[-1] = sorted(set(groups[-1]))
    return groups


def _layout_nodes(
    book_id: int,
    node_ids: list[int],
    edges: list[tuple[int, int]],
    relations: list[dict[str, Any]],
    region_groups: list[list[int]],
) -> dict[int, tuple[float, float]]:
    if not node_ids:
        return {}
    positions = {
        node_id: (
            (_stable_unit(book_id, node_id, "x") - 0.5) * 900,
            (_stable_unit(book_id, node_id, "y") - 0.5) * 620,
        )
        for node_id in node_ids
    }
    valid_ids = set(node_ids)
    graph_edges = sorted({tuple(sorted((source, target))) for source, target in edges if source in valid_ids and target in valid_ids and source != target})
    directional = [
        relation for relation in relations
        if relation["relative_position"] in _DIRECTION_VECTORS
        and int(relation["source_entity_id"]) in valid_ids
        and int(relation["target_entity_id"]) in valid_ids
    ]
    ordered = sorted(node_ids)
    for iteration in range(120):
        cooling = 1.0 - iteration / 145.0
        delta = {node_id: [0.0, 0.0] for node_id in node_ids}
        # Stable bounded repulsion keeps large books responsive without hiding disconnected places.
        for index, source in enumerate(ordered):
            neighbors = ordered[index + 1:index + 49]
            for target in neighbors:
                source_x, source_y = positions[source]
                target_x, target_y = positions[target]
                dx = source_x - target_x
                dy = source_y - target_y
                distance_sq = max(225.0, dx * dx + dy * dy)
                force = 25000.0 / distance_sq
                distance = math.sqrt(distance_sq)
                move_x = dx / distance * force
                move_y = dy / distance * force
                delta[source][0] += move_x
                delta[source][1] += move_y
                delta[target][0] -= move_x
                delta[target][1] -= move_y
        for source, target in graph_edges:
            source_x, source_y = positions[source]
            target_x, target_y = positions[target]
            dx = target_x - source_x
            dy = target_y - source_y
            distance = max(1.0, math.hypot(dx, dy))
            spring = (distance - 180.0) * 0.018
            move_x = dx / distance * spring
            move_y = dy / distance * spring
            delta[source][0] += move_x
            delta[source][1] += move_y
            delta[target][0] -= move_x
            delta[target][1] -= move_y
        group_centers: list[tuple[list[int], float, float]] = []
        for group in region_groups:
            members = [node_id for node_id in group if node_id in positions]
            if not members:
                continue
            center_x = sum(positions[node_id][0] for node_id in members) / len(members)
            center_y = sum(positions[node_id][1] for node_id in members) / len(members)
            group_centers.append((members, center_x, center_y))
            for node_id in members:
                node_x, node_y = positions[node_id]
                delta[node_id][0] += (center_x - node_x) * 0.022
                delta[node_id][1] += (center_y - node_y) * 0.022
        for index, (left_members, left_x, left_y) in enumerate(group_centers):
            for right_members, right_x, right_y in group_centers[index + 1:]:
                dx = left_x - right_x
                dy = left_y - right_y
                distance = max(1.0, math.hypot(dx, dy))
                if distance >= 430:
                    continue
                force = (430 - distance) * 0.025
                move_x = dx / distance * force
                move_y = dy / distance * force
                for node_id in left_members:
                    delta[node_id][0] += move_x
                    delta[node_id][1] += move_y
                for node_id in right_members:
                    delta[node_id][0] -= move_x
                    delta[node_id][1] -= move_y
        for relation in directional:
            source = int(relation["source_entity_id"])
            target = int(relation["target_entity_id"])
            vector_x, vector_y = _DIRECTION_VECTORS[str(relation["relative_position"])]
            source_x, source_y = positions[source]
            target_x, target_y = positions[target]
            # A cardinal relation constrains only its stated axis. Preserve the
            # orthogonal separation so evidence correction cannot stack whole
            # regions on top of each other.
            desired_x = source_x if abs(vector_x) < 0.01 else target_x + vector_x * 800.0
            desired_y = source_y if abs(vector_y) < 0.01 else target_y + vector_y * 800.0
            delta[source][0] += (desired_x - source_x) * 0.12
            delta[source][1] += (desired_y - source_y) * 0.12
            delta[target][0] -= (desired_x - source_x) * 0.035
            delta[target][1] -= (desired_y - source_y) * 0.035
        for node_id in node_ids:
            x, y = positions[node_id]
            limit = 20.0 * cooling
            positions[node_id] = (
                x + max(-limit, min(limit, delta[node_id][0])),
                y + max(-limit, min(limit, delta[node_id][1])),
            )

    min_x = min(point[0] for point in positions.values())
    max_x = max(point[0] for point in positions.values())
    min_y = min(point[1] for point in positions.values())
    max_y = max(point[1] for point in positions.values())
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    # A long novel is a navigable world, not a poster that must fit in one frame.
    # Grow the deterministic coordinate plane with the number of places so node
    # and label spacing stays useful while the viewport provides pan and zoom.
    density_scale = max(1.0, math.sqrt(len(node_ids) / 40.0))
    canvas_width = 1080.0 * density_scale
    canvas_height = 700.0 * density_scale
    return {
        node_id: (
            round(90.0 + (point[0] - min_x) / width * canvas_width, 2),
            round(90.0 + (point[1] - min_y) / height * canvas_height, 2),
        )
        for node_id, point in positions.items()
    }


def _separate_sibling_groups(
    positions: dict[int, tuple[float, float]],
    groups: list[list[int]],
    padding: float = 110.0,
    skip_shared_members: bool = False,
) -> dict[int, tuple[float, float]]:
    """Move disjoint topology groups apart without changing membership or relative shape."""

    result = dict(positions)
    for _ in range(18):
        changed = False
        boxes: list[tuple[list[int], float, float, float, float]] = []
        for group in groups:
            members = [item for item in group if item in result]
            if not members:
                continue
            xs = [result[item][0] for item in members]
            ys = [result[item][1] for item in members]
            boxes.append((members, min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding))
        for index, (left_members, left_min_x, left_min_y, left_max_x, left_max_y) in enumerate(boxes):
            for right_members, right_min_x, right_min_y, right_max_x, right_max_y in boxes[index + 1:]:
                if skip_shared_members and set(left_members).intersection(right_members):
                    continue
                overlap_x = min(left_max_x, right_max_x) - max(left_min_x, right_min_x)
                overlap_y = min(left_max_y, right_max_y) - max(left_min_y, right_min_y)
                if overlap_x <= 0 or overlap_y <= 0:
                    continue
                changed = True
                left_center_x = (left_min_x + left_max_x) / 2
                right_center_x = (right_min_x + right_max_x) / 2
                left_center_y = (left_min_y + left_max_y) / 2
                right_center_y = (right_min_y + right_max_y) / 2
                if overlap_x <= overlap_y:
                    direction = -1 if left_center_x <= right_center_x else 1
                    move_x, move_y = direction * (overlap_x / 2 + 8), 0.0
                else:
                    direction = -1 if left_center_y <= right_center_y else 1
                    move_x, move_y = 0.0, direction * (overlap_y / 2 + 8)
                for node_id in left_members:
                    x, y = result[node_id]
                    result[node_id] = (x + move_x, y + move_y)
                for node_id in right_members:
                    x, y = result[node_id]
                    result[node_id] = (x - move_x, y - move_y)
        if not changed:
            break
    min_x = min((point[0] for point in result.values()), default=0.0)
    min_y = min((point[1] for point in result.values()), default=0.0)
    return {node_id: (round(x - min_x + 100, 2), round(y - min_y + 100, 2)) for node_id, (x, y) in result.items()}


def _separate_nodes(
    positions: dict[int, tuple[float, float]],
    minimum_distance: float = 118.0,
) -> dict[int, tuple[float, float]]:
    """Give every place enough room for its node and one readable label."""

    result = dict(positions)
    ordered = sorted(result)
    for _ in range(160):
        changed = False
        for index, source in enumerate(ordered):
            for target in ordered[index + 1:]:
                source_x, source_y = result[source]
                target_x, target_y = result[target]
                dx = target_x - source_x
                dy = target_y - source_y
                distance = math.hypot(dx, dy)
                if distance >= minimum_distance:
                    continue
                changed = True
                if distance < 0.001:
                    angle = _stable_unit(source, target, "collision") * math.tau
                    dx, dy, distance = math.cos(angle), math.sin(angle), 1.0
                shift = (minimum_distance - distance) / 2 + 1.0
                move_x = dx / distance * shift
                move_y = dy / distance * shift
                result[source] = (source_x - move_x, source_y - move_y)
                result[target] = (target_x + move_x, target_y + move_y)
        if not changed:
            break
    return {node_id: (round(x, 2), round(y, 2)) for node_id, (x, y) in result.items()}


def _compact_proximity_relations(
    positions: dict[int, tuple[float, float]],
    edges: list[tuple[int, int]],
    maximum_distance: float = 230.0,
) -> dict[int, tuple[float, float]]:
    """Keep explicitly nearby places visually nearby after region separation."""

    result = dict(positions)
    valid_edges = sorted({
        tuple(sorted((source, target)))
        for source, target in edges
        if source in result and target in result and source != target
    })
    for _ in range(24):
        changed = False
        for source, target in valid_edges:
            source_x, source_y = result[source]
            target_x, target_y = result[target]
            dx = target_x - source_x
            dy = target_y - source_y
            distance = math.hypot(dx, dy)
            if distance <= maximum_distance:
                continue
            changed = True
            if distance < 0.001:
                angle = _stable_unit(source, target, "proximity") * math.tau
                dx, dy, distance = math.cos(angle), math.sin(angle), 1.0
            shift = (distance - maximum_distance) / 2
            move_x = dx / distance * shift
            move_y = dy / distance * shift
            result[source] = (source_x + move_x, source_y + move_y)
            result[target] = (target_x - move_x, target_y - move_y)
        if not changed:
            break
    return {node_id: (round(x, 2), round(y, 2)) for node_id, (x, y) in result.items()}


def _enforce_directional_relations(
    positions: dict[int, tuple[float, float]],
    relations: list[dict[str, Any]],
    containment_groups: list[list[int]],
) -> dict[int, tuple[float, float]]:
    """Correct compass evidence while preserving orthogonal region spacing."""

    result = dict(positions)
    directional = [
        relation for relation in relations
        if relation.get("relative_position") in _DIRECTION_VECTORS
        and int(relation["source_entity_id"]) in result
        and int(relation["target_entity_id"]) in result
    ]

    def source_members(node_id: int, other_id: int) -> list[int]:
        candidates = [group for group in containment_groups if node_id in group and other_id not in group]
        return min(candidates, key=lambda group: (len(group), group)) if candidates else [node_id]

    def target_members(node_id: int, other_id: int) -> list[int]:
        candidates = [group for group in containment_groups if node_id in group and other_id not in group]
        return max(candidates, key=lambda group: (len(group), group)) if candidates else [node_id]

    for relation in directional:
        source = int(relation["source_entity_id"])
        target = int(relation["target_entity_id"])
        vector_x, vector_y = _DIRECTION_VECTORS[str(relation["relative_position"])]
        vector_length = max(0.001, math.hypot(vector_x, vector_y))
        unit_x, unit_y = vector_x / vector_length, vector_y / vector_length
        moving = source_members(source, target)
        reference = target_members(target, source)
        moving_x = [result[node_id][0] for node_id in moving]
        moving_y = [result[node_id][1] for node_id in moving]
        reference_x = [result[node_id][0] for node_id in reference]
        reference_y = [result[node_id][1] for node_id in reference]
        if abs(unit_y) > abs(unit_x):
            overlap = min(max(moving_x), max(reference_x)) - max(min(moving_x), min(reference_x))
            if overlap > -240.0:
                direction = -1.0 if sum(moving_x) / len(moving_x) <= sum(reference_x) / len(reference_x) else 1.0
                shift_x = direction * (overlap + 240.0)
                for node_id in moving:
                    node_x, node_y = result[node_id]
                    result[node_id] = (node_x + shift_x, node_y)
        elif abs(unit_x) > abs(unit_y):
            overlap = min(max(moving_y), max(reference_y)) - max(min(moving_y), min(reference_y))
            if overlap > -180.0:
                direction = -1.0 if sum(moving_y) / len(moving_y) <= sum(reference_y) / len(reference_y) else 1.0
                shift_y = direction * (overlap + 180.0)
                for node_id in moving:
                    node_x, node_y = result[node_id]
                    result[node_id] = (node_x, node_y + shift_y)
        source_x, source_y = result[source]
        target_x, target_y = result[target]
        actual_x = source_x - target_x
        actual_y = source_y - target_y
        projection = actual_x * unit_x + actual_y * unit_y
        perpendicular = abs(actual_x * unit_y - actual_y * unit_x)
        required_projection = max(40.0, perpendicular * 0.25)
        if projection >= required_projection:
            continue
        correction = required_projection - projection
        for node_id in moving:
            node_x, node_y = result[node_id]
            result[node_id] = (node_x + unit_x * correction, node_y + unit_y * correction)
    return {node_id: (round(x, 2), round(y, 2)) for node_id, (x, y) in result.items()}


def _enforce_proximity_relations(
    positions: dict[int, tuple[float, float]],
    edges: list[tuple[int, int]],
    containment_groups: list[list[int]],
    maximum_distance: float = 230.0,
) -> dict[int, tuple[float, float]]:
    """Move one complete place family closer without stretching its region."""

    result = dict(positions)
    for source, target in sorted(edges):
        if source not in result or target not in result or source == target:
            continue
        source_x, source_y = result[source]
        target_x, target_y = result[target]
        dx = target_x - source_x
        dy = target_y - source_y
        distance = math.hypot(dx, dy)
        if distance <= maximum_distance:
            continue
        candidates = [group for group in containment_groups if source in group and target not in group]
        members = min(candidates, key=lambda group: (len(group), group)) if candidates else [source]
        shift = distance - maximum_distance
        move_x = dx / distance * shift
        move_y = dy / distance * shift
        for node_id in members:
            node_x, node_y = result[node_id]
            result[node_id] = (node_x + move_x, node_y + move_y)
    return {node_id: (round(x, 2), round(y, 2)) for node_id, (x, y) in result.items()}


def _compact_containment_families(
    book_id: int,
    positions: dict[int, tuple[float, float]],
    children_by_container: dict[int, set[int]],
    groups_by_container: dict[int, list[int]],
    maximum_distance: float = 420.0,
) -> dict[int, tuple[float, float]]:
    """Keep evidence-backed children near their container without flattening hierarchy."""

    result = dict(positions)
    child_to_parent = {
        child: container
        for container, children in children_by_container.items()
        for child in children
    }
    roots = [container for container in children_by_container if container not in child_to_parent]
    pending = sorted(roots) + [container for container in sorted(children_by_container) if container not in roots]
    visited: set[int] = set()
    while pending:
        container = pending.pop(0)
        if container in visited or container not in result:
            continue
        visited.add(container)
        container_x, container_y = result[container]
        for child in sorted(children_by_container.get(container, set())):
            if child not in result:
                continue
            child_x, child_y = result[child]
            distance = math.hypot(child_x - container_x, child_y - container_y)
            if distance > maximum_distance:
                angle = _stable_unit(book_id, child, f"inside:{container}") * math.tau
                target_x = container_x + math.cos(angle) * maximum_distance * 0.52
                target_y = container_y + math.sin(angle) * maximum_distance * 0.52
                move_x = target_x - child_x
                move_y = target_y - child_y
                for node_id in groups_by_container.get(child, [child]):
                    if node_id not in result:
                        continue
                    x, y = result[node_id]
                    result[node_id] = (x + move_x, y + move_y)
            if child in children_by_container:
                pending.append(child)

    adjacency: dict[int, set[int]] = defaultdict(set)
    for container, children in children_by_container.items():
        for child in children:
            adjacency[container].add(child)
            adjacency[child].add(container)
    remaining = set(adjacency)
    while remaining:
        first = min(remaining)
        component: set[int] = set()
        queue = [first]
        while queue:
            node_id = queue.pop()
            if node_id in component:
                continue
            component.add(node_id)
            queue.extend(adjacency[node_id] - component)
        remaining -= component
        visible = [node_id for node_id in component if node_id in result]
        if len(visible) < 2:
            continue
        center_x = sum(result[node_id][0] for node_id in visible) / len(visible)
        center_y = sum(result[node_id][1] for node_id in visible) / len(visible)
        radius = max(math.hypot(result[node_id][0] - center_x, result[node_id][1] - center_y) for node_id in visible)
        target_radius = maximum_distance * 0.72
        if radius <= target_radius:
            continue
        scale = target_radius / radius
        for node_id in visible:
            x, y = result[node_id]
            result[node_id] = (center_x + (x - center_x) * scale, center_y + (y - center_y) * scale)
    return {node_id: (round(x, 2), round(y, 2)) for node_id, (x, y) in result.items()}


def _merge_intersecting_groups(groups: list[list[int]]) -> list[list[int]]:
    """Merge visual families that share a member so group separation stays stable."""

    merged: list[set[int]] = []
    for group in groups:
        current = set(group)
        touching = [item for item in merged if item.intersection(current)]
        for item in touching:
            current.update(item)
            merged.remove(item)
        merged.append(current)
    return [sorted(group) for group in sorted(merged, key=lambda item: min(item))]


def build_map_layout_snapshot(
    connection: sqlite3.Connection,
    book_id: int,
    through_segment: int | None = None,
    from_segment: int = 0,
) -> dict[str, Any]:
    boundary = 1_000_000 if through_segment is None else max(0, through_segment)
    window_start = max(0, int(from_segment))
    places = [dict(row) for row in connection.execute(
        """
        SELECT id, name, importance, first_segment FROM entities
        WHERE book_id = ? AND kind = 'place' AND first_segment <= ? ORDER BY id
        """,
        (book_id, boundary),
    )]
    relations = [dict(row) for row in connection.execute(
        """
        SELECT id, source_entity_id, target_entity_id, relative_position, confidence, first_segment
        FROM place_relations WHERE book_id = ? AND first_segment <= ? ORDER BY id
        """,
        (book_id, boundary),
    )]
    journey = [dict(row) for row in connection.execute(
        """
        SELECT id, from_entity_id, to_entity_id, transport, ordinal, first_segment
        FROM journey_legs WHERE book_id = ? AND first_segment <= ? ORDER BY first_segment, ordinal, id
        """,
        (book_id, boundary),
    )]
    story_steps = [dict(row) for row in connection.execute(
        """
        SELECT id, location_entity_id, story_order, narrative_order
        FROM events WHERE book_id = ? AND first_segment <= ? ORDER BY story_order, narrative_order, id
        """,
        (book_id, boundary),
    )]
    source_payload = {
        "places": places, "relations": relations, "journey": journey,
        "story_steps": story_steps, "from_segment": window_start,
    }
    source_hash = hashlib.sha256(json.dumps(source_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cached = connection.execute(
        """
        SELECT payload_json FROM map_layout_snapshots
        WHERE book_id = ? AND layout_version = ? AND source_hash = ?
        ORDER BY id DESC LIMIT 1
        """,
        (book_id, LAYOUT_VERSION, source_hash),
    ).fetchone()
    if cached is not None:
        return json.loads(str(cached["payload_json"]))

    node_ids = [int(place["id"]) for place in places]
    valid_ids = set(node_ids)
    relation_edges = [
        (int(item["source_entity_id"]), int(item["target_entity_id"]))
        for item in relations
    ]
    journey_edges = [
        (int(item["from_entity_id"]), int(item["to_entity_id"]))
        for item in journey
        if item["from_entity_id"] is not None and item["to_entity_id"] is not None
    ]
    story_locations = [int(item["location_entity_id"]) for item in story_steps if item["location_entity_id"] is not None]
    chronology_edges = [
        (left, right) for left, right in zip(story_locations, story_locations[1:])
        if left != right
    ]
    edges = [edge for edge in relation_edges + journey_edges + chronology_edges if edge[0] in valid_ids and edge[1] in valid_ids]
    proximity_edges = [
        (int(item["source_entity_id"]), int(item["target_entity_id"]))
        for item in relations
        if item["relative_position"] == "near"
        and int(item["source_entity_id"]) in valid_ids
        and int(item["target_entity_id"]) in valid_ids
        and int(item["source_entity_id"]) != int(item["target_entity_id"])
    ]
    components = _semantic_region_groups(node_ids, edges, story_locations)
    positions = _layout_nodes(book_id, node_ids, edges, relations, components)
    positions = _separate_nodes(positions)
    positions = _separate_sibling_groups(positions, components, padding=150.0)
    depths, parent = _containment_depths(node_ids, relations)
    relation_ids_by_container: dict[int, list[int]] = defaultdict(list)
    children_by_container: dict[int, set[int]] = defaultdict(set)
    for relation in relations:
        source = int(relation["source_entity_id"])
        target = int(relation["target_entity_id"])
        if relation["relative_position"] == "inside":
            child, container = source, target
        elif relation["relative_position"] == "contains":
            child, container = target, source
        else:
            continue
        children_by_container[container].add(child)
        relation_ids_by_container[container].append(int(relation["id"]))

    def containment_members(container: int) -> list[int]:
        members = {container}
        pending = list(children_by_container.get(container, set()))
        while pending:
            child = pending.pop()
            if child in members:
                continue
            members.add(child)
            pending.extend(children_by_container.get(child, set()))
        return sorted(members)

    containment_groups_by_container = {
        container: containment_members(container)
        for container in sorted(children_by_container)
    }
    containment_groups = list(containment_groups_by_container.values())
    root_containment_groups = [
        group
        for container, group in containment_groups_by_container.items()
        if parent.get(container) not in children_by_container
    ]
    root_containment_groups = _merge_intersecting_groups(root_containment_groups)
    contained_node_ids = {node_id for group in containment_groups for node_id in group}
    proximity_node_ids = {node_id for edge in proximity_edges for node_id in edge}
    proximity_groups = [
        group for group in _connected_components(sorted(proximity_node_ids), proximity_edges)
        if len(group) >= 2
    ]
    topology_components = [
        [
            node_id for node_id in component
            if node_id not in contained_node_ids and node_id not in proximity_node_ids
        ]
        for component in components
    ]
    topology_components = [component for component in topology_components if component]
    positions = _compact_containment_families(
        book_id,
        positions,
        children_by_container,
        containment_groups_by_container,
    )
    positions = _separate_nodes(positions)
    for _ in range(4):
        positions = _compact_proximity_relations(positions, proximity_edges)
        positions = _separate_nodes(positions)
    separation_groups = _merge_intersecting_groups(
        root_containment_groups + proximity_groups + topology_components
    )
    positions = _separate_sibling_groups(
        positions,
        separation_groups,
        padding=300.0,
    )
    positions = _separate_nodes(positions)
    positions = _separate_sibling_groups(
        positions,
        containment_groups + proximity_groups + topology_components,
        padding=240.0,
        skip_shared_members=True,
    )
    positions = _separate_nodes(positions)
    positions = _separate_sibling_groups(
        positions,
        containment_groups + proximity_groups + topology_components,
        padding=240.0,
        skip_shared_members=True,
    )
    positions = _enforce_directional_relations(positions, relations, containment_groups)
    positions = _enforce_proximity_relations(positions, proximity_edges, containment_groups)
    directional_ids = {
        int(item["source_entity_id"]) for item in relations if item["relative_position"] in _DIRECTION_VECTORS
    } | {
        int(item["target_entity_id"]) for item in relations if item["relative_position"] in _DIRECTION_VECTORS
    }
    label_placements, node_footprints = _node_label_placements(places, positions, story_locations)
    nodes = [
        {
            "id": int(place["id"]), "name": place["name"],
            "x": positions[int(place["id"])][0], "y": positions[int(place["id"])][1],
            "z": depths[int(place["id"])] * 90,
            "containment_depth": depths[int(place["id"])],
            "parent_id": parent.get(int(place["id"])),
            "coordinate_source": "directional_evidence" if int(place["id"]) in directional_ids else "stable_topology_projection",
            "evidence_level": "explicit" if int(place["id"]) in directional_ids else "semantic",
            "label_placement": label_placements.get(int(place["id"]), {}),
            "occupancy_bbox": node_footprints.get(int(place["id"]), {}),
            "context_only": int(place["first_segment"]) < window_start,
        }
        for place in places
    ]
    name_by_id = {int(place["id"]): str(place["name"]) for place in places}
    first_segment_by_id = {int(place["id"]): int(place["first_segment"]) for place in places}
    importance_by_id = {int(place["id"]): float(place.get("importance") or 0.0) for place in places}
    story_visit_count = Counter(story_locations)
    connection_count: Counter[int] = Counter()
    for source, target in edges:
        connection_count[source] += 1
        connection_count[target] += 1

    def representative_places(member_ids: list[int]) -> list[int]:
        ranked = sorted(
            member_ids,
            key=lambda node_id: (
                -story_visit_count[node_id],
                -importance_by_id.get(node_id, 0.0),
                -connection_count[node_id],
                first_segment_by_id.get(node_id, 1_000_000),
                node_id,
            ),
        )
        return ranked[:2]
    regions = []
    containment_region_ids = {container: f"containment-{container}" for container in children_by_container}
    containment_hulls: dict[int, list[dict[str, float]]] = {}
    containment_triangles: dict[int, list[list[dict[str, float]]]] = {}
    ordered_containers = sorted(children_by_container, key=lambda node_id: (-depths.get(node_id, 0), node_id))
    for index, container in enumerate(ordered_containers):
        members = containment_groups_by_container[container]
        child_hulls = [containment_hulls[child] for child in children_by_container[container] if child in containment_hulls]
        _, hull, surface_triangles, geometry_validation = _region_geometry(
            members,
            node_footprints,
            padding=28.0,
            child_hulls=child_hulls,
        )
        if len(hull) < 3:
            continue
        containment_hulls[container] = hull
        containment_triangles[container] = surface_triangles
        centroid = {
            "x": round(sum(point["x"] for point in hull) / len(hull), 2),
            "y": round(sum(point["y"] for point in hull) / len(hull), 2),
        }
        parent_container = parent.get(container)
        display_name = name_by_id.get(container, f"地点 {container}")
        regions.append({
            "id": containment_region_ids[container],
            "label": display_name,
            "display_name": display_name,
            "representative_node_ids": [container],
            "naming_basis": "explicit_container_name",
            "kind": "evidence_containment",
            "region_kind": "evidence_containment",
            "node_ids": members,
            "member_count": len(members),
            "parent_region_id": containment_region_ids.get(parent_container),
            "containment_depth": depths.get(container, 0),
            "hull": hull,
            "surface_triangles": surface_triangles,
            "geometry_validation": geometry_validation,
            "centroid": centroid,
            "palette_index": index % 6,
            "evidence_ids": sorted(set(relation_ids_by_container[container])),
            "boundary_kind": "semantic",
            "display_policy": "all_views",
            "visibility_reason": "原文包含关系区域在全部地图视角中保留",
            "quality_state": "evidence_backed",
            "formal_geography": False,
            "evidence_level": "explicit",
            "context_only": all(first_segment_by_id.get(node_id, 0) < window_start for node_id in members),
            "volume": {
                "z_min": min((depths.get(node_id, 0) * 90 for node_id in members), default=0) - 28,
                "z_max": max((depths.get(node_id, 0) * 90 for node_id in members), default=0) + 28,
                "min_depth": min((depths.get(node_id, 0) for node_id in members), default=0),
                "max_depth": max((depths.get(node_id, 0) for node_id in members), default=0),
                "quality_state": "verified" if geometry_validation["valid"] else "failed",
            },
        })
    for index, component in enumerate(proximity_groups):
        _, hull, surface_triangles, geometry_validation = _region_geometry(
            component,
            node_footprints,
            padding=24.0,
        )
        if len(hull) < 3:
            continue
        centroid = {
            "x": round(sum(point["x"] for point in hull) / len(hull), 2),
            "y": round(sum(point["y"] for point in hull) / len(hull), 2),
        }
        representatives = representative_places(component)
        display_name = "—".join(name_by_id[node_id] for node_id in representatives)
        component_ids = set(component)
        evidence_ids = sorted({
            int(relation["id"])
            for relation in relations
            if relation["relative_position"] == "near"
            and int(relation["source_entity_id"]) in component_ids
            and int(relation["target_entity_id"]) in component_ids
        })
        regions.append({
            "id": f"proximity-{index + 1}",
            "label": display_name,
            "display_name": display_name,
            "representative_node_ids": representatives,
            "naming_basis": "explicit_proximity_places",
            "kind": "evidence_proximity",
            "region_kind": "evidence_proximity",
            "node_ids": component,
            "member_count": len(component),
            "parent_region_id": None,
            "containment_depth": 0,
            "hull": hull,
            "surface_triangles": surface_triangles,
            "geometry_validation": geometry_validation,
            "centroid": centroid,
            "palette_index": (len(ordered_containers) + index) % 6,
            "evidence_ids": evidence_ids,
            "boundary_kind": "semantic",
            "display_policy": "all_views",
            "visibility_reason": "原文明示相邻的地点在全部地图视角中共同保留",
            "quality_state": "evidence_backed",
            "formal_geography": False,
            "evidence_level": "explicit",
            "context_only": all(first_segment_by_id.get(node_id, 0) < window_start for node_id in component),
            "volume": {
                "z_min": -22,
                "z_max": 22,
                "min_depth": 0,
                "max_depth": 0,
                "quality_state": "verified" if geometry_validation["valid"] else "failed",
            },
        })
    for index, component in enumerate(topology_components):
        _, hull, surface_triangles, geometry_validation = _region_geometry(
            component,
            node_footprints,
            padding=22.0,
        )
        if len(hull) >= 3:
            centroid = {
                "x": round(sum(point["x"] for point in hull) / len(hull), 2),
                "y": round(sum(point["y"] for point in hull) / len(hull), 2),
            }
            representatives = representative_places(component)
            display_name = "—".join(name_by_id[node_id] for node_id in representatives)
            regions.append({
                "id": f"topology-{index + 1}",
                "label": display_name,
                "display_name": display_name,
                "representative_node_ids": representatives,
                "naming_basis": "representative_story_places",
                "kind": "topological_cluster",
                "region_kind": "story_cluster",
                "node_ids": component,
                "member_count": len(component),
                "parent_region_id": None,
                "containment_depth": 0,
                "hull": hull,
                "surface_triangles": surface_triangles,
                "geometry_validation": geometry_validation,
                "centroid": centroid,
                "palette_index": index % 6,
                "evidence_ids": [],
                "boundary_kind": "semantic",
                "display_policy": "all_views",
                "visibility_reason": "故事组织区域在全世界视角中完整展示",
                "quality_state": "semantic_only",
                "formal_geography": False,
                "evidence_level": "semantic",
                "context_only": all(first_segment_by_id.get(node_id, 0) < window_start for node_id in component),
                "volume": {
                    "z_min": -18,
                    "z_max": 18,
                    "min_depth": 0,
                    "max_depth": 0,
                    "quality_state": "verified" if geometry_validation["valid"] else "failed",
                },
            })
    occupied_label_boxes: list[dict[str, float]] = []
    region_hulls = {str(region["id"]): list(region["hull"]) for region in regions}
    parent_by_region = {str(region["id"]): region.get("parent_region_id") for region in regions}
    children_by_region: dict[str, set[str]] = defaultdict(set)
    for region_id, parent_region_id in parent_by_region.items():
        if parent_region_id:
            children_by_region[str(parent_region_id)].add(region_id)

    def ancestor_region_ids(region_id: str) -> set[str]:
        result = {region_id}
        current = parent_by_region.get(region_id)
        while current and str(current) not in result:
            result.add(str(current))
            current = parent_by_region.get(str(current))
        return result

    for region in sorted(regions, key=lambda item: (-int(item["containment_depth"]), str(item["id"]))):
        anchor, connector = _region_label_geometry(
            str(region["id"]),
            str(region["display_name"]),
            list(region["hull"]),
            [int(node_id) for node_id in region["node_ids"]],
            positions,
            occupied_label_boxes,
            node_footprints,
            region_hulls,
            ancestor_region_ids(str(region["id"])),
            children_by_region.get(str(region["id"]), set()),
        )
        region["label_anchor"] = anchor
        region["label_connector"] = connector

    assigned_node_ids = {
        int(node_id)
        for region in regions
        for node_id in region.get("node_ids", [])
    }
    unassigned_node_ids = sorted(valid_ids - assigned_node_ids)
    overlap_summary = _region_overlap_summary(regions)
    quality_issues = []
    geometry_failures = [region for region in regions if not region.get("geometry_validation", {}).get("valid")]
    constraint_summary, failed_constraints = _validate_map_constraints(
        positions,
        relations,
        regions,
        overlap_summary,
        [str(region["id"]) for region in geometry_failures],
        len(journey),
    )
    validation_state = "valid" if not failed_constraints else "invalid"
    if geometry_failures:
        quality_issues.append({
            "issue_type": "region_containment",
            "severity": "blocking",
            "count": len(geometry_failures),
            "region_ids": [str(region["id"]) for region in geometry_failures],
            "message": f"有 {len(geometry_failures)} 个区域没有通过节点和文字完整包裹检查",
        })
    if unassigned_node_ids:
        quality_issues.append({
            "issue_type": "unassigned_places",
            "severity": "warning",
            "count": len(unassigned_node_ids),
            "node_ids": unassigned_node_ids,
            "message": f"有 {len(unassigned_node_ids)} 个地点尚未归入任何语义区域",
        })
    if overlap_summary["same_level_overlap_pairs"]:
        quality_issues.append({
            "issue_type": "region_overlap",
            "severity": "blocking",
            "count": overlap_summary["same_level_overlap_pairs"],
            "message": "无包含关系的区域仍有重叠，语义世界图已停止发布",
        })
    relation_failures = [item for item in failed_constraints if item.get("relation_id") is not None]
    if relation_failures:
        quality_issues.append({
            "issue_type": "map_evidence_conflict",
            "severity": "blocking",
            "count": len(relation_failures),
            "relation_ids": sorted({int(item["relation_id"]) for item in relation_failures}),
            "message": "地图位置与原文明示的地点关系冲突，已退回证据逻辑图",
        })

    label_padding_x = 88.0
    label_padding_y = 54.0
    min_world_x = min((node["x"] - label_padding_x for node in nodes), default=0.0)
    max_world_x = max((node["x"] + label_padding_x for node in nodes), default=1080.0)
    min_world_y = min((node["y"] - label_padding_y for node in nodes), default=0.0)
    max_world_y = max((node["y"] + label_padding_y for node in nodes), default=700.0)
    region_label_boxes = [region["label_anchor"]["bbox"] for region in regions if region.get("label_anchor")]
    region_hull_points = [point for region in regions for point in region.get("hull", [])]
    min_world_x = min([min_world_x, *(float(box["min_x"]) - 20.0 for box in region_label_boxes), *(float(point["x"]) - 20.0 for point in region_hull_points)])
    max_world_x = max([max_world_x, *(float(box["max_x"]) + 20.0 for box in region_label_boxes), *(float(point["x"]) + 20.0 for point in region_hull_points)])
    min_world_y = min([min_world_y, *(float(box["min_y"]) - 20.0 for box in region_label_boxes), *(float(point["y"]) - 20.0 for point in region_hull_points)])
    max_world_y = max([max_world_y, *(float(box["max_y"]) + 20.0 for box in region_label_boxes), *(float(point["y"]) + 20.0 for point in region_hull_points)])
    topology_regions = {tuple(region["node_ids"]): region for region in regions if region["kind"] == "topological_cluster"}
    aggregates = [
        {
            "id": f"aggregate-{index + 1}",
            "label": topology_regions.get(tuple(component), {}).get("display_name") or name_by_id.get(component[0], "地点组"),
            "member_node_ids": component,
            "x": round(sum(positions[item][0] for item in component) / len(component), 2),
            "y": round(sum(positions[item][1] for item in component) / len(component), 2),
            "count": len(component),
        }
        for index, component in enumerate(components) if component
    ]
    main_route_nodes = list(dict.fromkeys(story_locations))
    payload = {
        "layout_version": LAYOUT_VERSION,
        "through_segment": boundary,
        "from_segment": window_start,
        "stable_seed": hashlib.sha256(f"atlas:{book_id}".encode()).hexdigest()[:16],
        "source_hash": source_hash,
        "fact_source": "overview.story_map_steps",
        "validation_state": validation_state,
        "constraint_summary": constraint_summary,
        "failed_relations": relation_failures,
        "failed_constraints": failed_constraints,
        "nodes": nodes,
        "node_footprints": {str(node_id): footprint for node_id, footprint in node_footprints.items()},
        "regions": regions,
        "region_coverage": {
            "total_place_count": len(nodes),
            "assigned_place_count": len(nodes) - len(unassigned_node_ids),
            "unassigned_place_count": len(unassigned_node_ids),
            "generated_region_count": len(regions),
            "evidence_region_count": sum(1 for region in regions if str(region["kind"]).startswith("evidence_")),
            "proximity_region_count": sum(1 for region in regions if region["kind"] == "evidence_proximity"),
            "story_region_count": sum(1 for region in regions if region["kind"] == "topological_cluster"),
            "visible_region_count": len(regions),
            "hidden_reasons": [],
            "overlap": overlap_summary,
            "geometry_verified_count": len(regions) - len(geometry_failures),
            "geometry_failed_count": len(geometry_failures),
        },
        "unassigned_node_ids": unassigned_node_ids,
        "quality_issues": quality_issues,
        "world_bounds": {
            "min_x": round(min_world_x, 2), "min_y": round(min_world_y, 2),
            "max_x": round(max_world_x, 2), "max_y": round(max_world_y, 2),
            "width": round(max_world_x - min_world_x, 2),
            "height": round(max_world_y - min_world_y, 2),
        },
        "detail_levels": {
            "low": {"aggregates": aggregates, "node_ids": main_route_nodes},
            "medium": {"aggregates": [], "node_ids": sorted(set(main_route_nodes + [item for component in components[:8] for item in component]))},
            "high": {"aggregates": [], "node_ids": node_ids},
        },
        "label_budget": {"low": 18, "medium": 52, "high": 180},
        "routes": [
            {"source": source, "target": target, "kind": "semantic_topology"}
            for source, target in sorted(set(edges))
        ],
        "warnings": [
            "语义区域用于整理拓扑，不代表原文明示的真实边界",
            "stable_topology_projection 坐标只用于稳定展示，不代表东南西北",
        ],
    }
    connection.execute(
        """
        INSERT INTO map_layout_snapshots(book_id, layout_version, stable_seed, source_hash, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (book_id, LAYOUT_VERSION, payload["stable_seed"], source_hash, json.dumps(payload, ensure_ascii=False)),
    )
    return payload
