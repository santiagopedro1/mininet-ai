"""Shared incident-operation accounting, independent of treatment decisions."""

from __future__ import annotations

import asyncio

NS = 1_000_000_000


async def record_inference(operation, call, clock, limit):
    try:
        value = await operation()
        call.update(value, status="completed")
        return value
    except asyncio.CancelledError:
        call["status"] = "locally_cancelled_remote_unknown"
        raise
    except Exception as error:
        call.update(
            status="failed",
            error=str(error),
            response=getattr(error, "native_response", None),
        )
        raise
    finally:
        call["completed_ns"] = clock.now_ns()
        call["latency_seconds"] = (call["completed_ns"] - call["started_ns"]) / NS
        call["late"] = call["completed_ns"] > limit


async def run_action(operation, action, clock, limit, bounded):
    async def execute():
        try:
            result = await operation()
        except Exception as error:
            result = {"success": False, "error": str(error)}
        action.update(
            result=result,
            completed_ns=clock.now_ns(),
            late=clock.now_ns() > limit,
        )
        return result

    try:
        return await bounded("action", execute(), limit)
    except TimeoutError:
        if action["result"] is None:
            action["result"] = {
                "success": None,
                "status": "cancelled_execution_unknown",
            }
        raise
