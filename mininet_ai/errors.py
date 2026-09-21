"""Public error types used by the specification toolchain."""


class MininetAIError(Exception):
    """Base class for errors that should be shown directly to a user."""


class SpecificationError(MininetAIError):
    """The source document is missing, malformed, or schema-invalid."""


class CompilationError(MininetAIError):
    """A valid document cannot be turned into a safe deployment plan."""
