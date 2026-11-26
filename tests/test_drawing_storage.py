import uuid
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from engine.storage import Storage
from server import app


@pytest.fixture
def drawing_storage(tmp_path: Path):
    db_file = tmp_path / "test_drawings.sqlite"
    storage = Storage(db_file)
    yield storage
    storage.close()


def test_drawing_storage_crud(drawing_storage: Storage):
    # 1. Initially empty
    drawings = drawing_storage.get_drawings("NVDA")
    assert drawings == []

    # 2. Save a risk/reward drawing
    drawing_item = {
        "id": "rr_12345",
        "symbol": "NVDA",
        "pane_id": "candle",
        "tool_type": "risk_reward",
        "timeframe_scope": "ALL",
        "data": {
            "entryPrice": 120.5,
            "stopPrice": 115.0,
            "targetPrice": 137.0,
            "startTime": 1700000000,
            "endTime": 1701000000
        }
    }
    saved = drawing_storage.save_drawing(drawing_item)
    assert saved["id"] == "rr_12345"
    assert saved["revision"] == 1

    # 3. Retrieve drawing
    fetched = drawing_storage.get_drawings("NVDA")
    assert len(fetched) == 1
    assert fetched[0]["id"] == "rr_12345"
    assert fetched[0]["symbol"] == "NVDA"
    assert fetched[0]["tool_type"] == "risk_reward"
    assert fetched[0]["revision"] == 1
    assert fetched[0]["data"]["entryPrice"] == 120.5

    # 4. Symbol isolation
    assert drawing_storage.get_drawings("AAPL") == []

    # 5. Update existing drawing
    drawing_item["data"]["entryPrice"] = 125.0
    updated_rec = drawing_storage.save_drawing(drawing_item)
    assert updated_rec["revision"] == 2
    updated = drawing_storage.get_drawings("NVDA")
    assert len(updated) == 1
    assert updated[0]["data"]["entryPrice"] == 125.0
    assert updated[0]["revision"] == 2

    # 6. Soft delete
    assert drawing_storage.delete_drawing("rr_12345", soft_delete=True) is True
    assert drawing_storage.get_drawings("NVDA") == []
    # Can still find with include_deleted=True
    deleted_records = drawing_storage.get_drawings("NVDA", include_deleted=True)
    assert len(deleted_records) == 1
    assert deleted_records[0]["is_deleted"] is True
    assert deleted_records[0]["revision"] == 3


def test_drawing_symbol_immutability_and_tombstone(drawing_storage: Storage):
    # 1. Create drawing for NVDA
    d = {"id": "draw_fixed_1", "symbol": "NVDA", "data": {"price": 100}}
    drawing_storage.save_drawing(d)

    # 2. Attempt to save same ID under AAPL -> Must raise ValueError
    d_conflict = {"id": "draw_fixed_1", "symbol": "AAPL", "data": {"price": 100}}
    with pytest.raises(ValueError, match="owned by symbol 'NVDA'"):
        drawing_storage.save_drawing(d_conflict)

    # 3. Delete drawing
    drawing_storage.delete_drawing("draw_fixed_1")

    # 4. Attempt to revive via standard save -> Must raise ValueError
    with pytest.raises(ValueError, match="deleted .* cannot be modified"):
        drawing_storage.save_drawing(d)


def test_drawing_storage_clear(drawing_storage: Storage):
    drawing_storage.save_drawing({"id": "d1", "symbol": "TSLA", "data": {}})
    drawing_storage.save_drawing({"id": "d2", "symbol": "TSLA", "data": {}})
    drawing_storage.save_drawing({"id": "d3", "symbol": "AAPL", "data": {}})

    assert len(drawing_storage.get_drawings("TSLA")) == 2
    assert len(drawing_storage.get_drawings("AAPL")) == 1

    cleared = drawing_storage.clear_drawings("TSLA")
    assert cleared == 2
    assert len(drawing_storage.get_drawings("TSLA")) == 0
    assert len(drawing_storage.get_drawings("AAPL")) == 1


def test_drawing_api_endpoints():
    client = TestClient(app)
    unique_id = f"api_test_{uuid.uuid4().hex[:8]}"

    # 1. Save drawing via POST
    payload = {
        "id": unique_id,
        "symbol": "SPY",
        "pane_id": "candle",
        "tool_type": "risk_reward",
        "timeframe_scope": "ALL",
        "data": {
            "entryPrice": 590.0,
            "stopPrice": 580.0,
            "targetPrice": 610.0
        }
    }
    res_post = client.post("/api/drawings", json=payload)
    assert res_post.status_code == 200
    res_data = res_post.json()
    assert res_data["status"] == "ok"
    assert res_data["drawing"]["revision"] == 1

    # 2. Get drawings via GET
    res_get = client.get("/api/drawings?symbol=SPY")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["status"] == "ok"
    assert any(d["id"] == unique_id for d in data["drawings"])

    # 3. Ownership conflict returns 409
    res_conflict = client.post("/api/drawings", json={
        "id": unique_id,
        "symbol": "QQQ",
        "data": {}
    })
    assert res_conflict.status_code == 409

    # 4. Delete drawing via DELETE /api/drawings/{id}
    res_del = client.delete(f"/api/drawings/{unique_id}")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] is True

    # 5. Verify deleted
    res_verify = client.get("/api/drawings?symbol=SPY")
    assert not any(d["id"] == unique_id for d in res_verify.json()["drawings"])


def test_drawing_api_symbol_normalization_parity():
    client = TestClient(app)
    unique_id = f"norm_test_{uuid.uuid4().hex[:8]}"

    # Save with bare symbol 'SPY' -> normalized to 'AMEX:SPY'
    res_post = client.post("/api/drawings", json={
        "id": unique_id,
        "symbol": "SPY",
        "pane_id": "candle",
        "tool_type": "risk_reward",
        "timeframe_scope": "ALL",
        "data": {"entryPrice": 600.0, "stopPrice": 590.0, "targetPrice": 620.0}
    })
    assert res_post.status_code == 200
    assert res_post.json()["drawing"]["symbol"] == "AMEX:SPY"

    # Fetch using prefixed symbol 'AMEX:SPY' finds the exact same drawing
    res_get_prefixed = client.get("/api/drawings?symbol=AMEX:SPY")
    assert res_get_prefixed.status_code == 200
    assert res_get_prefixed.json()["symbol"] == "AMEX:SPY"
    assert any(d["id"] == unique_id for d in res_get_prefixed.json()["drawings"])

    # Cleanup
    res_del = client.delete(f"/api/drawings/{unique_id}")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] is True
