"""
ConDiag — Failure-Guided Diagnostic Search Contracts for Repository-Level Program Repair.

Pipeline:
  FailureWitness (v2.0) → TrajectorySignals → ContextDeficiencyDiagnosis
    → DiagnosticSearchContract → Rendered Contract → Attempt-2 Agent
"""

__version__ = "2.1.0-dev"

from condiag.context_deficiency_diagnoser import (
    CDTYPE_API_DEFINITION,
    CDTYPE_CALLER_CALLEE,
    CDTYPE_DEPENDENCY,
    CDTYPE_INTERFACE_CONSTRAINT,
    CDTYPE_REGRESSION_CONSTRAINT,
    CDTYPE_RELATED_TEST,
    CDTYPE_ROOT_CAUSE,
    ContextDeficiencyDiagnoser,
    ContextDeficiencyDiagnosis,
    PatchBehavior,
)
from condiag.contract_renderer import render_contract_to_markdown
from condiag.schemas import (
    FailureWitness,
    RetryRequest,
)
from condiag.search_contract_builder import (
    DiagnosticSearchContract,
    DiagnosticSearchContractBuilder,
    build_contract,
    contract_to_file,
)
from condiag.trajectory_signals import (
    FailureWitnessLoader,
    OracleAudit,
    RuntimeSignals,
    TrajParser,
)

__all__ = [
    # CDType taxonomy constants
    "CDTYPE_API_DEFINITION",
    "CDTYPE_CALLER_CALLEE",
    "CDTYPE_DEPENDENCY",
    "CDTYPE_INTERFACE_CONSTRAINT",
    "CDTYPE_REGRESSION_CONSTRAINT",
    "CDTYPE_RELATED_TEST",
    "CDTYPE_ROOT_CAUSE",
    # Diagnoser
    "ContextDeficiencyDiagnoser",
    "ContextDeficiencyDiagnosis",
    "PatchBehavior",
    # Renderer
    "render_contract_to_markdown",
    # Schemas
    "DiagnosticSearchContract",
    "FailureWitness",
    "RetryRequest",
    # Contract builder
    "DiagnosticSearchContractBuilder",
    "build_contract",
    "contract_to_file",
    # Trajectory signals
    "FailureWitnessLoader",
    "OracleAudit",
    "RuntimeSignals",
    "TrajParser",
]
