"""Adversarial WebSocket Stress and Concurrency Harness for Milestone 2.

This module empirically challenges /ws/live and ConnectionManager under:
1. High tick volume (1,000 rapid messages) to a slow consumer (bounded queue & drop-oldest verification).
2. Concurrency stress with 50 rapid connecting/disconnecting clients (task and queue leak audit).
3. Abrupt disconnect / connection abort edge cases (leak reproduction).
"""

import asyncio
import random
import time
import sys
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import WebSocketDisconnect

import server
from server import ConnectionManager


async def stress_bounded_queue_1000_messages() -> Dict[str, Any]:
    """Adversarial Task 1: Push 1,000 rapid messages to a slow client."""
    mgr = ConnectionManager(max_queue_size=100)
    received = []

    class SlowMockWS:
        def __init__(self):
            self.closed = False

        async def accept(self):
            return None

        async def send_json(self, data):
            # Slow consumer: simulates network delay (1ms per message)
            await asyncio.sleep(0.001)
            received.append(data)

        async def receive_text(self):
            await asyncio.sleep(100)
            return ""

        async def close(self, code=1000, reason=""):
            self.closed = True

    ws = SlowMockWS()
    await mgr.connect(ws)
    writer_task = asyncio.create_task(mgr.client_writer(ws))

    start_time = time.perf_counter()
    max_observed_qsize = 0

    for i in range(1000):
        mgr.broadcast_nowait({"seq": i, "price": 68000.0 + i, "type": "tick"})
        qsize = mgr.client_queues[ws].qsize()
        if qsize > max_observed_qsize:
            max_observed_qsize = qsize
        assert qsize <= 100, f"Queue size exceeded bound: {qsize} > 100"

    push_duration = time.perf_counter() - start_time

    # Allow writer to drain remaining queue items
    while mgr.client_queues[ws].qsize() > 0:
        await asyncio.sleep(0.01)

    writer_task.cancel()
    try:
        await writer_task
    except asyncio.CancelledError:
        logger.debug("Writer task cancelled cleanly")

    received_seqs = [m["seq"] for m in received if "seq" in m]
    first_seq = received_seqs[0] if received_seqs else None
    last_seq = received_seqs[-1] if received_seqs else None
    dropped_count = 1000 - len(received_seqs)

    assert max_observed_qsize <= 100, f"Max queue size exceeded 100: {max_observed_qsize}"
    assert last_seq == 999, f"Latest message was not delivered: {last_seq} != 999"
    assert dropped_count > 0, "Expected older messages to be dropped"

    return {
        "messages_pushed": 1000,
        "push_duration_ms": push_duration * 1000,
        "max_observed_qsize": max_observed_qsize,
        "received_count": len(received_seqs),
        "dropped_count": dropped_count,
        "first_seq_received": first_seq,
        "last_seq_received": last_seq,
        "passed": True,
    }


async def stress_50_concurrent_clients_rapid_disconnect() -> Dict[str, Any]:
    """Adversarial Task 2: 50 concurrent clients connecting and disconnecting rapidly."""
    server.manager.client_queues.clear()
    initial_tasks = {t for t in asyncio.all_tasks() if not t.done()}

    clients_count = 50

    class RapidWS:
        def __init__(self, cid: int):
            self.cid = cid
            self.closed = False
            self.connected = True
            self.received = []

        async def accept(self):
            await asyncio.sleep(random.uniform(0.0005, 0.002))

        async def send_json(self, data):
            if not self.connected:
                raise WebSocketDisconnect(code=1006)
            self.received.append(data)
            await asyncio.sleep(random.uniform(0.0001, 0.0005))

        async def receive_text(self):
            # Normal rapid disconnect after receiving some ticks
            lifetime = random.uniform(0.01, 0.04)
            await asyncio.sleep(lifetime)
            self.connected = False
            raise WebSocketDisconnect(code=1000)

        async def close(self, code=1000, reason=""):
            self.closed = True
            self.connected = False

    clients = [RapidWS(i) for i in range(clients_count)]
    endpoint_tasks = [
        asyncio.create_task(server.websocket_live_endpoint(c)) for c in clients
    ]

    # Concurrently broadcast 500 messages while clients connect and disconnect
    for tick in range(500):
        server.manager.broadcast_nowait({"type": "quote", "tick": tick})
        if tick % 50 == 0:
            await asyncio.sleep(0.002)

    await asyncio.gather(*endpoint_tasks, return_exceptions=True)
    await asyncio.sleep(0.05)

    final_queues = len(server.manager.client_queues)
    current_tasks = {t for t in asyncio.all_tasks() if not t.done()}
    lingering_tasks = current_tasks - initial_tasks - {asyncio.current_task()}

    return {
        "clients_count": clients_count,
        "active_queues_remaining": final_queues,
        "dangling_tasks_count": len(lingering_tasks),
        "passed": (final_queues == 0 and len(lingering_tasks) == 0),
    }


