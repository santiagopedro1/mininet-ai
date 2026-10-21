"""Resolve shared model sources once per compilation into executable settings."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from mininet_ai.errors import SpecificationError
from mininet_ai.specification.loader import LoadedExperiment
from mininet_ai.specification.model_sources import read_model_configurations
from mininet_ai.specification.model_validation import validate_model_configuration
from mininet_ai.specification.models import ModelConfigurations, ModelReference


def resolve_models(
    loaded: LoadedExperiment,
) -> tuple[LoadedExperiment, dict[str, Any]]:
    files: dict[Path, ModelConfigurations] = {}
    blueprints = []
    provenance = {}
    for index, blueprint in enumerate(loaded.blueprints):
        source = (
            loaded.blueprint_sources[index]
            if loaded.blueprint_sources
            else loaded.source
        )
        model = blueprint.model
        if isinstance(model, ModelReference):
            path = (source.parent / model.file).resolve()
            context = (
                f"blueprint {blueprint.metadata.name!r} ({source}) model reference "
                f"{path}, entry {model.entry!r}"
            )
            try:
                if path not in files:
                    files[path] = read_model_configurations(path)
                configurations = files[path].models
                for name, base in configurations.items():
                    try:
                        validate_model_configuration(blueprint, base)
                    except SpecificationError as error:
                        raise SpecificationError(f"models.{name}: {error}") from error
                if model.entry not in configurations:
                    raise SpecificationError(
                        "model.entry: selected entry does not exist"
                    )
                effective = configurations[model.entry].model_copy(deep=True)
                validate_model_configuration(blueprint, effective)
            except SpecificationError as error:
                raise SpecificationError(f"{context}: {error}") from error
            provenance[blueprint.metadata.name] = {
                "source": str(source),
                "file": str(path),
                "entry": model.entry,
            }
            blueprint = blueprint.model_copy(update={"model": effective})
        elif model is not None:
            validate_model_configuration(blueprint, model)
        blueprints.append(blueprint)
    return replace(loaded, blueprints=tuple(blueprints)), provenance
