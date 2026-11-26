"""Real-time WebSocket streaming endpoint (/ws/live)."""

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import api.deps as deps
from api.bootstrap import logger
from api.config import WS_HEARTBEAT_INTERVAL, is_allowed_origin

router = APIRouter()


@router.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """Real-time multiplexed quote, bar, and status stream with bounded queues."""
    headers = getattr(websocket, "headers", None)
    origin = headers.get("origin") if headers else None
    if origin and not is_allowed_origin(origin):
        logger.warning("Rejected WebSocket connection from unauthorized origin: %s", origin)
        await websocket.close(code=1008, reason="Unauthorized origin")
        return

    if deps.is_desktop_mode():
        if not deps._dev_no_auth:
            token = websocket.query_params.get("token") or websocket.headers.get("x-session-token") or websocket.headers.get("x-terminal-token")
            if not token:
                auth_header = websocket.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:].strip()
            if not deps.verify_token(token):
                logger.warning("Rejected unauthorized WebSocket connection: missing or invalid session token")
                await websocket.close(code=1008, reason="Unauthorized: invalid or missing session token")
                return

    await deps.manager.connect(websocket)
    try:
        await websocket.send_json({"type": "status", "status": deps.current_status})
        if deps.tv_client and deps.tv_client.states:
            for sym, q in list(deps.tv_client.states.items()):
                if q.get("price", 0) > 0 or q.get("volume", 0) > 0:
                    await websocket.send_json({
                        "type": "quote",
                        "symbol": sym,
                        "data": q
                    })

        async def client_reader():
            try:
                while True:
                    await websocket.receive_text()
            except (WebSocketDisconnect, asyncio.CancelledError):
                logger.debug("Client reader disconnected cleanly")
            except Exception as e:
                logger.debug("Client reader error: %s", e)

        async def heartbeat_sender():
            try:
                while True:
                    await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                    q = deps.manager.client_queues.get(websocket)
                    if q is not None:
                        try:
                            q.put_nowait({"type": "heartbeat", "t": time.time()})
                        except asyncio.QueueFull:
                            pass
            except (asyncio.CancelledError, WebSocketDisconnect):
                logger.debug("Heartbeat sender disconnected cleanly")
            except Exception as e:
                logger.debug("Heartbeat sender error: %s", e)

        writer_coro = deps.manager.client_writer(websocket)
        writer_task = asyncio.create_task(writer_coro) # tracked
        reader_coro = client_reader()
        reader_task = asyncio.create_task(reader_coro) # tracked
        heartbeat_coro = heartbeat_sender()
        heartbeat_task = asyncio.create_task(heartbeat_coro) # tracked

        done, pending = await asyncio.wait(
            [writer_task, reader_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            try:
                t.result()
            except (asyncio.CancelledError, WebSocketDisconnect):
                logger.debug("Task terminated on disconnect")
            except Exception as e:
                logger.debug("WebSocket task ended with: %s", e)
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                logger.debug("Pending task cancelled")
    except (WebSocketDisconnect, asyncio.CancelledError):
        logger.debug("Live endpoint disconnected")
    except Exception as e:
        logger.debug("WebSocket live endpoint error: %s", e)
    finally:
        deps.manager.disconnect(websocket)
