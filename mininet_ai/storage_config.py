"""Explicit, strict path selection for an owning run."""

from pathlib import Path

import click
import yaml
from typer.core import TyperCommand

STORAGE_KEYS = frozenset({"log_file", "ledger_db", "shared_state_db", "agno_db"})


def load_storage_config(path: Path | None) -> dict[str, Path]:
    """Read only the supplied file; resolve each override relative to that file."""
    if path is None:
        return {}
    try:
        with path.open(encoding="utf-8") as stream:
            node = yaml.compose(stream, Loader=yaml.SafeLoader)
        if not isinstance(node, yaml.MappingNode):
            raise TypeError("expected a mapping of storage path keys")
        paths: dict[str, Path] = {}
        for key, value in node.value:
            if (
                not isinstance(key, yaml.ScalarNode)
                or key.tag != "tag:yaml.org,2002:str"
                or key.value not in STORAGE_KEYS
            ):
                raise ValueError(
                    "unknown storage key; allowed: " + ", ".join(sorted(STORAGE_KEYS))
                )
            if key.value in paths:
                raise ValueError(f"duplicate storage key: {key.value}")
            if (
                not isinstance(value, yaml.ScalarNode)
                or value.tag != "tag:yaml.org,2002:str"
                or not value.value.strip()
                or "\x00" in value.value
            ):
                raise ValueError(f"{key.value} must be a nonempty path string")
            selected = Path(value.value)
            paths[key.value] = (
                selected
                if selected.is_absolute()
                else path.absolute().parent / selected
            )
        return paths
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as error:
        raise click.BadParameter(
            f"Invalid storage configuration {path}: {error}",
            param_hint="--storage-config",
        ) from error


class StorageCommand(TyperCommand):
    """Reject removed alpha selectors before Click can obscure their replacement."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        removed = {"--agno-db", "--shared-state-db"}
        if self.name == "run":
            removed |= {"--artifact-root", "--log-file", "--ledger-db"}
        # Walk option arities so literal intent/path values are not mistaken for flags.
        options = {
            option: parameter
            for parameter in self.params
            if isinstance(parameter, click.Option)
            for option in parameter.opts
        }
        index = 0
        while index < len(args):
            argument = args[index]
            if argument == "--":
                break
            flag = argument.split("=", 1)[0]
            if flag in removed:
                replacement = (
                    "--results-dir"
                    if flag == "--artifact-root"
                    else f"--storage-config FILE with {flag[2:].replace('-', '_')}"
                )
                owner = (
                    " Storage is selected on the owning run.\n"
                    if self.name == "invoke"
                    else " "
                )
                raise click.UsageError(
                    f"{flag} was removed.{owner}Use {replacement}.", ctx
                )
            parameter = options.get(flag)
            if parameter is not None and not parameter.is_flag and "=" not in argument:
                index += parameter.nargs
            index += 1
        return super().parse_args(ctx, args)
