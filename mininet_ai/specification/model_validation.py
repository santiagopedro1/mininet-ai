"""Pure, ownership-aware checks for source model configurations.

These checks deliberately do not run in structural models or saved-plan readers.
Reference resolution may call the same boundary for complete bases and effective
models, supplying its own safe source/entry context. No factory is imported.
"""

from collections.abc import Mapping

from pydantic import ValidationError

from mininet_ai.errors import SpecificationError
from mininet_ai.specification.models import Implementation, ModelConfiguration

_OLLAMA_FACTORIES = {
    "mininet_ai.agents.agno.ollama_factory:create_prompt_parsed_agent",
    "mininet_ai.agents.agno.ollama_factory:create_deterministic_prompt_parsed_agent",
}


def validate_shared_model_configuration(
    configuration: ModelConfiguration, *, context: str
) -> None:
    """Source rules common to all owners, including unused central entries."""
    for field in configuration.parameters:
        if field in {
            "api_key",
            "api-key",
            "token",
            "password",
            "secret",
            "credentials",
        }:
            raise SpecificationError(
                f"{context}: model.parameters.{field}: literal credentials are unsupported; "
                "use the Provider runtime environment"
            )


def validate_model_configuration(
    configuration: ModelConfiguration | None,
    implementation: Implementation,
    *,
    context: str,
) -> None:
    """Validate supported construction paths without runtime or environment I/O."""
    if configuration is not None:
        validate_shared_model_configuration(configuration, context=context)
    bundled = (
        implementation.type == "python"
        and implementation.entrypoint in _OLLAMA_FACTORIES
    )
    if implementation.type == "python" and not bundled:
        return

    def fail(field: str, reason: str) -> None:
        raise SpecificationError(f"{context}: model{field}: {reason}")

    if configuration is None:
        fail("", "a model configuration is required")
        return
    if bundled and configuration.provider != "ollama":
        fail(".provider", "the bundled factory requires an Ollama model")
    if configuration.provider == "ollama":
        for key in configuration.parameters:
            if key not in {"host", "think"}:
                fail(
                    f".parameters.{key}",
                    "unsupported Ollama parameter; allowed: host, think",
                )
        if "think" in configuration.parameters and not isinstance(
            configuration.parameters["think"], bool
        ):
            fail(".parameters.think", "must be a boolean")
        return
    if configuration.provider == "mock":
        for key in configuration.parameters:
            if key not in {"response", "usage"}:
                fail(
                    f".parameters.{key}",
                    "unsupported mock parameter; allowed: response, usage",
                )
        if "response" not in configuration.parameters:
            fail(".parameters.response", "a deterministic response is required")
        usage = configuration.parameters.get("usage", {})
        if not isinstance(usage, Mapping):
            fail(".parameters.usage", "must be an object")

        # Reuse the exact runtime schemas, not a second hand-written subset.
        # Lazy imports keep the SDK/catalog/compiler initialization acyclic.
        from mininet_ai.sdk.contracts import AgentResponse, TokenMetrics

        for field, schema, value in (
            ("response", AgentResponse, configuration.parameters["response"]),
            ("usage", TokenMetrics, usage),
        ):
            try:
                schema.model_validate(value)
            except ValidationError as error:
                # Pydantic messages/context can contain user values. Only expose
                # field locations and controlled schema error types.
                details = "; ".join(
                    f"{'.'.join(str(part) for part in issue['loc']) or field} "
                    f"({issue['type']})"
                    for issue in error.errors(include_url=False, include_input=False)
                )
                requirement = (
                    "must match AgentResponse with an outcome"
                    if field == "response"
                    else "must use known metrics with nonnegative counts/cost and "
                    "totals covering input plus output (including audio)"
                )
                fail(f".parameters.{field}", f"{requirement}: {details}")
        return
    if configuration.parameters:
        fail(
            ".parameters",
            "declarative Agno models do not accept Provider-specific parameters; "
            "use a Python factory for custom configuration",
        )
