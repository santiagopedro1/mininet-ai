"""Public error types used by the specification toolchain."""

from typing import Self

from mininet_ai.outcomes import (
    CleanupOutcome,
    MutationEffects,
    OperationFailure,
    OperationOutcome,
)


class MininetAIError(Exception):
    """Base class for errors that should be shown directly to a user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.operation = OperationOutcome.FAILED
        self.effects = MutationEffects.UNKNOWN
        self.cleanup = CleanupOutcome()
        self.teardown_error: OperationFailure | None = None

    def with_cleanup(
        self, cleanup: CleanupOutcome, *, teardown_error: OperationFailure | None = None
    ) -> Self:
        self.cleanup = cleanup
        self.teardown_error = teardown_error
        return self


def describe_failure(error: Exception) -> OperationFailure:
    """Normalize typed and unexpected errors without discarding outcome detail."""
    return OperationFailure(
        code=getattr(error, "code", "operation.failed"),
        message=str(error) or type(error).__name__,
        run_id=getattr(error, "run_id", None),
        operation=getattr(error, "operation", OperationOutcome.UNKNOWN),
        effects=getattr(error, "effects", MutationEffects.UNKNOWN),
        cleanup=getattr(error, "cleanup", CleanupOutcome()),
    )


class SpecificationError(MininetAIError):
    """The source document is missing, malformed, or schema-invalid."""


class CompilationError(MininetAIError):
    """A valid document cannot be turned into a safe deployment plan."""


class RuntimeOperationError(MininetAIError):
    """A substrate runtime operation could not be completed safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        run_id: str | None = None,
        operation: OperationOutcome = OperationOutcome.FAILED,
        effects: MutationEffects = MutationEffects.UNKNOWN,
        cleanup: CleanupOutcome | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.run_id = run_id
        self.operation = operation
        self.effects = effects
        self.cleanup = cleanup or CleanupOutcome()


class AgentRuntimeError(MininetAIError):
    """An agent-runtime contract or invocation could not be used safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        agent_id: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.agent_id = agent_id
        self.invocation_id = invocation_id


class AuditError(MininetAIError):
    """An agent-runtime audit record could not be safely persisted."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code
