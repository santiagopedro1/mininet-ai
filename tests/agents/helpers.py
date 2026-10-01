from __future__ import annotations

from mininet_ai.sdk import AgentResponse


def mapping_agent(context):
    return {
        "message": f"inspected {context.agent_id}",
        "metadata": {"runId": context.run_id},
    }


def response_agent(context):
    return AgentResponse(message=context.intent)


def invalid_agent(context):
    return {"unexpected": context.agent_id}


def failing_agent(context):
    raise RuntimeError(f"failed {context.agent_id}")


def timeout_agent(context):
    raise TimeoutError(f"timed out {context.agent_id}")


not_callable = "agent"
