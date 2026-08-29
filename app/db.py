"""SQLite 数据层与证据优先的规范化表结构。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS library_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES library_folders(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(parent_id, name)
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    folder_id INTEGER REFERENCES library_folders(id) ON DELETE SET NULL,
    segment_count INTEGER NOT NULL DEFAULT 0,
    character_count INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT '',
    corpus_kind TEXT NOT NULL DEFAULT 'user_upload',
    license_name TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    rights_status TEXT NOT NULL DEFAULT 'user_supplied',
    source_sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    chapter_title TEXT NOT NULL,
    anchor TEXT NOT NULL,
    text TEXT NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    UNIQUE(book_id, ordinal),
    UNIQUE(book_id, anchor)
);

CREATE TABLE IF NOT EXISTS book_update_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    filename TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    proposed_title TEXT NOT NULL,
    proposed_author TEXT NOT NULL DEFAULT '',
    previous_segment_count INTEGER NOT NULL,
    added_segment_count INTEGER NOT NULL DEFAULT 0,
    common_prefix_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'previewed',
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL,
    resolution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS maintenance_runs (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    repair_key TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(book_id, repair_key)
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    importance REAL NOT NULL DEFAULT 0.5,
    first_segment INTEGER NOT NULL DEFAULT 0,
    x REAL,
    y REAL,
    created_by TEXT NOT NULL DEFAULT 'model',
    UNIQUE(book_id, kind, name)
);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    UNIQUE(entity_id, alias)
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL,
    directionality TEXT NOT NULL DEFAULT 'directed',
    reverse_predicate TEXT,
    temporal_scope TEXT NOT NULL DEFAULT 'current',
    summary TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'unreviewed',
    first_segment INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'model',
    UNIQUE(book_id, source_entity_id, target_entity_id, predicate, first_segment)
);

CREATE TABLE IF NOT EXISTS place_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relative_position TEXT NOT NULL,
    summary TEXT NOT NULL,
    confidence REAL NOT NULL,
    first_segment INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'model',
    UNIQUE(book_id, source_entity_id, target_entity_id, relative_position, first_segment)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    narrative_order INTEGER NOT NULL,
    story_order REAL NOT NULL,
    temporal_kind TEXT NOT NULL,
    temporal_value TEXT NOT NULL DEFAULT '',
    temporal_start TEXT,
    temporal_end TEXT,
    confidence REAL NOT NULL,
    location_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    transport TEXT NOT NULL DEFAULT '',
    first_segment INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'model',
    UNIQUE(book_id, title, narrative_order, first_segment)
);

CREATE TABLE IF NOT EXISTS event_participants (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    PRIMARY KEY(event_id, entity_id, role)
);

CREATE TABLE IF NOT EXISTS world_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    confidence REAL NOT NULL,
    first_segment INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'model',
    archived_at TEXT,
    UNIQUE(book_id, category, title, first_segment)
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL,
    first_segment INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'model',
    UNIQUE(book_id, category, name, first_segment)
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    quote TEXT NOT NULL,
    quote_start INTEGER NOT NULL,
    quote_end INTEGER NOT NULL,
    run_manifest_id INTEGER REFERENCES run_manifests(id) ON DELETE SET NULL,
    model_call_id INTEGER REFERENCES model_call_ledger(id) ON DELETE SET NULL,
    UNIQUE(target_type, target_id, segment_id, quote_start, quote_end)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    start_segment INTEGER NOT NULL DEFAULT 0,
    segments_requested INTEGER NOT NULL,
    segments_succeeded INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entity_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'model',
    UNIQUE(book_id, kind, normalized_name)
);

CREATE TABLE IF NOT EXISTS entity_merges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    kept_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    removed_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entity_merge_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    left_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    right_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'unreviewed',
    resolution_reason TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, left_entity_id, right_entity_id, reason)
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    start_segment INTEGER NOT NULL DEFAULT 0,
    end_segment INTEGER NOT NULL,
    total_segments INTEGER NOT NULL DEFAULT 0,
    completed_segments INTEGER NOT NULL DEFAULT 0,
    failed_segments INTEGER NOT NULL DEFAULT 0,
    accepted_facts INTEGER NOT NULL DEFAULT 0,
    rejected_facts INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_usd_per_million REAL,
    cache_miss_input_usd_per_million REAL,
    output_usd_per_million REAL,
    estimated_cost_usd REAL,
    pricing_source TEXT NOT NULL DEFAULT '',
    pricing_effective_date TEXT NOT NULL DEFAULT '',
    max_retries INTEGER NOT NULL DEFAULT 3,
    prompt_version TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS analysis_job_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    accepted_facts INTEGER NOT NULL DEFAULT 0,
    rejected_facts INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, segment_id)
);

CREATE TABLE IF NOT EXISTS analysis_job_review_usage (
    job_id INTEGER PRIMARY KEY REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    batches INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS analysis_job_quality_usage (
    job_id INTEGER PRIMARY KEY REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS segment_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0,
    job_id INTEGER REFERENCES analysis_jobs(id) ON DELETE SET NULL,
    run_manifest_id INTEGER REFERENCES run_manifests(id) ON DELETE SET NULL,
    model_call_id INTEGER REFERENCES model_call_ledger(id) ON DELETE SET NULL,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, segment_id, provider, model, prompt_version)
);

CREATE TABLE IF NOT EXISTS book_memory (
    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    through_segment INTEGER NOT NULL DEFAULT -1,
    summary TEXT NOT NULL DEFAULT '',
    open_threads_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_order_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    earlier_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    later_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    created_by TEXT NOT NULL DEFAULT 'model',
    status TEXT NOT NULL DEFAULT 'accepted',
    reason TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    resolution_reason TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    UNIQUE(earlier_event_id, later_event_id, relation)
);

CREATE TABLE IF NOT EXISTS book_settings (
    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    protagonist_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    auto_protagonist INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS synthesis_basis (
    world_note_id INTEGER NOT NULL REFERENCES world_notes(id) ON DELETE CASCADE,
    basis_type TEXT NOT NULL,
    basis_id INTEGER NOT NULL,
    PRIMARY KEY(world_note_id, basis_type, basis_id)
);

CREATE TABLE IF NOT EXISTS contradictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    left_type TEXT NOT NULL,
    left_id INTEGER NOT NULL,
    right_type TEXT NOT NULL,
    right_id INTEGER NOT NULL,
    summary TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'unreviewed',
    resolution_reason TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, left_type, left_id, right_type, right_id, summary)
);

CREATE TABLE IF NOT EXISTS global_review_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    batch_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    completed_at TEXT,
    UNIQUE(book_id, batch_hash, provider, model, prompt_version)
);

CREATE TABLE IF NOT EXISTS identity_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    canonical_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    canonical_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    merged_into_cluster_id INTEGER REFERENCES identity_clusters(id) ON DELETE SET NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    locked_subject INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS identity_cluster_members (
    cluster_id INTEGER NOT NULL REFERENCES identity_clusters(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL UNIQUE REFERENCES entities(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'system',
    confidence REAL NOT NULL DEFAULT 1.0,
    decision_id INTEGER,
    PRIMARY KEY(cluster_id, entity_id)
);

CREATE TABLE IF NOT EXISTS identity_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    left_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    right_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    contradictions_json TEXT NOT NULL DEFAULT '[]',
    left_cluster_id INTEGER,
    right_cluster_id INTEGER,
    moved_entity_ids_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    undone_at TEXT,
    UNIQUE(book_id, left_entity_id, right_entity_id, verdict, created_at)
);

CREATE TABLE IF NOT EXISTS journey_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    subject_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    from_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    to_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    ordinal INTEGER NOT NULL,
    transport TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL,
    gap_status TEXT NOT NULL DEFAULT 'complete',
    confidence REAL NOT NULL DEFAULT 0.5,
    first_segment INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'model',
    UNIQUE(book_id, subject_entity_id, from_entity_id, to_entity_id, ordinal, first_segment)
);

CREATE TABLE IF NOT EXISTS record_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS generation_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    instruction TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    title_value TEXT NOT NULL,
    summary_value TEXT NOT NULL,
    category_value TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS extraction_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_call_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES analysis_jobs(id) ON DELETE SET NULL,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS relationship_layouts (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    z REAL NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(book_id, entity_id, mode)
);

CREATE TABLE IF NOT EXISTS quality_benchmark_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    case_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    actual_json TEXT NOT NULL DEFAULT '{}',
    passed INTEGER,
    critical INTEGER NOT NULL DEFAULT 1,
    source_segment INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, case_type, subject, source_segment)
);

CREATE TABLE IF NOT EXISTS benchmark_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    case_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    source_segment INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    critical INTEGER NOT NULL DEFAULT 1,
    candidate_origin TEXT NOT NULL DEFAULT 'evidence_index',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    accepted_benchmark_id INTEGER REFERENCES quality_benchmark_cases(id) ON DELETE SET NULL,
    resolution_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    UNIQUE(book_id, case_type, subject, source_segment)
);

CREATE TABLE IF NOT EXISTS entity_connectivity_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    mention_count INTEGER NOT NULL DEFAULT 0,
    scanned_segment_count INTEGER NOT NULL DEFAULT 0,
    source_segment_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    review_method TEXT NOT NULL DEFAULT 'deterministic',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, entity_id)
);

CREATE TABLE IF NOT EXISTS event_location_reviews (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'unresolved',
    effective_location_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quality_gate_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES analysis_jobs(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quality_audit_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    quality_json TEXT NOT NULL DEFAULT '{}',
    exclusions_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    promoted_at TEXT
);

CREATE TABLE IF NOT EXISTS collaboration_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
    original_text TEXT NOT NULL,
    interpreted_goal TEXT NOT NULL,
    acceptance_json TEXT NOT NULL DEFAULT '[]',
    impact_json TEXT NOT NULL DEFAULT '[]',
    estimated_cost_change_percent REAL NOT NULL DEFAULT 0,
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'interpreted',
    regression_case_id INTEGER REFERENCES quality_benchmark_cases(id) ON DELETE SET NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_bundle_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    core_text TEXT NOT NULL,
    task_text TEXT NOT NULL DEFAULT '',
    change_note TEXT NOT NULL DEFAULT '',
    parent_id INTEGER REFERENCES prompt_bundle_versions(id) ON DELETE SET NULL,
    prompt_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    promoted_at TEXT,
    UNIQUE(task_key, version)
);

CREATE TABLE IF NOT EXISTS domain_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
    task_key TEXT NOT NULL DEFAULT 'all',
    statement TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    examples_json TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 100,
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS external_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    statement TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
    job_id INTEGER REFERENCES analysis_jobs(id) ON DELETE SET NULL,
    run_kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    auth_mode TEXT NOT NULL DEFAULT 'api',
    contract_version TEXT NOT NULL,
    prompt_bundle_id INTEGER REFERENCES prompt_bundle_versions(id) ON DELETE SET NULL,
    prompt_hash TEXT NOT NULL,
    domain_rule_hash TEXT NOT NULL,
    external_fact_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    eval_suite_version TEXT NOT NULL,
    input_scope_json TEXT NOT NULL DEFAULT '{}',
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    validation_json TEXT NOT NULL DEFAULT '{}',
    conflict_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS model_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    eligible INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    circuit_open_until TEXT,
    benchmark_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_race_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    total_cases INTEGER NOT NULL DEFAULT 0,
    passed_cases INTEGER NOT NULL DEFAULT 0,
    critical_failures INTEGER NOT NULL DEFAULT 0,
    accuracy_percent REAL,
    evidence_percent REAL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    eligible INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS map_layout_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    layout_version TEXT NOT NULL,
    stable_seed TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, layout_version, source_hash)
);

CREATE TABLE IF NOT EXISTS event_narrative_frames (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    cause TEXT NOT NULL DEFAULT '',
    trigger_text TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    state_changes_json TEXT NOT NULL DEFAULT '[]',
    open_threads_json TEXT NOT NULL DEFAULT '[]',
    resolved_threads_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'model',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_causal_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    target_event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'accepted',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'model',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, source_event_id, target_event_id, relation)
);

CREATE TABLE IF NOT EXISTS character_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    through_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    location_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    goal TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL DEFAULT '[]',
    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, entity_id)
);

CREATE TABLE IF NOT EXISTS open_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    thread_key TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    opened_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    resolved_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, thread_key)
);

CREATE TABLE IF NOT EXISTS arc_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    arc_key TEXT NOT NULL,
    start_segment INTEGER NOT NULL,
    end_segment INTEGER NOT NULL,
    summary TEXT NOT NULL,
    event_ids_json TEXT NOT NULL DEFAULT '[]',
    source_hash TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'local',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, arc_key, source_hash)
);

CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    scheme TEXT NOT NULL DEFAULT 'book',
    category TEXT NOT NULL,
    preferred_label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    custom INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL DEFAULT 'migration',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, scheme, category, preferred_label)
);

CREATE TABLE IF NOT EXISTS concept_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    source_concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    target_concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'migration',
    UNIQUE(book_id, source_concept_id, target_concept_id, relation)
);

CREATE TABLE IF NOT EXISTS knowledge_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL,
    subject_id INTEGER NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    confidence REAL NOT NULL DEFAULT 0.5,
    source_kind TEXT NOT NULL DEFAULT 'original_text',
    created_by TEXT NOT NULL DEFAULT 'migration',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, subject_type, subject_id, predicate, value_json)
);

CREATE TABLE IF NOT EXISTS claim_qualifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_claim_id INTEGER NOT NULL REFERENCES knowledge_claims(id) ON DELETE CASCADE,
    qualifier_key TEXT NOT NULL,
    qualifier_value_json TEXT NOT NULL,
    UNIQUE(knowledge_claim_id, qualifier_key, qualifier_value_json)
);

CREATE TABLE IF NOT EXISTS knowledge_claim_evidence (
    knowledge_claim_id INTEGER NOT NULL REFERENCES knowledge_claims(id) ON DELETE CASCADE,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    PRIMARY KEY(knowledge_claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS knowledge_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS world_systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    structure_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, name, category)
);

CREATE TABLE IF NOT EXISTS world_system_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id INTEGER NOT NULL REFERENCES world_systems(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    rank_value REAL,
    concept_id INTEGER REFERENCES concepts(id) ON DELETE SET NULL,
    evidence_id INTEGER REFERENCES evidence(id) ON DELETE SET NULL,
    effective_from_segment INTEGER NOT NULL DEFAULT 0,
    effective_to_segment INTEGER,
    confidence REAL NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'accepted',
    created_by TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(system_id, label)
);

CREATE TABLE IF NOT EXISTS world_system_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id INTEGER NOT NULL REFERENCES world_systems(id) ON DELETE CASCADE,
    source_node_id INTEGER NOT NULL REFERENCES world_system_nodes(id) ON DELETE CASCADE,
    target_node_id INTEGER NOT NULL REFERENCES world_system_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    evidence_id INTEGER REFERENCES evidence(id) ON DELETE SET NULL,
    confidence REAL NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'accepted',
    created_by TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(system_id, source_node_id, target_node_id, relation_type)
);

CREATE TABLE IF NOT EXISTS knowledge_completion_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    concept_id INTEGER REFERENCES concepts(id) ON DELETE CASCADE,
    instruction TEXT NOT NULL,
    segment_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ui_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_key TEXT NOT NULL,
    viewport TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    reproduction TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT NOT NULL DEFAULT '',
    regression_test TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS feature_flags (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    feature_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(book_id, feature_key)
);

CREATE INDEX IF NOT EXISTS idx_segments_book ON segments(book_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_library_folders_parent ON library_folders(parent_id, sort_order, name);
CREATE INDEX IF NOT EXISTS idx_book_updates_book ON book_update_batches(book_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_entities_book ON entities(book_id, kind, first_segment);
CREATE INDEX IF NOT EXISTS idx_claims_book ON claims(book_id, first_segment, status);
CREATE INDEX IF NOT EXISTS idx_place_relations_book ON place_relations(book_id, first_segment);
CREATE INDEX IF NOT EXISTS idx_events_book ON events(book_id, first_segment, narrative_order);
CREATE INDEX IF NOT EXISTS idx_evidence_target ON evidence(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_entity_keys_lookup ON entity_keys(book_id, kind, normalized_name);
CREATE INDEX IF NOT EXISTS idx_merge_candidates_book ON entity_merge_candidates(book_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON analysis_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_job_segments_status ON analysis_job_segments(job_id, status, ordinal);
CREATE INDEX IF NOT EXISTS idx_segment_results_book ON segment_results(book_id, segment_id);
CREATE INDEX IF NOT EXISTS idx_event_edges_book ON event_order_edges(book_id, earlier_event_id, later_event_id);
CREATE INDEX IF NOT EXISTS idx_contradictions_book ON contradictions(book_id, status);
CREATE INDEX IF NOT EXISTS idx_global_reviews_book ON global_review_batches(book_id, status);
CREATE INDEX IF NOT EXISTS idx_identity_clusters_book ON identity_clusters(book_id, status, kind);
CREATE INDEX IF NOT EXISTS idx_identity_decisions_book ON identity_decisions(book_id, verdict);
CREATE INDEX IF NOT EXISTS idx_journey_legs_book ON journey_legs(book_id, ordinal, first_segment);
CREATE INDEX IF NOT EXISTS idx_record_versions_target ON record_versions(target_type, target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_generation_drafts_target ON generation_drafts(target_type, target_id, status);
CREATE INDEX IF NOT EXISTS idx_model_call_ledger_book ON model_call_ledger(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quality_benchmark_book ON quality_benchmark_cases(book_id, case_type, critical);
CREATE INDEX IF NOT EXISTS idx_connectivity_reviews_book ON entity_connectivity_reviews(book_id, status);
CREATE INDEX IF NOT EXISTS idx_location_reviews_book ON event_location_reviews(book_id, status);
CREATE INDEX IF NOT EXISTS idx_quality_snapshots_book ON quality_gate_snapshots(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_collaboration_book ON collaboration_items(book_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_prompt_bundles_task ON prompt_bundle_versions(task_key, status, id);
CREATE INDEX IF NOT EXISTS idx_domain_rules_book ON domain_rules(book_id, task_key, active, priority);
CREATE INDEX IF NOT EXISTS idx_external_facts_book ON external_facts(book_id, active);
CREATE INDEX IF NOT EXISTS idx_run_manifests_book ON run_manifests(book_id, started_at);
CREATE INDEX IF NOT EXISTS idx_model_races_book ON model_race_runs(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_map_layout_book ON map_layout_snapshots(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_narrative_frames_book ON event_narrative_frames(book_id, event_id);
CREATE INDEX IF NOT EXISTS idx_causal_links_book ON event_causal_links(book_id, source_event_id, target_event_id);
CREATE INDEX IF NOT EXISTS idx_character_states_book ON character_states(book_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_open_threads_book ON open_threads(book_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_arc_memories_book ON arc_memories(book_id, start_segment, end_segment);
CREATE INDEX IF NOT EXISTS idx_concepts_book ON concepts(book_id, category, preferred_label);
CREATE INDEX IF NOT EXISTS idx_concept_relations_book ON concept_relations(book_id, source_concept_id, relation);
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_book ON knowledge_claims(book_id, concept_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_revisions_target ON knowledge_revisions(book_id, target_type, target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_world_systems_book ON world_systems(book_id, status, category);
CREATE INDEX IF NOT EXISTS idx_world_system_nodes_system ON world_system_nodes(system_id, status, rank_value);
CREATE INDEX IF NOT EXISTS idx_world_system_relations_system ON world_system_relations(system_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_completion_book ON knowledge_completion_requests(book_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_ui_issues_status ON ui_issues(status, severity, page_key);
"""


