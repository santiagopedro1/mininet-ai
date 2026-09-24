"""Public error types used by the specification toolchain."""


class MininetAIError(Exception):
    """Base class for errors that should be shown directly to a user."""


class SpecificationError(MininetAIError):
    """The source document is missing, malformed, or schema-invalid."""


class CompilationError(MininetAIError):
    """A valid document cannot be turned into a safe deployment plan."""


class RuntimeOperationError(MininetAIError):
    """A substrate runtime operation could not be completed safely."""

    def __init__(
        self, message: str, *, code: str, run_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.run_id = run_id


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
