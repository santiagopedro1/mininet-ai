"""Source-only effective model validation, never saved-plan reconstruction."""

from mininet_ai.errors import SpecificationError
from mininet_ai.specification.models import AgentBlueprint, ModelConfiguration


def validate_model_configuration(
    blueprint: AgentBlueprint, model: ModelConfiguration
) -> None:
    """Check shared source rules without constructing models or loading factories."""
    for field in model.parameters:
        if field in {
            "api_key",
            "api-key",
            "token",
            "password",
            "secret",
            "credentials",
        }:
            raise SpecificationError(
                f"blueprint {blueprint.metadata.name!r} model.parameters.{field}: "
                "literal credentials are unsupported; use the Provider runtime environment"
            )