def connect(path: Path) -> sqlite3.Connection:
    """打开启用外键与行对象的连接。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: Path) -> None:
    """创建数据库表，并把中断任务恢复到可继续或可重试状态。"""

    with connect(path) as connection:
        connection.executescript(SCHEMA)
        _migrate_cost_columns(connection)
        _migrate_semantic_columns(connection)
        _migrate_library_columns(connection)
        _migrate_quality_harness(connection)
        _migrate_control_plane(connection)
        _migrate_v27(connection)
        _migrate_v28(connection)
        _migrate_v29(connection)
        _migrate_v291(connection)
        _repair_derived_self_routes(connection)
        _repair_mismatched_evidence_segments(connection)
        connection.execute(
            """
            UPDATE analysis_jobs
            SET status = 'needs_review', quality_gate_status = 'needs_review',
                error = '结构检查已完成；真实模型结果缺少至少 20 条人工金标准，尚不能承诺 95% 准确率',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'completed' AND provider NOT IN ('mock', 'demo')
              AND (
                SELECT COUNT(*) FROM quality_benchmark_cases benchmark
                WHERE benchmark.book_id = analysis_jobs.book_id
                  AND benchmark.confirmed_by_user = 1
                  AND benchmark.review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
              ) < 20
            """
        )
        from app.control_plane import ensure_control_plane_defaults

        ensure_control_plane_defaults(connection)
        connection.execute(
            """
            UPDATE analysis_jobs SET status = 'queued', error = '应用重启后自动续跑', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
            """
        )
        connection.execute(
            """
            UPDATE analysis_jobs SET status = 'needs_review', quality_gate_status = 'needs_review',
                error = '质量复审被应用重启中断，可直接自动重试或人工解决', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'quality_checking'
            """
        )


def _migrate_v27(connection: sqlite3.Connection) -> None:
    """无损建立 2.7 派生层；旧表继续作为一个版本周期内的兼容来源。"""

    features = ("atlas_v2", "narrative_memory_v2", "knowledge_v2")
    for book in connection.execute("SELECT id FROM books"):
        book_id = int(book["id"])
        for feature in features:
            connection.execute(
                "INSERT OR IGNORE INTO feature_flags(book_id, feature_key, enabled) VALUES (?, ?, 1)",
                (book_id, feature),
            )

    # 旧事件至少保留一个可读行动；未抽取到的前因、目标和结果保持为空。
    connection.execute(
        """
        INSERT OR IGNORE INTO event_narrative_frames(event_id, book_id, action, created_by)
        SELECT id, book_id, summary, 'legacy_migration' FROM events
        """
    )
    # 旧世界卡与条目先迁入概念层，再逐条建立带来源边界的原子说明。
    connection.execute(
        """
        INSERT OR IGNORE INTO concepts(
            book_id, category, preferred_label, description, custom, created_by
        )
        SELECT book_id, category, title, summary, 0, 'world_note_migration'
        FROM world_notes
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO concepts(
            book_id, category, preferred_label, description, custom, created_by
        )
        SELECT book_id, category, name, summary, 0, 'entry_migration'
        FROM entries
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO knowledge_claims(
            book_id, concept_id, subject_type, subject_id, predicate, value_json,
            status, confidence, source_kind, created_by
        )
        SELECT w.book_id, c.id, 'world_note', w.id, 'summary', json_quote(w.summary),
            CASE WHEN w.archived_at IS NULL THEN 'accepted' ELSE 'deprecated' END,
            w.confidence, 'original_text', 'world_note_migration'
        FROM world_notes w
        JOIN concepts c ON c.book_id = w.book_id AND c.category = w.category
            AND c.preferred_label = w.title
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO knowledge_claims(
            book_id, concept_id, subject_type, subject_id, predicate, value_json,
            status, confidence, source_kind, created_by
        )
        SELECT e.book_id, c.id, 'entry', e.id, 'summary', json_quote(e.summary),
            'accepted', e.confidence, 'original_text', 'entry_migration'
        FROM entries e
        JOIN concepts c ON c.book_id = e.book_id AND c.category = e.category
            AND c.preferred_label = e.name
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO knowledge_claim_evidence(knowledge_claim_id, evidence_id)
        SELECT k.id, ev.id
        FROM knowledge_claims k
        JOIN evidence ev ON ev.book_id = k.book_id
            AND ev.target_type = k.subject_type AND ev.target_id = k.subject_id
        """
    )


def _migrate_v28(connection: sqlite3.Connection) -> None:
    """Add reversible 2.8 relation semantics and feature switches to existing books."""

    from app.relations import SAFE_REVERSE_PREDICATES, SYMMETRIC_PREDICATES

    existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(claims)")}
    additions = (
        "directionality TEXT NOT NULL DEFAULT 'directed'",
        "reverse_predicate TEXT",
        "temporal_scope TEXT NOT NULL DEFAULT 'current'",
    )
    for definition in additions:
        column = definition.split()[0]
        if column not in existing:
            connection.execute(f"ALTER TABLE claims ADD COLUMN {definition}")

    for predicate in sorted(SYMMETRIC_PREDICATES):
        connection.execute(
            """
            UPDATE claims SET directionality = 'bidirectional', reverse_predicate = predicate
            WHERE predicate = ? AND directionality = 'directed'
            """,
            (predicate,),
        )
    for predicate, reverse_predicate in SAFE_REVERSE_PREDICATES.items():
        connection.execute(
            """
            UPDATE claims SET directionality = 'bidirectional', reverse_predicate = ?
            WHERE predicate = ? AND directionality = 'directed'
            """,
            (reverse_predicate, predicate),
        )

    features = ("relation_semantics_v2", "atlas_workspace_v3", "library_workspace_v2")
    for book in connection.execute("SELECT id FROM books"):
        for feature in features:
            connection.execute(
                "INSERT OR IGNORE INTO feature_flags(book_id, feature_key, enabled) VALUES (?, ?, 1)",
                (int(book["id"]), feature),
            )


def _migrate_v29(connection: sqlite3.Connection) -> None:
    """Enable the reversible 2.9 interaction, atlas, cost, systems, and reader layers."""

    existing_node_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(world_system_nodes)")}
    if "evidence_id" not in existing_node_columns:
        connection.execute("ALTER TABLE world_system_nodes ADD COLUMN evidence_id INTEGER REFERENCES evidence(id) ON DELETE SET NULL")
    existing_relation_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(world_system_relations)")}
    if "updated_at" not in existing_relation_columns:
        connection.execute("ALTER TABLE world_system_relations ADD COLUMN updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")

    features = (
        "ui_foundation_v3",
        "atlas_lod_v4",
        "guided_3d_v1",
        "cost_forecast_v2",
        "system_graph_v1",
        "knowledge_reader_v3",
    )
    for book in connection.execute("SELECT id FROM books"):
        for feature in features:
            connection.execute(
                "INSERT OR IGNORE INTO feature_flags(book_id, feature_key, enabled) VALUES (?, ?, 1)",
                (int(book["id"]), feature),
            )


def _migrate_v291(connection: sqlite3.Connection) -> None:
    """Add auditable benchmark review states without counting legacy seed data as human work."""

    existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(quality_benchmark_cases)")}
    additions = (
        "review_status TEXT NOT NULL DEFAULT 'candidate'",
        "reviewer_id TEXT NOT NULL DEFAULT ''",
        "reviewer_role TEXT NOT NULL DEFAULT ''",
        "review_session TEXT NOT NULL DEFAULT ''",
        "review_evidence_hash TEXT NOT NULL DEFAULT ''",
        "reviewed_at TEXT",
        "second_review_status TEXT NOT NULL DEFAULT 'not_required'",
        "second_reviewer_id TEXT NOT NULL DEFAULT ''",
        "second_reviewed_at TEXT",
    )
    for definition in additions:
        column = definition.split()[0]
        if column not in existing:
            connection.execute(f"ALTER TABLE quality_benchmark_cases ADD COLUMN {definition}")

    # These cases were inserted by code in earlier releases. Preserve them as useful
    # candidates while removing the unsupported claim that a person confirmed them.
    connection.execute(
        """
        UPDATE quality_benchmark_cases
        SET origin = 'agent_seeded_candidate', holdout = 0, confirmed_by_user = 0,
            review_status = 'candidate', reviewer_id = '', reviewer_role = '',
            review_session = '', review_evidence_hash = '', reviewed_at = NULL,
            second_review_status = 'not_required', second_reviewer_id = '',
            second_reviewed_at = NULL,
            note = '系统根据《西游记》固定题库准备的待人工核对候选'
        WHERE failure_category = 'xiyouji-core'
          AND subject IN (
              '玄奘=陈玄奘', '玄奘=唐僧', '石猴=孙悟空', '猪八戒=猪悟能',
              '沙僧=沙悟净', '孙悟空≠唐僧', '孙悟空≠猪八戒', '唐僧≠猪八戒',
              '观音菩萨≠如来佛祖', '牛魔王≠红孩儿', '石猴出世', '大闹天宫',
              '揭帖救出孙悟空', '白骨精第一次变化', '红孩儿三昧真火', '扇熄火焰山',
              '石猴出世早于大闹天宫', '大闹天宫早于压五行山', '压五行山早于揭帖',
              '揭帖早于白骨精', '白骨精早于红孩儿', '红孩儿早于火焰山',
              '火焰山早于传播真经', '主线人物包含孙悟空', '主线行程从开篇开始',
              '全书片段无缺漏', '正式事实全部有证据', '证据逐字存在于原文'
          )
        """
    )
    for book in connection.execute("SELECT id FROM books"):
        connection.execute(
            "INSERT OR IGNORE INTO feature_flags(book_id, feature_key, enabled) VALUES (?, 'trusted_eval_v1', 1)",
            (int(book["id"]),),
        )


def _repair_derived_self_routes(connection: sqlite3.Connection) -> None:
    """删除旧版本自动生成的同地点伪路线，事件和地点本身继续保留。"""

    connection.execute(
        """
        DELETE FROM journey_legs
        WHERE created_by = 'derived'
          AND from_entity_id IS NOT NULL
          AND from_entity_id = to_entity_id
        """
    )


def _migrate_control_plane(connection: sqlite3.Connection) -> None:
    """为旧数据库补齐透明协作、评估来源和逐次调用溯源字段。"""

    additions: dict[str, tuple[str, ...]] = {
        "quality_benchmark_cases": (
            "suite_name TEXT NOT NULL DEFAULT 'book-gold'",
            "origin TEXT NOT NULL DEFAULT 'manual'",
            "holdout INTEGER NOT NULL DEFAULT 0",
            "confirmed_by_user INTEGER NOT NULL DEFAULT 1",
            "failure_category TEXT NOT NULL DEFAULT ''",
        ),
        "model_call_ledger": (
            "run_manifest_id INTEGER REFERENCES run_manifests(id) ON DELETE SET NULL",
            "prompt_hash TEXT NOT NULL DEFAULT ''",
            "duration_ms INTEGER NOT NULL DEFAULT 0",
            "auth_mode TEXT NOT NULL DEFAULT 'api'",
        ),
        "analysis_jobs": (
            "run_manifest_id INTEGER REFERENCES run_manifests(id) ON DELETE SET NULL",
        ),
        "segment_results": (
            "job_id INTEGER REFERENCES analysis_jobs(id) ON DELETE SET NULL",
            "run_manifest_id INTEGER REFERENCES run_manifests(id) ON DELETE SET NULL",
            "model_call_id INTEGER REFERENCES model_call_ledger(id) ON DELETE SET NULL",
        ),
        "evidence": (
            "run_manifest_id INTEGER REFERENCES run_manifests(id) ON DELETE SET NULL",
            "model_call_id INTEGER REFERENCES model_call_ledger(id) ON DELETE SET NULL",
        ),
    }
    for table, definitions in additions.items():
        existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for definition in definitions:
            column = definition.split()[0]
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migrate_quality_harness(connection: sqlite3.Connection) -> None:
    """为旧数据库补齐质量门禁状态和世界信息归档字段。"""

    additions: dict[str, tuple[str, ...]] = {
        "world_notes": (
            "archived_at TEXT",
        ),
        "analysis_jobs": (
            "quality_gate_status TEXT NOT NULL DEFAULT 'pending'",
            "quality_gate_snapshot_id INTEGER REFERENCES quality_gate_snapshots(id) ON DELETE SET NULL",
        ),
        "entity_connectivity_reviews": (
            "source_segment_count INTEGER NOT NULL DEFAULT 0",
        ),
    }
    for table, definitions in additions.items():
        existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for definition in definitions:
            column = definition.split()[0]
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
        connection.execute(
            """
            UPDATE analysis_job_segments SET status = 'pending', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
            """
        )
    # 旧版任务没有逐次调用账本。先保存其既有用量和费用，后续专项复审才能正确累加而非覆盖。
    connection.execute(
        """
        INSERT INTO model_call_ledger(
            book_id, job_id, purpose, provider, model, prompt_version, request_hash,
            status, input_tokens, output_tokens, cache_hit_input_tokens,
            cache_miss_input_tokens, estimated_cost_usd
        )
        SELECT job.book_id, job.id, 'legacy_analysis', job.provider, job.model,
            job.prompt_version, 'legacy-job-' || job.id, 'completed',
            job.input_tokens, job.output_tokens, job.cache_hit_input_tokens,
            job.cache_miss_input_tokens, job.estimated_cost_usd
        FROM analysis_jobs job
        WHERE (job.input_tokens > 0 OR job.output_tokens > 0)
          AND NOT EXISTS (
              SELECT 1 FROM model_call_ledger ledger
              WHERE ledger.job_id = job.id
                AND ledger.purpose IN ('legacy_analysis', 'segment_extraction', 'global_review')
          )
        """
    )


def _repair_mismatched_evidence_segments(connection: sqlite3.Connection) -> None:
    """修复旧任务把队列行号误存成原文章节编号的证据记录。"""

    connection.execute(
        """
        UPDATE evidence AS evidence_row
        SET segment_id = (
            SELECT job_segment.segment_id
            FROM analysis_job_segments job_segment
            JOIN analysis_jobs job ON job.id = job_segment.job_id
            WHERE job_segment.id = evidence_row.segment_id
              AND job.book_id = evidence_row.book_id
            ORDER BY job.id DESC LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM segments current_segment
            WHERE current_segment.id = evidence_row.segment_id
              AND current_segment.book_id != evidence_row.book_id
        )
          AND EXISTS (
            SELECT 1 FROM analysis_job_segments job_segment
            JOIN analysis_jobs job ON job.id = job_segment.job_id
            WHERE job_segment.id = evidence_row.segment_id
              AND job.book_id = evidence_row.book_id
          )
        """
    )


def _migrate_library_columns(connection: sqlite3.Connection) -> None:
    """为旧书库补齐文件夹和更新时间字段。"""

    existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(books)")}
    additions = (
        "folder_id INTEGER REFERENCES library_folders(id) ON DELETE SET NULL",
        "updated_at TEXT NOT NULL DEFAULT ''",
        "language TEXT NOT NULL DEFAULT ''",
        "corpus_kind TEXT NOT NULL DEFAULT 'user_upload'",
        "license_name TEXT NOT NULL DEFAULT ''",
        "source_url TEXT NOT NULL DEFAULT ''",
        "rights_status TEXT NOT NULL DEFAULT 'user_supplied'",
        "source_sha256 TEXT NOT NULL DEFAULT ''",
    )
    for definition in additions:
        column = definition.split()[0]
        if column not in existing:
            connection.execute(f"ALTER TABLE books ADD COLUMN {definition}")
    connection.execute(
        "UPDATE books SET updated_at = created_at WHERE updated_at IS NULL OR updated_at = ''"
    )
    connection.execute(
        """
        UPDATE books SET corpus_kind = 'synthetic', rights_status = 'synthetic',
            license_name = '系统虚构，不对应真实作品', language = 'zh-CN'
        WHERE author IN ('系统虚构样例', '系统大型联动样例')
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_books_folder ON books(folder_id, updated_at)")


