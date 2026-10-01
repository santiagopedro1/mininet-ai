"""Paths to shared YAML specifications used as test inputs."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
SPECIFICATIONS = ROOT / "tests" / "fixtures" / "specifications"

COMPILER_MULTILAYER_SPECIFICATION = (
    SPECIFICATIONS / "compiler-multilayer" / "experiment.yaml"
)
MININET_OVS_SMOKE_SPECIFICATION = (
    SPECIFICATIONS / "mininet-ovs-smoke" / "experiment.yaml"
)