async def test_handshake_abort_defect_reproduction() -> Dict[str, Any]:
    """Adversarial Defect Check: Verify client disconnect during initial status frame."""
    server.manager.client_queues.clear()

    class AbortiveWS:
        def __init__(self, cid: int):
            self.cid = cid
            self.closed = False

        async def accept(self):
            return None

        async def send_json(self, data):
            # Aborts connection immediately when server attempts initial status send
            raise WebSocketDisconnect(code=1006)

        async def receive_text(self):
            raise WebSocketDisconnect(code=1006)

        async def close(self, code=1000, reason=""):
            self.closed = True

    aborted_client = AbortiveWS(cid=999)

    exception_raised = False
    try:
        await server.websocket_live_endpoint(aborted_client)
    except WebSocketDisconnect:
        exception_raised = True

    leaked = aborted_client in server.manager.client_queues
    leaked_count = len(server.manager.client_queues)

    return {
        "exception_raised": exception_raised,
        "websocket_leaked": leaked,
        "leaked_queues_count": leaked_count,
        "defect_confirmed": leaked,
    }


async def main():
    print("=" * 60)
    print("MILESTONE 2 ADVERSARIAL STRESS TEST SUITE")
    print("=" * 60)

    print("\n[1/3] Running Task 1: 1,000 rapid messages to slow consumer...")
    res1 = await stress_bounded_queue_1000_messages()
    print(f"  - Pushed: {res1['messages_pushed']} in {res1['push_duration_ms']:.2f}ms")
    print(f"  - Max observed queue size: {res1['max_observed_qsize']} (bound: 100)")
    print(f"  - Dropped count: {res1['dropped_count']} messages")
    print(f"  - Sequence range delivered: [{res1['first_seq_received']} .. {res1['last_seq_received']}]")
    print(f"  - Status: {'PASSED' if res1['passed'] else 'FAILED'}")

    print("\n[2/3] Running Task 2: 50 concurrent rapid connecting/disconnecting clients...")
    res2 = await stress_50_concurrent_clients_rapid_disconnect()
    print(f"  - Concurrent clients: {res2['clients_count']}")
    print(f"  - Active queues remaining: {res2['active_queues_remaining']}")
    print(f"  - Dangling tasks count: {res2['dangling_tasks_count']}")
    print(f"  - Status: {'PASSED' if res2['passed'] else 'FAILED'}")

    print("\n[3/3] Running Defect Reproduction: Abrupt disconnect during handshake...")
    res3 = await test_handshake_abort_defect_reproduction()
    print(f"  - WebSocketDisconnect raised at line 251: {res3['exception_raised']}")
    print(f"  - Client leaked in manager.client_queues: {res3['websocket_leaked']}")
    print(f"  - Leaked queues in manager: {res3['leaked_queues_count']}")
    if res3["defect_confirmed"]:
        print("  - VULNERABILITY CONFIRMED: Queue and socket leak on abrupt handshake disconnect.")
    else:
        print("  - No defect observed.")

    print("\n" + "=" * 60)
    if res1["passed"] and res2["passed"] and not res3["defect_confirmed"]:
        print("OVERALL VERDICT: CONFIRMED")
    else:
        print("OVERALL VERDICT: DEFECT_DETECTED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
