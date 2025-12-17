"""WebSocket handler for terminal streaming."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from compose_farm.web.streaming import tasks

router = APIRouter()


@router.websocket("/ws/terminal/{task_id}")
async def terminal_websocket(websocket: WebSocket, task_id: str) -> None:
    """WebSocket endpoint for terminal streaming."""
    await websocket.accept()

    if task_id not in tasks:
        await websocket.send_text("\x1b[31mError: Task not found\x1b[0m\r\n")
        await websocket.close(code=4004)
        return

    task = tasks[task_id]
    sent_count = 0

    try:
        while True:
            # Send any new output
            output = task["output"]
            while sent_count < len(output):
                await websocket.send_text(output[sent_count])
                sent_count += 1

            # Check if task is done
            if task["status"] in ("completed", "failed"):
                # Send any remaining output
                while sent_count < len(output):
                    await websocket.send_text(output[sent_count])
                    sent_count += 1

                # Send completion message
                if task["status"] == "completed":
                    await websocket.send_text("\r\n\x1b[32m[Done]\x1b[0m\r\n")
                else:
                    await websocket.send_text("\r\n\x1b[31m[Failed]\x1b[0m\r\n")
                break

            # Small delay to avoid busy loop
            await asyncio.sleep(0.05)

    except WebSocketDisconnect:
        pass
    finally:
        # Clean up task after a delay (allow reconnection)
        await asyncio.sleep(60)
        tasks.pop(task_id, None)