def _migrate_cost_columns(connection: sqlite3.Connection) -> None:
    """为旧数据库补齐令牌明细和价格快照字段。"""

    additions: dict[str, tuple[str, ...]] = {
        "analysis_jobs": (
            "cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0",
            "cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0",
            "cache_hit_input_usd_per_million REAL",
            "cache_miss_input_usd_per_million REAL",
            "output_usd_per_million REAL",
            "estimated_cost_usd REAL",
            "pricing_source TEXT NOT NULL DEFAULT ''",
            "pricing_effective_date TEXT NOT NULL DEFAULT ''",
            "max_cost_usd REAL NOT NULL DEFAULT 0.5",
            "max_input_tokens INTEGER NOT NULL DEFAULT 500000",
            "max_output_tokens INTEGER NOT NULL DEFAULT 120000",
            "estimated_before_start_usd REAL",
            "budget_status TEXT NOT NULL DEFAULT 'within_budget'",
            "budget_mode TEXT NOT NULL DEFAULT 'adaptive'",
            "budget_adjustments INTEGER NOT NULL DEFAULT 0",
            "review_mode TEXT NOT NULL DEFAULT 'local'",
            "cache_reused_segments INTEGER NOT NULL DEFAULT 0",
        ),
        "analysis_job_segments": (
            "cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0",
            "cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0",
        ),
        "analysis_job_review_usage": (
            "cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0",
            "cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0",
        ),
        "segment_results": (
            "cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0",
            "cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0",
        ),
        "global_review_batches": (
            "cache_hit_input_tokens INTEGER NOT NULL DEFAULT 0",
            "cache_miss_input_tokens INTEGER NOT NULL DEFAULT 0",
        ),
    }
    for table, definitions in additions.items():
        existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for definition in definitions:
            column = definition.split()[0]
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    for table in (
        "analysis_job_segments",
        "analysis_job_review_usage",
        "segment_results",
        "global_review_batches",
    ):
        connection.execute(
            f"""
            UPDATE {table} SET cache_miss_input_tokens = input_tokens
            WHERE input_tokens > 0 AND cache_hit_input_tokens + cache_miss_input_tokens = 0
            """
        )
    connection.execute(
        """
        UPDATE analysis_jobs SET
            cache_miss_input_tokens = CASE
                WHEN cache_hit_input_tokens + cache_miss_input_tokens = 0 THEN input_tokens
                ELSE cache_miss_input_tokens
            END,
            cache_hit_input_usd_per_million = CASE
                WHEN model = 'deepseek-reasoner' THEN 0.14 ELSE 0.07
            END,
            cache_miss_input_usd_per_million = CASE
                WHEN model = 'deepseek-reasoner' THEN 0.55 ELSE 0.27
            END,
            output_usd_per_million = CASE
                WHEN model = 'deepseek-reasoner' THEN 2.19 ELSE 1.10
            END,
            pricing_source = 'DeepSeek 官方价格页；旧任务按当前价补算',
            pricing_effective_date = '2026-08-23'
        WHERE provider = 'deepseek' AND cache_hit_input_usd_per_million IS NULL
        """
    )


