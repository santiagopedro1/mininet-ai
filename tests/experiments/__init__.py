"""Load the example-local runner without adding it to the installed package."""

from importlib import import_module

example = import_module("examples.link-failure-latency.run")
