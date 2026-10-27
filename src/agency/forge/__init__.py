"""Forge — Agency-native software factory.

v1: read-only patch inspection gate (bounded, non-executing).
v2: static security scan + bounded isolated test runner + composed pipeline.
"""

from __future__ import annotations

from agency.forge.backlog import (
    MAX_ITEMS as MAX_BACKLOG_ITEMS,
)
from agency.forge.backlog import (
    BacklogItem,
    BacklogReport,
    BacklogStore,
    slice_prd,
)
from agency.forge.coding_agent import (
    MAX_EDITS,
    CodingAgentReport,
    CodingAgentStore,
    run_coding_agent,
)
from agency.forge.cost import (
    CostEntry,
    CostReport,
    CostStore,
    record_cost,
)
from agency.forge.evidence import (
    EvidenceReport,
    EvidenceStore,
    capture_evidence,
)
from agency.forge.external_agent import (
    ExternalAgentReport,
    ExternalAgentStore,
    run_external_agent,
)
from agency.forge.holdout import (
    HoldoutReport,
    HoldoutScenario,
    HoldoutScenarioResult,
    HoldoutStore,
    run_holdout,
)
from agency.forge.inspection import (
    CAVEATS,
    MAX_FILE_BYTES,
    MAX_FILES,
    FileRecord,
    InspectChangesResult,
    InspectionReport,
    InspectionStore,
    RejectedRecord,
    inspect_changes,
)
from agency.forge.merge_queue import (
    MergePlan,
    MergeQueueEntry,
    MergeQueueReport,
    MergeQueueStore,
    evaluate_merge,
)
from agency.forge.pipeline import (
    ForgePipelineReport,
    ForgePipelineStore,
    run_pipeline,
)
from agency.forge.prompt_params import (
    AVAILABILITY_PROMPT_ID,
    REA_TOOL_PARAM_NAMES,
    forge_tool_params,
    rea_prompt_params,
)
from agency.forge.reviewer import (
    MAX_ROUNDS,
    ReviewFinding,
    ReviewReport,
    ReviewStore,
    run_review,
)
from agency.forge.scheduler import (
    MAX_COMMAND_CHARS,
    MAX_SCHEDULE_CHARS,
    SchedulerConfig,
    SchedulerReport,
    SchedulerStore,
    generate_timer,
)
from agency.forge.security_scan import (
    SecurityFinding,
    SecurityReport,
    scan_changes,
)
from agency.forge.test_runner import TestReport, run_tests
from agency.forge.tool_catalog import (
    CATALOG,
    ToolSpec,
    available_tools,
    get_tool,
    install_hint,
    is_available,
    list_tools,
    missing_tools,
)
from agency.forge.worktree import WorktreeError, WorktreeInfo, create_worktree, remove_worktree

__all__ = [
    "AVAILABILITY_PROMPT_ID",
    "CATALOG",
    "CAVEATS",
    "MAX_BACKLOG_ITEMS",
    "MAX_COMMAND_CHARS",
    "MAX_EDITS",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_ROUNDS",
    "MAX_SCHEDULE_CHARS",
    "REA_TOOL_PARAM_NAMES",
    "BacklogItem",
    "BacklogReport",
    "BacklogStore",
    "CodingAgentReport",
    "CodingAgentStore",
    "CostEntry",
    "CostReport",
    "CostStore",
    "EvidenceReport",
    "EvidenceStore",
    "ExternalAgentReport",
    "ExternalAgentStore",
    "FileRecord",
    "ForgePipelineReport",
    "ForgePipelineStore",
    "HoldoutReport",
    "HoldoutScenario",
    "HoldoutScenarioResult",
    "HoldoutStore",
    "InspectChangesResult",
    "InspectionReport",
    "InspectionStore",
    "MergePlan",
    "MergeQueueEntry",
    "MergeQueueReport",
    "MergeQueueStore",
    "RejectedRecord",
    "ReviewFinding",
    "ReviewReport",
    "ReviewStore",
    "SchedulerConfig",
    "SchedulerReport",
    "SchedulerStore",
    "SecurityFinding",
    "SecurityReport",
    "TestReport",
    "ToolSpec",
    "WorktreeError",
    "WorktreeInfo",
    "available_tools",
    "capture_evidence",
    "create_worktree",
    "evaluate_merge",
    "forge_tool_params",
    "generate_timer",
    "get_tool",
    "inspect_changes",
    "install_hint",
    "is_available",
    "list_tools",
    "missing_tools",
    "rea_prompt_params",
    "record_cost",
    "remove_worktree",
    "run_coding_agent",
    "run_external_agent",
    "run_holdout",
    "run_pipeline",
    "run_review",
    "run_tests",
    "scan_changes",
    "slice_prd",
]
