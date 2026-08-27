"""Evidence-preserving semantic atlas projection for 2D and 3D views."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict, deque
from typing import Any


LAYOUT_VERSION = "semantic-atlas-v2.3"
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
            desired_x = target_x + vector_x * 180.0
            desired_y = target_y + vector_y * 150.0
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


def build_map_layout_snapshot(
    connection: sqlite3.Connection,
    book_id: int,
    through_segment: int | None = None,
) -> dict[str, Any]:
    boundary = 1_000_000 if through_segment is None else max(0, through_segment)
    places = [dict(row) for row in connection.execute(
        """
        SELECT id, name, first_segment FROM entities
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
    source_payload = {"places": places, "relations": relations, "journey": journey, "story_steps": story_steps}
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
    components = _semantic_region_groups(node_ids, edges, story_locations)
    positions = _layout_nodes(book_id, node_ids, edges, relations, components)
    depths, parent = _containment_depths(node_ids, relations)
    directional_ids = {
        int(item["source_entity_id"]) for item in relations if item["relative_position"] in _DIRECTION_VECTORS
    } | {
        int(item["target_entity_id"]) for item in relations if item["relative_position"] in _DIRECTION_VECTORS
    }
    nodes = [
        {
            "id": int(place["id"]), "name": place["name"],
            "x": positions[int(place["id"])][0], "y": positions[int(place["id"])][1],
            "z": depths[int(place["id"])] * 90,
            "containment_depth": depths[int(place["id"])],
            "parent_id": parent.get(int(place["id"])),
            "coordinate_source": "directional_evidence" if int(place["id"]) in directional_ids else "stable_topology_projection",
            "evidence_level": "explicit" if int(place["id"]) in directional_ids else "semantic",
        }
        for place in places
    ]
    regions = []
    for index, component in enumerate(components):
        if len(component) < 3:
            continue
        hull = _expanded_hull([positions[node_id] for node_id in component], padding=36.0)
        if len(hull) >= 3:
            regions.append({
                "id": f"semantic-{index + 1}",
                "label": f"故事拓扑片区 {index + 1}",
                "kind": "topological_cluster",
                "node_ids": component,
                "hull": hull,
                "formal_geography": False,
                "evidence_level": "semantic",
            })
    payload = {
        "layout_version": LAYOUT_VERSION,
        "through_segment": boundary,
        "stable_seed": hashlib.sha256(f"atlas:{book_id}".encode()).hexdigest()[:16],
        "source_hash": source_hash,
        "fact_source": "overview.story_map_steps",
        "nodes": nodes,
        "regions": regions,
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
