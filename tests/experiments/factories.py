"""Offline Agno adapter for the link-failure experiment's public seams."""

from agno.agent import Agent

from mininet_ai.agents.agno import DeterministicAgnoModel
from mininet_ai.experiments.link_failure import build_scenario
from mininet_ai.sdk import AgentExecutionDefinition, AgentResponse


def create_recovery_agent(definition: AgentExecutionDefinition) -> Agent:
    switch = definition.instance.attachment.targets[0]
    rules = build_scenario(seed=7).rules(switch, failed=True)
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
