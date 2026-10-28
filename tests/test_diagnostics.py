"""Conservative, evidence-backed run failure diagnostics."""

from dataclasses import FrozenInstanceError

import pytest

from gridwise.diagnostics import (FailureCategory, FailureStage,
                                  diagnose_failure)
from gridwise.run_artifacts import RunArtifactError


def command_failure(stderr: str, *, stage: str = "solve") -> RunArtifactError:
    return RunArtifactError(
        f"command exited 2: {stderr.strip()}", stage=stage,
        reason="command_failed", returncode=2, stderr=stderr)


def test_infeasible_requires_explicit_solver_termination_evidence():
    error = command_failure(
        "gridwise: no optimal solution: error, infeasible\n")

    diagnostic = diagnose_failure(error)

    assert diagnostic.category is FailureCategory.INFEASIBLE
    assert diagnostic.stage is FailureStage.SOLVE
    assert diagnostic.solver_status == "error"
    assert diagnostic.termination_condition == "infeasible"
    assert diagnostic.original_error is error
    assert any("termination_condition=infeasible" in item for item in diagnostic.evidence)

    ambiguous = diagnose_failure(
        RuntimeError("perhaps infeasible, perhaps not"),
        stage_hint=FailureStage.SOLVE)
    assert ambiguous.category is FailureCategory.UNKNOWN
    assert ambiguous.termination_condition is None


@pytest.mark.parametrize(("error", "category", "stage"), [
    (command_failure("gridwise: solver unavailable: appsi_highs\n"),
     FailureCategory.SOLVER_UNAVAILABLE, FailureStage.SOLVE),
    (command_failure("gridwise: solver failed: synthetic backend failure\n"),
     FailureCategory.SOLVER_ERROR, FailureStage.SOLVE),
    (RunArtifactError("command timed out after 4 s", stage="solve", reason="timeout"),
     FailureCategory.SOLVER_TIMEOUT, FailureStage.SOLVE),
    (RunArtifactError("run cancelled", stage="solve", reason="cancelled"),
     FailureCategory.CANCELLED, FailureStage.SOLVE),
    (RunArtifactError("command exited 3", stage="audit", reason="command_failed"),
     FailureCategory.AUDIT_FAILED, FailureStage.AUDIT),
    (RunArtifactError("size or SHA-256 mismatch", stage="artifact", reason="invalid"),
     FailureCategory.ARTIFACT_INVALID, FailureStage.ARTIFACT),
])
def test_structured_stage_and_reason_classify_distinct_failures(error, category, stage):
    diagnostic = diagnose_failure(error)

    assert diagnostic.category is category
    assert diagnostic.stage is stage
    assert diagnostic.original_error is error
    assert diagnostic.suggestions


def test_input_diagnostic_is_immutable_and_never_changes_parameters():
    parameters = {"grid_max_kw": 120, "terminal_kwh": 40}
    original = parameters.copy()
    error = ValueError("grid_max_kw must be nonnegative")

    diagnostic = diagnose_failure(error, stage_hint=FailureStage.INPUT)

    assert diagnostic.category is FailureCategory.INPUT_INVALID
    assert parameters == original
    with pytest.raises(FrozenInstanceError):
        diagnostic.category = FailureCategory.UNKNOWN


def test_unknown_and_diagnostic_formatting_failures_preserve_original_error():
    class UnprintableError(RuntimeError):
        def __str__(self):
            raise RuntimeError("synthetic formatting failure")

    error = UnprintableError()
    diagnostic = diagnose_failure(error)

    assert diagnostic.category is FailureCategory.UNKNOWN
    assert diagnostic.original_error is error
    assert "根因无法确认" in diagnostic.summary
    assert diagnostic.cause_chain
