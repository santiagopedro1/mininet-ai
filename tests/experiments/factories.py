"""Offline Agno adapter for the link-failure experiment's public seams."""

from agno.agent import Agent

from mininet_ai.agents.agno import DeterministicAgnoModel
from mininet_ai.sdk import AgentExecutionDefinition, AgentResponse
from tests.experiments import example


def create_recovery_agent(definition: AgentExecutionDefinition) -> Agent:
    return recovery_agent(definition, example.build_scenario(seed=7))


def create_seed_zero_recovery_agent(definition: AgentExecutionDefinition) -> Agent:
    return recovery_agent(definition, example.build_scenario(seed=0))


def recovery_agent(definition: AgentExecutionDefinition, scenario) -> Agent:
    switch = definition.instance.attachment.targets[0]
    rules = scenario.rules(switch, failed=True)
    return Agent(
        model=DeterministicAgnoModel(
            {
                "message": "Install alternate-path forwarding",
                "proposals": [
                    {
                        "id": f"flow-{i}",
                        "capability": "openflow.flow.install",
                        "target": switch,
                        "arguments": rule,
                    }
                    for i, rule in enumerate(rules)
                ],
            }
        ),
        output_schema=AgentResponse,
        telemetry=False,
    )
