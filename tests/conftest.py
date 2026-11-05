"""Strict pytest classification and safety guards for GridWise tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest


PRIMARY_MARKERS = frozenset({"unit", "gui", "integration"})


def _test_key(item: pytest.Item) -> tuple[str, str]:
    """Return a stable module/name key shared by parametrized items."""
    return Path(str(item.path)).name, getattr(item, "originalname", item.name)


# Every audit-history test uses historical_case, which creates exports with the
# real CLI solver before exercising the independently isolated history auditor.
SOLVER_MODULES = frozenset({"test_audit_history.py"})
AUDIT_MODULES = frozenset({"test_audit_history.py"})
SLOW_MODULES = frozenset({"test_audit_history.py"})


# audit_exports.py uses the real export fixture and auditor everywhere except
# the isolated git-provenance fallback test.
AUDIT_EXPORTS_SAFE_EXCEPTION = "test_git_command_missing_returns_explicit_unavailable"


SOLVER_TESTS = {
    "test_cli_output_safety.py": {
        "test_explicit_overwrite_replaces_only_export_files",
        "test_default_publish_rolls_back_on_late_collision",
        "test_explicit_overwrite_rolls_back_if_later_replace_fails",
        "test_failed_staging_keeps_existing_exports_and_removes_stage",
        "test_failed_rollback_retains_recovery_copy",
        "test_non_oserror_during_publication_restores_old_files",
        "test_backup_creation_failure_restores_previous_replacements",
        "test_cleanup_failure_reports_published_state_and_retains_backups",
        "test_cleanup_failure_after_publication_error_reports_both_causes",
    },
    "test_core.py": {
        "test_example_solves_and_exports",
        "test_explicit_peak_cap_shaves_sample_below_no_storage_baseline",
        "test_energy_boundaries",
        "test_infeasible_grid_cap",
        "test_solver_unavailable",
        "test_independent_validator_catches_corruption",
        "test_validator_rejects_changed_input_echo",
        "test_efficiency_and_terminal_soc_at_nonunit_step",
        "test_empty_battery_cannot_supply_load_without_import",
        "test_full_battery_cannot_accept_surplus_without_curtailment",
        "test_no_battery_baseline_infeasible_when_grid_cap_too_low",
        "test_time_step_and_extreme_valid_prices",
        "test_terminal_soc_can_make_model_infeasible",
    },
    "test_dashboard.py": {
        "test_custom_devices_flow_through_both_modes_verified_artifacts_and_history",
        "test_valid_synthetic_csv_runs_audited_comparison",
        "test_custom_timestamp_spellings_complete_verified_dashboard_flow",
        "test_custom_half_hour_csv_runs_cost_and_peak_cap_modes",
        "test_valid_but_infeasible_custom_devices_fail_without_completed_run",
        "test_infeasible_dashboard_error_exposes_evidence_backed_diagnostic",
        "test_peak_cap_comparison_publishes_verified_policy_and_metrics",
        "test_peak_cap_history_preserves_mode_and_parameter",
        "test_peak_cap_cost_delta_requires_verified_same_input_cost_run",
        "test_verified_history_runs_compare_metrics_and_signed_delta",
        "test_history_comparison_rejects_same_run_identifier",
        "test_history_comparison_rejects_different_scenario_and_devices",
        "test_history_comparison_rejects_solver_or_model_context",
        "test_history_comparison_rejects_baseline_metric_mismatch",
        "test_history_comparison_rejects_damaged_or_incomplete_run",
        "test_history_comparison_rejects_current_csv_mismatch",
        "test_audit_failure_blocks_chart_preparation",
        "test_solver_timeout_fails_and_cleans_temporary_files",
        "test_solver_failure_fails_closed_and_cleans_temporary_files",
        "test_history_classifies_and_rechecks_selected_run",
        "test_history_rejects_incomplete_and_other_input",
        "test_preview_verified_run_uses_safe_reader_and_recalculated_metrics",
        "test_preview_verified_run_fails_closed_for_mismatch_damage_and_cancellation",
        "test_two_runs_keep_separate_verified_metrics",
        "test_failed_verified_read_never_prepares_chart",
    },
    "test_data.py": {
        "test_visualization_data_keeps_cost_energy_and_peak_distinct",
        "test_visualization_data_rejects_misaligned_or_changed_results",
        "test_visualization_data_rejects_bad_import",
        "test_visualization_rejects_both_scenario_alignment_errors",
    },
    "test_desktop_ui.py": {
        "test_desktop_peak_cap_run_shows_verified_metrics_and_cost_reference",
        "test_infeasible_peak_cap_never_shows_success",
        "test_chart_time_labels_remain_separate_at_double_scaling",
        "test_verified_desktop_run_history_and_damaged_result",
        "test_verified_desktop_two_runs_and_input_change",
        "test_loading_history_with_different_devices_immediately_invalidates_comparison",
        "test_desktop_audit_failure_never_displays_success",
        "test_saved_runs_root_restores_history_after_desktop_restart",
        "test_saved_peak_cap_history_restores_mode_after_restart",
        "test_keyboard_browse_check_run_and_chart_tabs",
    },
    "test_export_consistency.py": {
        "test_two_cli_writers_allow_only_one_complete_export",
    },
    "test_peak_cap.py": {
        "test_peak_cap_cli_exports_traceable_auditable_comparison",
        "test_peak_cap_infeasible_fails_without_exports",
        "test_peak_cap_audit_rejects_falsified_cap",
        "test_peak_cap_audit_rejects_falsified_policy_or_status",
        "test_independent_result_validation_rejects_claimed_cap_below_grid",
        "test_peak_cap_immutable_run_records_and_verifies_policy",
        "test_peak_cap_verified_reader_rejects_changed_summary",
        "test_default_immutable_run_retains_v1_manifest_and_audit",
    },
    "test_reports.py": {
        "test_single_and_ab_html_reuse_verified_metrics_and_escape_input_source",
        "test_export_refuses_damaged_run_and_unmatched_comparison_without_output",
    },
    "test_run_artifacts.py": {
        "test_publish_and_verify_cli_and_same_run_metadata",
        "test_new_runs_do_not_replace_existing_run",
        "test_missing_or_corrupt_file_is_rejected",
        "test_missing_or_forged_marker_is_rejected",
        "test_manifest_change_without_marker_update_is_rejected",
        "test_manifest_refuses_unsafe_or_duplicate_names",
        "test_manifest_refuses_extra_file_and_unbounded_sizes",
        "test_manifest_refuses_oversize_token",
        "test_unlisted_file_and_declared_file_over_limit_are_rejected",
        "test_reader_rejects_link_target_before_open",
        "test_reader_rejects_junction_target_before_open",
        "test_wrong_directory_and_incomplete_run_rejected",
        "test_summary_metadata_mismatch_rejected_even_when_hashes_updated",
        "test_concurrent_readers_receive_complete_verified_bytes",
        "test_audit_failure_leaves_incomplete_unreadable_run",
        "test_audited_file_changed_before_manifest_is_rejected",
        "test_publish_failure_leaves_incomplete_run",
        "test_manifest_generation_failure_leaves_incomplete_run",
        "test_final_verification_failure_removes_own_marker",
        "test_reader_detects_modification_during_open",
        "test_reader_detects_replacement_during_open",
        "test_reader_detects_new_directory_entry_during_verification",
        "test_reader_detects_file_removed_after_open",
        "test_reader_rejects_duplicate_or_nonfinite_manifest_json",
        "test_reader_rejects_duplicate_or_nonfinite_summary_json",
        "test_forced_termination_leaves_no_readable_run",
    },
}


AUDIT_TESTS = {
    "test_dashboard.py": {
        "test_custom_devices_flow_through_both_modes_verified_artifacts_and_history",
        "test_valid_synthetic_csv_runs_audited_comparison",
        "test_custom_timestamp_spellings_complete_verified_dashboard_flow",
        "test_custom_half_hour_csv_runs_cost_and_peak_cap_modes",
        "test_peak_cap_comparison_publishes_verified_policy_and_metrics",
        "test_peak_cap_history_preserves_mode_and_parameter",
        "test_peak_cap_cost_delta_requires_verified_same_input_cost_run",
        "test_verified_history_runs_compare_metrics_and_signed_delta",
        "test_history_comparison_rejects_same_run_identifier",
        "test_history_comparison_rejects_different_scenario_and_devices",
        "test_history_comparison_rejects_solver_or_model_context",
        "test_history_comparison_rejects_baseline_metric_mismatch",
        "test_history_comparison_rejects_damaged_or_incomplete_run",
        "test_history_comparison_rejects_current_csv_mismatch",
        "test_audit_failure_blocks_chart_preparation",
        "test_history_classifies_and_rechecks_selected_run",
        "test_history_rejects_incomplete_and_other_input",
        "test_preview_verified_run_uses_safe_reader_and_recalculated_metrics",
        "test_preview_verified_run_fails_closed_for_mismatch_damage_and_cancellation",
        "test_two_runs_keep_separate_verified_metrics",
        "test_failed_verified_read_never_prepares_chart",
    },
    "test_desktop_ui.py": {
        "test_desktop_peak_cap_run_shows_verified_metrics_and_cost_reference",
        "test_chart_time_labels_remain_separate_at_double_scaling",
        "test_verified_desktop_run_history_and_damaged_result",
        "test_verified_desktop_two_runs_and_input_change",
        "test_loading_history_with_different_devices_immediately_invalidates_comparison",
        "test_desktop_audit_failure_never_displays_success",
        "test_saved_runs_root_restores_history_after_desktop_restart",
        "test_saved_peak_cap_history_restores_mode_after_restart",
        "test_keyboard_browse_check_run_and_chart_tabs",
    },
    "test_peak_cap.py": {
        "test_peak_cap_cli_exports_traceable_auditable_comparison",
        "test_peak_cap_audit_rejects_falsified_cap",
        "test_peak_cap_audit_rejects_falsified_policy_or_status",
        "test_peak_cap_immutable_run_records_and_verifies_policy",
        "test_peak_cap_verified_reader_rejects_changed_summary",
        "test_default_immutable_run_retains_v1_manifest_and_audit",
    },
    "test_reports.py": {
        "test_single_and_ab_html_reuse_verified_metrics_and_escape_input_source",
        "test_export_refuses_damaged_run_and_unmatched_comparison_without_output",
    },
    "test_run_artifacts.py": {
        "test_publish_and_verify_cli_and_same_run_metadata",
        "test_new_runs_do_not_replace_existing_run",
        "test_missing_or_corrupt_file_is_rejected",
        "test_missing_or_forged_marker_is_rejected",
        "test_manifest_change_without_marker_update_is_rejected",
        "test_manifest_refuses_unsafe_or_duplicate_names",
        "test_manifest_refuses_extra_file_and_unbounded_sizes",
        "test_manifest_refuses_oversize_token",
        "test_unlisted_file_and_declared_file_over_limit_are_rejected",
        "test_reader_rejects_link_target_before_open",
        "test_reader_rejects_junction_target_before_open",
        "test_wrong_directory_and_incomplete_run_rejected",
        "test_summary_metadata_mismatch_rejected_even_when_hashes_updated",
        "test_concurrent_readers_receive_complete_verified_bytes",
        "test_audit_failure_leaves_incomplete_unreadable_run",
        "test_audited_file_changed_before_manifest_is_rejected",
        "test_publish_failure_leaves_incomplete_run",
        "test_manifest_generation_failure_leaves_incomplete_run",
        "test_final_verification_failure_removes_own_marker",
        "test_reader_detects_modification_during_open",
        "test_reader_detects_replacement_during_open",
        "test_reader_detects_new_directory_entry_during_verification",
        "test_reader_detects_file_removed_after_open",
        "test_reader_rejects_duplicate_or_nonfinite_manifest_json",
        "test_reader_rejects_duplicate_or_nonfinite_summary_json",
    },
}


SLOW_TESTS = {
    "test_export_consistency.py": {
        "test_two_cli_writers_allow_only_one_complete_export",
        "test_kill_after_first_overwrite_exposes_mixed_export",
    },
    "test_run_artifacts.py": {
        "test_forced_termination_leaves_no_readable_run",
    },
    "test_tk_focus_isolation.py": {
        "test_window_lifecycle_keeps_focus_traversal_command_available",
    },
    "test_tk_runtime_diagnostics.py": {
        "test_tk_diagnostic_records_runtime_paths_and_focus",
        "test_probe_child_output_is_bounded",
    },
}


def _in_mapping(mapping: dict[str, set[str]], key: tuple[str, str]) -> bool:
    module, name = key
    return name in mapping.get(module, set())


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply reviewed risk marks and reject incomplete primary classification."""
    problems = []
    for item in items:
        module, name = _test_key(item)
        if module in SOLVER_MODULES or _in_mapping(SOLVER_TESTS, (module, name)):
            item.add_marker(pytest.mark.solver)
        if module in AUDIT_MODULES or _in_mapping(AUDIT_TESTS, (module, name)):
            item.add_marker(pytest.mark.audit)
        if module in SLOW_MODULES or _in_mapping(SLOW_TESTS, (module, name)):
            item.add_marker(pytest.mark.slow)
        if module == "test_audit_exports.py" and name != AUDIT_EXPORTS_SAFE_EXCEPTION:
            item.add_marker(pytest.mark.solver)
            item.add_marker(pytest.mark.audit)

        primary = sorted(mark for mark in PRIMARY_MARKERS if item.get_closest_marker(mark))
        if len(primary) != 1:
            detail = "missing primary marker" if not primary else (
                "multiple primary markers: " + ", ".join(primary))
            problems.append(f"{item.nodeid}: {detail}")

    if problems:
        raise pytest.UsageError(
            "pytest marker classification errors:\n" + "\n".join(problems))


