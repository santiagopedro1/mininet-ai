"""Outcome vocabulary shared by runtime results, errors, and saved evidence."""

from enum import StrEnum

from pydantic import Field, model_validator

from mininet_ai.specification.models import StrictModel


class OperationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"


class MutationEffects(StrEnum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class CleanupStatus(StrEnum):
    VERIFIED = "verified"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class CleanupOutcome(StrictModel):
    status: CleanupStatus = CleanupStatus.UNKNOWN
    remaining_resources: tuple[str, ...] = ()
    unknown_resources: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ("owned-resource absence has not been verified",)

    @model_validator(mode="after")
    def verification_requires_evidence(self) -> CleanupOutcome:
        if self.status == CleanupStatus.VERIFIED and (
            not self.evidence
            or self.remaining_resources
            or self.unknown_resources
            or self.evidence_gaps
        ):
            raise ValueError(
                "verified cleanup requires evidence and no unresolved resources or gaps"
            )
        if self.status == CleanupStatus.INCOMPLETE and not self.remaining_resources:
            raise ValueError("incomplete cleanup requires known remaining resources")
        return self

    @property
    def verified(self) -> bool:
        return self.status == CleanupStatus.VERIFIED


class OperationFailure(StrictModel):
    """Serializable error detail, independent of the experiment's work outcome."""

    code: str
    message: str
    run_id: str | None = None
    operation: OperationOutcome = OperationOutcome.UNKNOWN
    effects: MutationEffects = MutationEffects.UNKNOWN
    cleanup: CleanupOutcome = Field(default_factory=CleanupOutcome)
