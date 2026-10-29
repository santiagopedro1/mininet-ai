"""Resolve shared model sources once per compilation into executable settings."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mininet_ai.errors import SpecificationError
from mininet_ai.specification.loader import LoadedExperiment
from mininet_ai.specification.model_sources import (
    ModelConfigurationSource,
    read_model_configurations,
)
from mininet_ai.specification.model_validation import validate_model_configuration
from mininet_ai.specification.models import ModelConfiguration, ModelReference


def resolve_models(
    loaded: LoadedExperiment,
) -> tuple[LoadedExperiment, dict[str, Any]]:
    files: dict[Path, ModelConfigurationSource] = {}
    blueprints = []
    provenance = {}
    for document in loaded._blueprint_documents:
        blueprint = document.blueprint
        source = document.source
        blueprint_context = f"{source}: blueprint {blueprint.metadata.name!r}"
        source_fields = document.locations
        model = blueprint.model
        if isinstance(model, ModelReference):
            path = (source.parent / model.file).resolve()
            context = (
                f"blueprint {blueprint.metadata.name!r} ({source}) model reference "
                f"{path}, entry {model.entry!r}"
            )
            if "model" in source_fields:
                context += f" ({source_fields['model']})"
            try:
                if path not in files:
                    files[path] = read_model_configurations(path)
                configurations = files[path].models
                if model.entry not in configurations:
                    raise SpecificationError(
                        "model.entry: selected entry does not exist"
                    )
                base = configurations[model.entry]
                location = files[path].entry_locations[model.entry]
                validate_model_configuration(
                    base,
                    blueprint.implementation,
                    context=f"models.{model.entry} base{location}",
                    locations=files[path].field_locations[model.entry],
                    execution_kind=(
                        blueprint.execution.kind if blueprint.execution else None
                    ),
                )
                effective_fields = dict(files[path].field_locations[model.entry])
                # Programmatic overrides have no YAML mark. Do not misattribute
                # their values to the base field's source position.
                overridden_fields = [
                    f"parameters.{key}" for key in model.overrides.get("parameters", {})
                ]
                if "name" in model.overrides:
                    overridden_fields.append("name")
                for field in overridden_fields:
                    origin = f"model.overrides.{field}"
                    effective_fields[f"model.{field}"] = source_fields.get(
                        origin, origin
                    )
                effective = base.model_copy(deep=True)
                if "name" in model.overrides:
                    effective.name = model.overrides["name"]
                effective.parameters.update(model.overrides.get("parameters", {}))
                # Structural rules (including explicit endpoint validation) still
                # apply after replacement. Never expose validation input values.
                try:
                    effective = ModelConfiguration.model_validate(
                        effective.model_dump()
                    )
                except ValidationError as error:
                    details = []
                    for issue in error.errors(include_url=False, include_input=False):
                        field = "model." + ".".join(map(str, issue["loc"]))
                        position = effective_fields.get(field, field)
                        details.append(f"{position}: {issue['msg']}")
                    raise SpecificationError(
                        f"models.{model.entry} effective{location}: "
                        + "; ".join(details)
                    ) from error
                validate_model_configuration(
                    effective,
                    blueprint.implementation,
                    context=f"models.{model.entry} effective{location}",
                    locations=effective_fields,
                    execution_kind=(
                        blueprint.execution.kind if blueprint.execution else None
                    ),
                )
            except SpecificationError as error:
                raise SpecificationError(f"{context}: {error}") from error
            provenance[blueprint.metadata.name] = {
                "source": str(source),
                "file": str(path),
                "entry": model.entry,
                "overrides": deepcopy(model.overrides),
            }
            blueprint = blueprint.model_copy(update={"model": effective})
        else:
            validate_model_configuration(
                model,
                blueprint.implementation,
                context=blueprint_context,
                locations=source_fields,
                execution_kind=(
                    blueprint.execution.kind if blueprint.execution else None
                ),
            )
        blueprints.append(blueprint)
    return replace(loaded, blueprints=tuple(blueprints)), provenance