def _migrate_semantic_columns(connection: sqlite3.Connection) -> None:
    """为旧数据库补齐叙事坐标和时间约束状态。"""

    additions: dict[str, tuple[str, ...]] = {
        "events": (
            "narrative_phase TEXT NOT NULL DEFAULT 'main'",
            "narrative_offset INTEGER NOT NULL DEFAULT 0",
        ),
        "event_order_edges": (
            "status TEXT NOT NULL DEFAULT 'accepted'",
            "reason TEXT NOT NULL DEFAULT ''",
            "evidence_json TEXT NOT NULL DEFAULT '[]'",
            "resolution_reason TEXT NOT NULL DEFAULT ''",
            "resolved_by TEXT NOT NULL DEFAULT ''",
            "resolved_at TEXT",
        ),
        "entity_merge_candidates": (
            "resolution_reason TEXT NOT NULL DEFAULT ''",
            "resolved_by TEXT NOT NULL DEFAULT ''",
            "resolved_at TEXT",
        ),
        "contradictions": (
            "resolution_reason TEXT NOT NULL DEFAULT ''",
            "resolved_by TEXT NOT NULL DEFAULT ''",
            "resolved_at TEXT",
        ),
    }
    for table, definitions in additions.items():
        existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for definition in definitions:
            column = definition.split()[0]
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    connection.execute(
        """
        UPDATE analysis_jobs SET
            estimated_cost_usd = CASE
                WHEN EXISTS (
                    SELECT 1 FROM model_call_ledger ledger
                    WHERE ledger.job_id = analysis_jobs.id
                      AND ledger.status IN ('completed', 'cache_reused', 'failed')
                ) AND NOT EXISTS (
                    SELECT 1 FROM model_call_ledger ledger
                    WHERE ledger.job_id = analysis_jobs.id
                      AND ledger.status IN ('completed', 'cache_reused', 'failed')
                      AND ledger.estimated_cost_usd IS NULL
                ) THEN ROUND((
                    SELECT COALESCE(SUM(ledger.estimated_cost_usd), 0)
                    FROM model_call_ledger ledger
                    WHERE ledger.job_id = analysis_jobs.id
                      AND ledger.status IN ('completed', 'cache_reused', 'failed')
                ), 8)
                ELSE ROUND((
                    cache_hit_input_tokens * cache_hit_input_usd_per_million
                    + cache_miss_input_tokens * cache_miss_input_usd_per_million
                    + output_tokens * output_usd_per_million
                ) / 1000000.0, 8)
            END
        WHERE cache_hit_input_usd_per_million IS NOT NULL
          AND cache_miss_input_usd_per_million IS NOT NULL
          AND output_usd_per_million IS NOT NULL
        """
    )
@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    """在异常时回滚，在成功时提交。"""

    connection = connect(path)
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
