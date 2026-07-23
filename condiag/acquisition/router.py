"""P1-3D: AcquisitionRouter — dispatch SearchAction to executor."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from condiag.acquisition.definition_search import find_definition
from condiag.acquisition.related_test_search import find_related_tests
from condiag.acquisition.schema import (
    AcquisitionResult,
    AcquisitionStatus,
)
from condiag.diagnosis.search_contract import (
    SearchAction,
    SearchActionType,
    SearchContract,
)


class AcquisitionRouter:
    """Dispatch SearchActions to executors.

    v1 supports only FIND_DEFINITION and FIND_RELATED_TESTS. Other
    action types return UNSUPPORTED. The router NEVER modifies the
    repo and NEVER reads gold patch / gold context.
    """

    def __init__(
        self,
        repo_root: Path | str,
        r1_viewed_files: Iterable[str] | None = None,
        failed_test_names: Iterable[str] | None = None,
        max_files_examined: int = 200,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.r1_viewed_files = set(r1_viewed_files or [])
        self.failed_test_names = list(failed_test_names or [])
        self.max_files_examined = max_files_examined

    def dispatch(self, action: SearchAction) -> AcquisitionResult:
        """Execute one SearchAction against the repo."""
        if action.action_type == SearchActionType.FIND_DEFINITION:
            return find_definition(
                action, self.repo_root,
                max_files_examined=self.max_files_examined,
            )
        if action.action_type == SearchActionType.FIND_RELATED_TESTS:
            return find_related_tests(
                action,
                self.repo_root,
                r1_viewed_files=self.r1_viewed_files,
                failed_test_names=self.failed_test_names,
                max_files_examined=self.max_files_examined,
            )
        return AcquisitionResult(
            action_id=action.action_id,
            action_type=action.action_type,
            target=action.target,
            status=AcquisitionStatus.UNSUPPORTED,
            errors=[f"action_type={action.action_type.value} not implemented in v1"],
        )

    def dispatch_contract(
        self, contract: SearchContract,
    ) -> list[AcquisitionResult]:
        """Execute every action in a contract, returning one result per action."""
        return [self.dispatch(a) for a in contract.actions]