def _command_text(args, kwargs) -> str:
    command = args[0] if args else kwargs.get("args", "")
    if isinstance(command, (str, bytes)):
        return command.decode(errors="replace") if isinstance(command, bytes) else command
    return " ".join(str(token) for token in command)


@pytest.fixture(autouse=True)
def protect_external_test_boundaries(request, monkeypatch, tmp_path_factory):
    """Fail safe tests if they cross a real solver or auditor boundary."""
    solver_allowed = request.node.get_closest_marker("solver") is not None
    audit_allowed = request.node.get_closest_marker("audit") is not None

    if not solver_allowed:
        import pyomo.environ as pyo

        def reject_solver_factory(*_args, **_kwargs):
            pytest.fail(
                f"unmarked real SolverFactory call from {request.node.nodeid}", pytrace=False)

        monkeypatch.setattr(pyo, "SolverFactory", reject_solver_factory)

        # Child Python processes inherit this isolated startup hook. It records
        # an attempted SolverFactory call before rejecting it, so a subprocess
        # that happens to return an expected nonzero code cannot hide a solver
        # boundary violation.
        guard_root = tmp_path_factory.getbasetemp() / "gridwise-boundary-guard"
        guard_root.mkdir(exist_ok=True)
        sitecustomize = guard_root / "sitecustomize.py"
        sitecustomize.write_text(
            "import os\n"
            "if os.environ.get('GRIDWISE_PYTEST_BLOCK_SOLVER') == '1':\n"
            "    import pathlib\n"
            "    import pyomo.environ as pyo\n"
            "    def _blocked_solver_factory(*args, **kwargs):\n"
            "        pathlib.Path(os.environ['GRIDWISE_PYTEST_SOLVER_SENTINEL']).write_text(\n"
            "            'SolverFactory attempted', encoding='utf-8')\n"
            "        raise RuntimeError('unmarked real SolverFactory call in child process')\n"
            "    pyo.SolverFactory = _blocked_solver_factory\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()
        solver_sentinel = guard_root / f"solver-{digest}.txt"
        solver_sentinel.unlink(missing_ok=True)
        monkeypatch.setenv("GRIDWISE_PYTEST_BLOCK_SOLVER", "1")
        monkeypatch.setenv("GRIDWISE_PYTEST_SOLVER_SENTINEL", str(solver_sentinel))
        existing_pythonpath = os.environ.get("PYTHONPATH")
        guarded_pythonpath = str(guard_root)
        if existing_pythonpath:
            guarded_pythonpath += os.pathsep + existing_pythonpath
        monkeypatch.setenv("PYTHONPATH", guarded_pythonpath)
    else:
        solver_sentinel = None

    real_popen = subprocess.Popen

    def check_command(args, kwargs):
        command = _command_text(args, kwargs).replace("\\", "/")
        if not audit_allowed and ("audit_exports.py" in command or "audit_history.py" in command):
            pytest.fail(
                f"unmarked independent auditor subprocess from {request.node.nodeid}: {command}",
                pytrace=False)

    class GuardedPopen(real_popen):
        def __init__(self, *args, **kwargs):
            check_command(args, kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    try:
        yield
    finally:
        # The sentinel is outside the repository and is unique per test item.
        if solver_sentinel is not None and solver_sentinel.exists():
            pytest.fail(
                f"unmarked real SolverFactory call in child process from "
                f"{request.node.nodeid}", pytrace=False)
