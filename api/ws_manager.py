"""WebSocket connection fan-out and bounded per-client queue manager."""

import asyncio
import json
import threading
from typing import Any, Dict, Set

from fastapi import WebSocket

from api.bootstrap import logger

class ConnectionManager:
    """Manages active WebSocket client connections with per-client bounded queues and drop-oldest strategy."""

    def __init__(self, max_queue_size: int = 100):
        self.max_queue_size = max_queue_size
        self.client_queues: Dict[WebSocket, asyncio.Queue] = {}
        self.client_loops: Dict[WebSocket, asyncio.AbstractEventLoop] = {}
        self._seq_lock = threading.Lock()
        self._bar_seq: Dict[str, int] = {}

    def next_bar_seq(self, symbol: str) -> int:
        """Returns a thread-safe monotonically increasing bar sequence number per symbol."""
        key = str(symbol).strip().upper()
        with self._seq_lock:
            seq = self._bar_seq.get(key, 0) + 1
            self._bar_seq[key] = seq
            return seq

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.client_queues[websocket] = queue
        try:
            self.client_loops[websocket] = asyncio.get_running_loop()
        except RuntimeError as loop_err:
            logger.debug("Failed to acquire running loop for websocket: %s", loop_err)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.client_queues:
            del self.client_queues[websocket]
        if websocket in self.client_loops:
            del self.client_loops[websocket]

    def broadcast_nowait(self, message: Dict[str, Any]) -> None:
        """Non-blocking drop-oldest broadcast to all connected client queues."""
        for ws, queue in list(self.client_queues.items()):
            loop = self.client_loops.get(ws) or getattr(queue, "_loop", None)

            def _push(q=queue, m=message):
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        logger.debug("Broadcaster queue already drained.")
                try:
                    q.put_nowait(m)
                except asyncio.QueueFull:
                    logger.debug("Broadcaster queue full; message dropped.")

            if loop and loop.is_running():
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None
                if current_loop is loop:
                    _push()
                else:
                    loop.call_soon_threadsafe(_push)
            else:
                _push()

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Awaitable broadcast wrapper for async callers and testing."""
        self.broadcast_nowait(message)

    async def client_writer(self, websocket: WebSocket) -> None:
        """Dedicated writer task for a single WebSocket connection."""
        queue = self.client_queues.get(websocket)
        if not queue:
            return
        try:
            while True:
                await asyncio.sleep(0)
                msg = await queue.get()
                await websocket.send_json(msg)
                queue.task_done()
        except (WebSocketDisconnect, asyncio.CancelledError):
            logger.debug("Client writer disconnected")
        except Exception as e:
            logger.debug("Client writer error on %s: %s", websocket, e)
        finally:
            self.disconnect(websocket)

    async def close_all(self) -> None:
        """Gracefully closes all active client connections during server shutdown."""
        for ws in list(self.client_queues.keys()):
            try:
                await ws.close(code=1001, reason="Server shutting down")
            except Exception as e:
                logger.debug("Client websocket close error: %s", e)
        self.client_queues.clear()
        self.client_loops.clear()


manager = ConnectionManager(max_queue_size=100)
