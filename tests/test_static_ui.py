"""前端合同测试：阻止地图双视图和发布版本重新漂移。"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_release_version_is_consistent_across_state_api_and_page() -> None:
    state = json.loads(_text("docs/current-state.json"))
    release = re.escape(state["release"])
    assert re.search(rf'version\s*=\s*"{release}"', _text("pyproject.toml"))
    assert f'Novel Atlas · {state["release"]}' in _text("static/index.html")
    assert f'version="{state["release"]}"' in _text("app/main.py")


def test_map_2d_and_3d_share_one_story_step_state() -> None:
    script = _text("static/app.js")
    assert script.count("function storyMapSteps()") == 1
    assert 'data-mode="2d"' in script
    assert 'data-mode="3d"' in script
    assert "createMapGraph3D(locations, geographyRelations, routeTopology, journey, routeByEventId, points)" in script
    assert "syncMap3DStep(event, visibleLocationIds, step, animate)" in script
    assert "state.mapStep = step" in script
    assert "window.localStorage.setItem(\"novel-atlas-map-mode\", nextMode)" in script


def test_map_3d_does_not_invent_random_geography() -> None:
    script = _text("static/app.js")
    start = script.index("function mapContainmentDepths")
    end = script.index("function renderMapLocationDetails", start)
    implementation = script[start:end]
    assert "Math.random" not in implementation
    assert 'relation.relative_position === "inside"' in implementation
    assert 'relation.relative_position === "contains"' in implementation
    assert "point.x - centerX" in implementation
    assert "point.y - centerY" in implementation


def test_map_without_direction_uses_stable_semantic_projection_instead_of_grid() -> None:
    script = _text("static/app.js")
    assert "function chronologySchematicLayout" not in script
    assert "function stableTopologyFallback(locations)" in script
    assert "state.mapLayout?.nodes" in script
    assert "stable_topology_projection" in script
    assert 'data-presentation="atlas"' in script
    assert 'data-presentation="evidence"' in script


def test_data_canvases_share_semantic_colors_across_2d_and_3d() -> None:
    script = _text("static/app.js")
    styles = _text("static/styles.css")
    assert "const semanticPalette" in script
    assert "semanticPalette.place" in script
    assert "semanticPalette.current" in script
    assert "--data-person: #0f6cbd" in styles
    assert "--data-place: #4856a6" in styles
    assert ".semantic-region.region-0" in styles


def test_rapid_navigation_keeps_separate_live_positions_for_both_renderers() -> None:
    script = _text("static/app.js")
    assert "state.mapMarkerPoint = progress < 1 ? { x, y } : target" in script
    assert "const start = state.mapMarkerPoint3D" in script
    assert "state.mapMarkerPoint3D = point" in script
    assert script.count("cancelAnimationFrame(state.mapAnimationFrame)") >= 4


def test_monochrome_ui_has_large_controls_and_visible_focus() -> None:
    styles = _text("static/styles.css")
    assert "--ink: #111111" in styles
    assert ".button { min-height: 40px" in styles
    assert "outline: 3px solid #ffffff" in styles
    assert "box-shadow: 0 0 0 5px #111111" in styles
    assert ".map-3d-shell" in styles
