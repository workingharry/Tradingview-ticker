"""Real-browser end-to-end tests for drawing tools across Chrome (CDP) and Safari WebKit (safaridriver)."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "drawing_regression_baseline.json"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _BackendServer:
    def __init__(self):
        self.port = _find_free_port()
        self.tmp_dir = tempfile.mkdtemp(prefix="mst_e2e_be_")
        self.db_path = os.path.join(self.tmp_dir, "test.sqlite")
        self.proc = None

    def __enter__(self):
        env = os.environ.copy()
        env["TERMINAL_DB_PATH"] = self.db_path
        self.proc = subprocess.Popen(
            [sys.executable, "server.py", "--desktop-mode", "--port", str(self.port), "--dev-no-auth"],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=1) as resp:
                    if resp.status == 200:
                        return self
            except Exception:
                time.sleep(0.25)
        raise RuntimeError(f"Backend server failed to become ready on port {self.port}")

    def __exit__(self, exc_type, exc, tb):
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), 15)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), 9)
                except Exception:
                    pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class ChromeBrowserDriver:
    name = "chrome"

    def __init__(self, url: str):
        self.url = url
        self.port = _find_free_port()
        self.profile_dir = tempfile.mkdtemp(prefix="mst_e2e_chrome_")
        self.proc = None
        self.loop = asyncio.new_event_loop()
        self.ws = None
        self.mid = 0

    def __enter__(self):
        if not os.path.exists(CHROME_BIN):
            raise RuntimeError(f"Google Chrome binary not found at {CHROME_BIN}")
        self.proc = subprocess.Popen(
            [
                CHROME_BIN,
                "--headless=new",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile_dir}",
                "--window-size=1440,900",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        ws_url = None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=1) as resp:
                    tabs = json.loads(resp.read().decode())
                    for t in tabs:
                        if t.get("type") == "page":
                            ws_url = t["webSocketDebuggerUrl"]
                            break
                    if ws_url:
                        break
            except Exception:
                time.sleep(0.2)
        if not ws_url:
            raise RuntimeError("Chrome CDP websocket did not appear")

        async def _connect():
            self.ws = await websockets.connect(ws_url, max_size=10_000_000)
            await self._cdp("Runtime.enable")
            await self._cdp("Page.enable")
            await self._cdp("Page.navigate", {"url": self.url})

        self.loop.run_until_complete(_connect())
        return self

    async def _cdp(self, method: str, params=None):
        self.mid += 1
        cid = self.mid
        await self.ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            data = json.loads(await self.ws.recv())
            if data.get("id") == cid:
                if "error" in data:
                    raise RuntimeError(f"CDP error {method}: {data['error']}")
                return data.get("result", {})

    def eval(self, js_expr: str):
        async def _eval():
            res = await self._cdp(
                "Runtime.evaluate",
                {"expression": js_expr, "returnByValue": True, "awaitPromise": True},
            )
            if "exceptionDetails" in res:
                raise RuntimeError(f"Browser JS error: {res['exceptionDetails']}")
            return res.get("result", {}).get("value")

        return self.loop.run_until_complete(_eval())

    def move(self, x: float, y: float):
        async def _move():
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": float(x), "y": float(y), "button": "none", "buttons": 0
            })
            await asyncio.sleep(0.06)

        self.loop.run_until_complete(_move())

    def click(self, x: float, y: float):
        async def _click():
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": float(x), "y": float(y), "button": "none", "buttons": 0
            })
            await asyncio.sleep(0.04)
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": float(x), "y": float(y), "button": "left", "buttons": 1, "clickCount": 1
            })
            await asyncio.sleep(0.04)
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": float(x), "y": float(y), "button": "left", "buttons": 0, "clickCount": 1
            })
            await asyncio.sleep(0.12)

        self.loop.run_until_complete(_click())

    def drag(self, x1: float, y1: float, x2: float, y2: float):
        async def _drag():
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": float(x1), "y": float(y1), "button": "none", "buttons": 0
            })
            await asyncio.sleep(0.04)
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": float(x1), "y": float(y1), "button": "left", "buttons": 1, "clickCount": 1
            })
            await asyncio.sleep(0.04)
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": float((x1 + x2) / 2), "y": float((y1 + y2) / 2), "button": "left", "buttons": 1
            })
            await asyncio.sleep(0.04)
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": float(x2), "y": float(y2), "button": "left", "buttons": 1
            })
            await asyncio.sleep(0.04)
            await self._cdp("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": float(x2), "y": float(y2), "button": "left", "buttons": 0, "clickCount": 1
            })
            await asyncio.sleep(0.15)

        self.loop.run_until_complete(_drag())

    def press_escape(self):
        async def _esc():
            await self._cdp("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27
            })
            await asyncio.sleep(0.04)
            await self._cdp("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27
            })
            await asyncio.sleep(0.12)

        self.loop.run_until_complete(_esc())

    def __exit__(self, exc_type, exc, tb):
        if self.ws:
            try:
                self.loop.run_until_complete(self.ws.close())
            except Exception:
                pass
        try:
            self.loop.close()
        except Exception:
            pass
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), 15)
            except Exception:
                pass
        shutil.rmtree(self.profile_dir, ignore_errors=True)


class SafariWebKitDriver:
    name = "webkit"

    def __init__(self, url: str):
        self.url = url
        self.port = _find_free_port()
        self.proc = None
        self.session_id = None

    def _wd(self, method: str, path: str, body=None, timeout=15):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"Safari WebDriver {method} {path} failed ({e.code}): {err_body}")

    def __enter__(self):
        self.proc = subprocess.Popen(
            ["safaridriver", "-p", str(self.port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = self.proc.stdout.read().decode("utf-8", "replace") if self.proc.stdout else ""
                raise RuntimeError(f"safaridriver exited early ({self.proc.returncode}): {out}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.15)
        res = self._wd("POST", "/session", {"capabilities": {"alwaysMatch": {"browserName": "safari"}}}, timeout=20)
        self.session_id = res["value"]["sessionId"]
        self._wd("POST", f"/session/{self.session_id}/window/rect", {"x": 0, "y": 0, "width": 1440, "height": 900})
        self._wd("POST", f"/session/{self.session_id}/url", {"url": self.url})
        # Compensate for Safari titlebar/toolbar height so inner viewport matches 1440x900 if possible
        dims = self.eval("({ iw: window.innerWidth, ih: window.innerHeight, ow: window.outerWidth, oh: window.outerHeight })")
        if isinstance(dims, dict) and dims.get("ih"):
            extra_h = max(0, 900 - dims["ih"])
            extra_w = max(0, 1440 - dims["iw"])
            if extra_h or extra_w:
                self._wd("POST", f"/session/{self.session_id}/window/rect", {"x": 0, "y": 0, "width": 1440 + extra_w, "height": 900 + extra_h})
        return self

    def eval(self, js_expr: str):
        stripped = js_expr.strip()
        if ";" in stripped and not (stripped.startswith("(") and stripped.endswith(")")):
            body = stripped
        else:
            body = f"return ({stripped});"
        wrapped = (
            "var __done = arguments[arguments.length - 1];\n"
            "Promise.resolve().then(function() { " + body + " })\n"
            "  .then(function(val) { __done({ ok: true, value: val }); })\n"
            "  .catch(function(err) { __done({ ok: false, error: String(err && err.stack ? err.stack : err) }); });"
        )
        res = self._wd("POST", f"/session/{self.session_id}/execute/async", {"script": wrapped, "args": []})
        val = res.get("value")
        if isinstance(val, dict) and not val.get("ok"):
            raise RuntimeError(f"Safari JS error: {val.get('error')}")
        return val.get("value") if isinstance(val, dict) else val

    def _actions(self, actions_payload):
        self._wd("POST", f"/session/{self.session_id}/actions", {"actions": actions_payload})
        time.sleep(0.12)

    def move(self, x: float, y: float):
        self._actions([{
            "type": "pointer",
            "id": "mouse",
            "parameters": {"pointerType": "mouse"},
            "actions": [
                {"type": "pointerMove", "duration": 20, "x": int(round(x)), "y": int(round(y)), "origin": "viewport"},
            ],
        }])

    def click(self, x: float, y: float):
        self._actions([{
            "type": "pointer",
            "id": "mouse",
            "parameters": {"pointerType": "mouse"},
            "actions": [
                {"type": "pointerMove", "duration": 20, "x": int(round(x)), "y": int(round(y)), "origin": "viewport"},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
            ],
        }])

    def drag(self, x1: float, y1: float, x2: float, y2: float):
        self._actions([{
            "type": "pointer",
            "id": "mouse",
            "parameters": {"pointerType": "mouse"},
            "actions": [
                {"type": "pointerMove", "duration": 20, "x": int(round(x1)), "y": int(round(y1)), "origin": "viewport"},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 40, "x": int(round((x1 + x2) / 2)), "y": int(round((y1 + y2) / 2)), "origin": "viewport"},
                {"type": "pointerMove", "duration": 40, "x": int(round(x2)), "y": int(round(y2)), "origin": "viewport"},
                {"type": "pointerUp", "button": 0},
            ],
        }])

    def press_escape(self):
        self._actions([{
            "type": "key",
            "id": "keyboard",
            "actions": [
                {"type": "keyDown", "value": "\uE00C"},
                {"type": "keyUp", "value": "\uE00C"},
            ],
        }])

    def __exit__(self, exc_type, exc, tb):
        if self.session_id:
            try:
                self._wd("DELETE", f"/session/{self.session_id}")
            except Exception:
                pass
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), 15)
            except Exception:
                pass


def _wait_for_app_ready(driver):
    deadline = time.time() + 30
    while time.time() < deadline:
        st = driver.eval("""(() => {
            const dc = document.getElementById('drawingCanvas');
            return {
                hasChart: typeof chart !== 'undefined' && Boolean(chart),
                hasSeries: typeof candlestickSeries !== 'undefined' && Boolean(candlestickSeries),
                bars: typeof currentRawBars !== 'undefined' && Array.isArray(currentRawBars) ? currentRawBars.length : -1,
                loaded: typeof loadedHistorySymbol !== 'undefined' && loadedHistorySymbol === activeSymbol && !isHistoryLoading,
                dcW: dc ? dc.width : 0
            };
        })()""")
        if isinstance(st, dict) and st.get("hasChart") and st.get("hasSeries") and st.get("loaded") and st.get("bars", 0) > 10 and st.get("dcW", 0) > 100:
            time.sleep(0.35)
            return
        time.sleep(0.4)
    raise AssertionError("Chart application did not finish loading in time")


def _enable_drawing_toolbar_via_ui(driver):
    ind = driver.eval("""(() => {
        const b = document.getElementById('indicatorsBtn');
        const r = b.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    })()""")
    driver.click(ind["x"], ind["y"])
    chk = driver.eval("""(() => {
        const c = document.getElementById('chkShowDrawingToolbar');
        c.scrollIntoView({ block: 'center' });
        const r = c.getBoundingClientRect();
        const cl = document.getElementById('closeIndicatorModal').getBoundingClientRect();
        return { checked: c.checked, x: r.left + r.width / 2, y: r.top + r.height / 2, cx: cl.left + cl.width / 2, cy: cl.top + cl.height / 2 };
    })()""")
    if not chk["checked"]:
        driver.click(chk["x"], chk["y"])
    driver.click(chk["cx"], chk["cy"])
    tb_disp = driver.eval("getComputedStyle(document.getElementById('drawingToolbar')).display")
    assert tb_disp == "flex", f"Expected drawingToolbar visible flex, got {tb_disp}"


def _btn_center(driver, tool: str):
    return driver.eval(f"""(() => {{
        const b = document.querySelector('.draw-tool-btn[data-tool="{tool}"]');
        const r = b.getBoundingClientRect();
        return {{ x: r.left + r.width / 2, y: r.top + r.height / 2 }};
    }})()""")


def _verify_regression_baseline(driver):
    fixture = json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))
    actual = driver.eval(f"""(async () => {{
        const payload = {json.dumps({"bars": fixture["bars"], "drawings": fixture["drawings"]})};
        const legAv = document.getElementById('legAvwap');
        if (legAv) legAv.style.display = 'none';
        indicatorConfig.avwap = false;
        applyIndicatorVisibilities();
        if (typeof resizeAllCharts === 'function') resizeAllCharts();
        for (const s of [volumeSeries, volSmaSeries, ema10Series, ema21Series, ema30Series, vwapSeries, sma50Series, sma200Series, bbUpperSeries, bbLowerSeries]) {{
            if (s && typeof s.setData === 'function') s.setData([]);
        }}
        if (typeof rsSeries !== 'undefined' && rsSeries && typeof rsSeries.setData === 'function') rsSeries.setData([]);
        currentAvwapData = null;
        candlestickSeries.setData(payload.bars);
        currentRawBars = payload.bars;
        latestBarData = payload.bars[payload.bars.length - 1];
        chart.priceScale('right').applyOptions({{ autoScale: true }});
        chart.timeScale().applyOptions({{ rightOffset: RIGHT_WHITESPACE_BARS }});
        await new Promise(r => setTimeout(r, 150));
        chart.timeScale().setVisibleRange({{ from: payload.bars[60].time, to: payload.bars[119].time }});
        for (let tries = 0; tries < 25; tries++) {{
            await new Promise(r => setTimeout(r, 120));
            const vr = chart.timeScale().getVisibleRange();
            if (candlestickSeries.data().length === payload.bars.length &&
                vr && vr.from === payload.bars[60].time &&
                candlestickSeries.priceToCoordinate(payload.drawings[0].price) !== null &&
                chart.timeScale().timeToCoordinate(payload.drawings[1].time1) !== null) {{
                break;
            }}
        }}
        await new Promise(r => setTimeout(r, 250));

        const dc = document.getElementById('drawingCanvas');
        const ctx = dc.getContext('2d');
        const ops = [];
        const origMoveTo = ctx.moveTo.bind(ctx);
        const origLineTo = ctx.lineTo.bind(ctx);
        const origStrokeRect = ctx.strokeRect.bind(ctx);
        const origArc = ctx.arc.bind(ctx);
        ctx.moveTo = function(x, y) {{ ops.push(['moveTo', x, y]); return origMoveTo(x, y); }};
        ctx.lineTo = function(x, y) {{ ops.push(['lineTo', x, y]); return origLineTo(x, y); }};
        ctx.strokeRect = function(x, y, w, h) {{ ops.push(['strokeRect', x, y, w, h]); return origStrokeRect(x, y, w, h); }};
        ctx.arc = function(x, y, r, s, e) {{ ops.push(['arc', x, y, r]); return origArc(x, y, r, s, e); }};
        const results = [];
        try {{
            for (let i = 0; i < payload.drawings.length; i++) {{
                ops.length = 0;
                const clone = Object.assign({{}}, payload.drawings[i]);
                drawings = [clone];
                renderDrawingCanvas();
                results.push({{ index: i, type: clone.type, ops: ops.slice(), rrBox: clone.type === 'risk-reward' ? {{
                    boxLeft: clone.boxLeft, boxRight: clone.boxRight, targetTop: clone.targetTop,
                    stopTop: clone.stopTop, closeBtnX: clone.closeBtnX, closeBtnY: clone.closeBtnY
                }} : null }});
            }}
        }} finally {{
            ctx.moveTo = origMoveTo;
            ctx.lineTo = origLineTo;
            ctx.strokeRect = origStrokeRect;
            ctx.arc = origArc;
        }}
        return {{ canvasWidth: dc.width, canvasHeight: dc.height, hlineY: candlestickSeries.priceToCoordinate(payload.drawings[0].price), dataLen: candlestickSeries.data().length, results: results }};
    }})()""")

    expected_block = (fixture.get("expected_by_engine") or {}).get(driver.name) or fixture["expected"]
    exp_w = expected_block["canvasWidth"]
    exp_h = expected_block["canvasHeight"]
    expected_results = expected_block["results"]
    assert len(actual["results"]) == len(expected_results)
    # Only assert <= 1px absolute coordinate parity when viewport canvas size matches baseline (1200x530)
    assert abs(actual["canvasWidth"] - exp_w) <= 1 and abs(actual["canvasHeight"] - exp_h) <= 1, (
        f"Viewport canvas {actual['canvasWidth']}x{actual['canvasHeight']} != baseline {exp_w}x{exp_h}"
    )
    for act_item, exp_item in zip(actual["results"], expected_results):
        assert len(act_item["ops"]) == len(exp_item["ops"]), (
            f"Op count mismatch on {act_item['type']}: actual={actual}, expected_ops={exp_item['ops']}"
        )
        for aop, eop in zip(act_item["ops"], exp_item["ops"]):
            assert aop[0] == eop[0]
            for av, ev in zip(aop[1:], eop[1:]):
                assert abs(av - ev) <= 1.0, f"Baseline pixel drift > 1px on {act_item['type']} {aop[0]}: {av} vs {ev}"
        if exp_item.get("rrBox"):
            for k in ("boxLeft", "boxRight", "targetTop", "stopTop", "closeBtnX", "closeBtnY"):
                assert abs(act_item["rrBox"][k] - exp_item["rrBox"][k]) <= 1.0, f"R:R {k} drift > 1px"


def _run_full_drawing_e2e(driver):
    _wait_for_app_ready(driver)

    # 0) Existing drawing regression baseline comparison <= 1.0px against 929d5f3 fixture
    _verify_regression_baseline(driver)

    # Restore NVDA history before interactive drawing scenarios
    driver.eval("""(async () => {
        drawings = [];
        saveDrawingsForActiveSymbol();
        await loadHistory(activeSymbol);
        await new Promise(r => setTimeout(r, 300));
    })()""")

    _enable_drawing_toolbar_via_ui(driver)

    # Clear drawings and use deterministic right-visible range
    driver.eval("drawings = []; saveDrawingsForActiveSymbol(); renderDrawingCanvas();")
    dc = driver.eval("(() => { const r = document.getElementById('drawingCanvas').getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; })()")

    # 1) K-bar R:R box + trendline (2-click & drag), hline, ruler (2-click & drag), close X protection & hover cursor cleanup
    rr_btn = _btn_center(driver, "risk-reward")
    driver.click(rr_btn["x"], rr_btn["y"])
    driver.click(dc["left"] + dc["width"] * 0.45, dc["top"] + dc["height"] * 0.48)
    rr_kbar = driver.eval("drawings[0]")
    assert rr_kbar["type"] == "risk-reward"
    assert rr_kbar["boxRight"] <= dc["width"]

    # Hover over close button in cursor mode to set .cursor-pointer
    cur_btn = _btn_center(driver, "cursor")
    driver.click(cur_btn["x"], cur_btn["y"])
    driver.move(dc["left"] + rr_kbar["closeBtnX"], dc["top"] + rr_kbar["closeBtnY"])
    hover_cls = driver.eval("document.getElementById('drawingCanvas').className")
    assert "cursor-pointer" in hover_cls

    # Switch to trendline -> hover classes cleared & computed cursor is crosshair
    tl_btn = _btn_center(driver, "trendline")
    driver.click(tl_btn["x"], tl_btn["y"])
    after_switch = driver.eval("""(() => {
        const dc = document.getElementById('drawingCanvas');
        return {
            tool: currentDrawTool,
            cls: dc.className,
            cursor: getComputedStyle(dc).cursor
        };
    })()""")
    assert after_switch["tool"] == "trendline"
    assert "drawing-active" in after_switch["cls"]
    assert "cursor-pointer" not in after_switch["cls"]
    assert "cursor-move" not in after_switch["cls"]
    assert "cursor-ns-resize" not in after_switch["cls"]
    assert after_switch["cursor"] == "crosshair"

    # Non-cursor click on R:R close button X must NOT delete box (starts trendline step 1), then 1st Esc cancels drawingInProgress keeping trendline, 2nd Esc resets to cursor
    driver.click(dc["left"] + rr_kbar["closeBtnX"], dc["top"] + rr_kbar["closeBtnY"])
    mid_state = driver.eval("({ rrCount: drawings.filter(d => d.type === 'risk-reward').length, hasInProgress: Boolean(drawingInProgress), tool: currentDrawTool })")
    assert mid_state["rrCount"] == 1
    assert mid_state["hasInProgress"] is True

    driver.press_escape()
    after_esc1 = driver.eval("({ hasInProgress: Boolean(drawingInProgress), tool: currentDrawTool, hasActive: document.getElementById('drawingCanvas').classList.contains('drawing-active') })")
    assert after_esc1["hasInProgress"] is False
    assert after_esc1["tool"] == "trendline"
    assert after_esc1["hasActive"] is True

    driver.press_escape()
    after_esc2 = driver.eval("({ tool: currentDrawTool, hasActive: document.getElementById('drawingCanvas').classList.contains('drawing-active') })")
    assert after_esc2["tool"] == "cursor"
    assert after_esc2["hasActive"] is False

    # Draw trendline (two-click + drag), hline, ruler (two-click + drag) INSIDE K-bar R:R box
    driver.click(tl_btn["x"], tl_btn["y"])
    driver.click(dc["left"] + rr_kbar["boxLeft"] + 25, dc["top"] + rr_kbar["targetTop"] + 20)
    driver.click(dc["left"] + rr_kbar["boxRight"] - 45, dc["top"] + rr_kbar["stopTop"] + 15)
    driver.drag(dc["left"] + rr_kbar["boxLeft"] + 30, dc["top"] + rr_kbar["stopTop"] + 10, dc["left"] + rr_kbar["boxRight"] - 35, dc["top"] + rr_kbar["targetTop"] + 25)

    hl_btn = _btn_center(driver, "hline")
    driver.click(hl_btn["x"], hl_btn["y"])
    driver.click(dc["left"] + rr_kbar["boxLeft"] + 60, dc["top"] + rr_kbar["targetTop"] + 30)

    ru_btn = _btn_center(driver, "ruler")
    driver.click(ru_btn["x"], ru_btn["y"])
    driver.click(dc["left"] + rr_kbar["boxLeft"] + 35, dc["top"] + rr_kbar["targetTop"] + 15)
    driver.click(dc["left"] + rr_kbar["boxRight"] - 50, dc["top"] + rr_kbar["stopTop"] + 15)
    driver.drag(dc["left"] + rr_kbar["boxLeft"] + 45, dc["top"] + rr_kbar["targetTop"] + 25, dc["left"] + rr_kbar["boxRight"] - 25, dc["top"] + rr_kbar["stopTop"] + 10)

    types_kbar = driver.eval("drawings.map(d => d.type)")
    assert types_kbar == ["risk-reward", "trendline", "trendline", "hline", "ruler", "ruler"], f"Unexpected K-bar drawings: {types_kbar}"

    # Verify cursor drag of R:R body and cursor close X still work
    driver.click(cur_btn["x"], cur_btn["y"])
    entry_before = driver.eval("drawings[0].entryPrice")
    driver.drag(dc["left"] + rr_kbar["boxLeft"] + 80, dc["top"] + rr_kbar["targetTop"] + 20, dc["left"] + rr_kbar["boxLeft"] + 80, dc["top"] + rr_kbar["targetTop"] - 10)
    entry_after = driver.eval("drawings[0].entryPrice")
    assert entry_after > entry_before

    rr_updated = driver.eval("drawings[0]")
    driver.click(dc["left"] + rr_updated["closeBtnX"], dc["top"] + rr_updated["closeBtnY"])
    assert driver.eval("drawings.filter(d => d.type === 'risk-reward').length") == 0

    # 2) Right whitespace R:R box + trendline (2-click & drag), hline, ruler (2-click & drag) + stability on new bar / TF switch / symbol switch
    driver.eval("drawings = []; saveDrawingsForActiveSymbol(); renderDrawingCanvas();")
    last_bar_pt = driver.eval("""(() => {
        const ts = chart.timeScale();
        const lb = currentRawBars[currentRawBars.length - 1];
        return { x: ts.timeToCoordinate(lb.time), y: candlestickSeries.priceToCoordinate(lb.close), time: lb.time };
    })()""")
    driver.click(rr_btn["x"], rr_btn["y"])
    driver.click(dc["left"] + last_bar_pt["x"], dc["top"] + last_bar_pt["y"])
    rr_ws = driver.eval("drawings[0]")
    assert rr_ws["boxRight"] <= dc["width"], f"R:R box overflowed canvas right: {rr_ws['boxRight']} > {dc['width']}"

    ws_x1 = dc["left"] + max(last_bar_pt["x"] + 20, rr_ws["boxLeft"] + 25)
    ws_x2 = dc["left"] + min(rr_ws["boxRight"] - 15, last_bar_pt["x"] + 90)
    assert ws_x2 > ws_x1 + 10

    driver.click(tl_btn["x"], tl_btn["y"])
    driver.click(ws_x1, dc["top"] + rr_ws["targetTop"] + 20)
    driver.click(ws_x2, dc["top"] + rr_ws["stopTop"] + 15)
    driver.drag(ws_x1 + 5, dc["top"] + rr_ws["stopTop"] + 10, ws_x2 - 5, dc["top"] + rr_ws["targetTop"] + 25)

    driver.click(hl_btn["x"], hl_btn["y"])
    driver.click((ws_x1 + ws_x2) / 2, dc["top"] + rr_ws["targetTop"] + 30)

    driver.click(ru_btn["x"], ru_btn["y"])
    driver.click(ws_x1, dc["top"] + rr_ws["targetTop"] + 18)
    driver.click(ws_x2, dc["top"] + rr_ws["stopTop"] + 12)
    driver.drag(ws_x1 + 4, dc["top"] + rr_ws["targetTop"] + 22, ws_x2 - 4, dc["top"] + rr_ws["stopTop"] + 10)

    ws_check = driver.eval("""(() => {
        const tl = drawings.find(d => d.type === 'trendline');
        const ru = drawings.find(d => d.type === 'ruler');
        const ts = chart.timeScale();
        const tfSec = TF_SECONDS[activeTimeframe] || 86400;
        const axis = getMainTimeAxis();
        return {
            types: drawings.map(d => d.type),
            lastBarTime: currentRawBars[currentRawBars.length - 1].time,
            tl: tl,
            ru: ru,
            tlX1: safeTimeToCoordinate(ts, tl.time1, axis, tfSec),
            tlX2: safeTimeToCoordinate(ts, tl.time2, axis, tfSec),
            ruX1: safeTimeToCoordinate(ts, ru.time1, axis, tfSec),
            ruX2: safeTimeToCoordinate(ts, ru.time2, axis, tfSec)
        };
    })()""")
    assert ws_check["types"] == ["risk-reward", "trendline", "trendline", "hline", "ruler", "ruler"]
    assert ws_check["tl"]["time1"] > ws_check["lastBarTime"]
    assert ws_check["tl"]["time2"] > ws_check["tl"]["time1"]
    assert abs(ws_check["tlX2"] - ws_check["tlX1"]) > 10
    assert abs(ws_check["ruX2"] - ws_check["ruX1"]) > 10

    # (a) Appending a new bar keeps whitespace drawing locked to its candle-relative offset <= 1px (no jump)
    new_bar_delta = driver.eval("""(() => {
        const ts = chart.timeScale();
        const tfSec = TF_SECONDS[activeTimeframe] || 86400;
        const tl = drawings.find(d => d.type === 'trendline');
        const lb = currentRawBars[currentRawBars.length - 1];
        const prevLbX = safeTimeToCoordinate(ts, lb.time, getMainTimeAxis(), tfSec);
        const prevX1 = safeTimeToCoordinate(ts, tl.time1, getMainTimeAxis(), tfSec);
        const nextBar = Object.assign({}, lb, { time: lb.time + tfSec });
        currentRawBars = currentRawBars.concat([nextBar]);
        candlestickSeries.update(nextBar);
        const afterLbX = safeTimeToCoordinate(ts, lb.time, getMainTimeAxis(), tfSec);
        const afterX1 = safeTimeToCoordinate(ts, tl.time1, getMainTimeAxis(), tfSec);
        const relBefore = prevX1 - prevLbX;
        const relAfter = afterX1 - afterLbX;
        return { prevLbX, afterLbX, prevX1, afterX1, relBefore, relAfter, diff: Math.abs(relAfter - relBefore) };
    })()""")
    assert new_bar_delta["diff"] <= 1.0, f"Whitespace drawing jumped relative to bars on new bar: {new_bar_delta}"

    # (b) Switching 1D -> 1W -> 1h -> 1D keeps whitespace drawing coordinates finite & returns cleanly
    tf_check = driver.eval("""(async () => {
        const results = {};
        const tl = drawings.find(d => d.type === 'trendline');
        for (const tf of ['1D', '1W', '1h', '1D']) {
            const btn = document.querySelector(`.tf-btn[data-tf="${tf}"]`);
            btn.click();
            await new Promise(r => setTimeout(r, 450));
            const x1 = safeTimeToCoordinate(chart.timeScale(), tl.time1, getMainTimeAxis(), TF_SECONDS[activeTimeframe] || 86400);
            const x2 = safeTimeToCoordinate(chart.timeScale(), tl.time2, getMainTimeAxis(), TF_SECONDS[activeTimeframe] || 86400);
            results[tf] = { activeTimeframe, x1, x2 };
        }
        return results;
    })()""")
    for tf in ("1D", "1W", "1h"):
        assert tf_check[tf]["x1"] is not None and tf_check[tf]["x2"] is not None, f"Missing coords on {tf}: {tf_check}"

    # (c) Switching symbol away and back preserves drawings
    saved_cnt_before = driver.eval("drawings.length")
    sym_roundtrip = driver.eval("""(async () => {
        const orig = activeSymbol;
        const other = orig === 'NASDAQ:NVDA' ? 'AMEX:SPY' : 'NASDAQ:NVDA';
        switchSymbol(other);
        await new Promise(r => setTimeout(r, 350));
        switchSymbol(orig);
        await new Promise(r => setTimeout(r, 350));
        return { restoredSymbol: activeSymbol, count: drawings.length };
    })()""")
    assert sym_roundtrip["count"] == saved_cnt_before


@pytest.mark.e2e
class TestDrawingRealBrowserE2E(unittest.TestCase):
    def test_drawing_workflows_chrome_cdp(self):
        with _BackendServer() as be:
            with ChromeBrowserDriver(f"http://127.0.0.1:{be.port}/") as chrome:
                _run_full_drawing_e2e(chrome)

    def test_drawing_workflows_webkit_safari(self):
        with _BackendServer() as be:
            with SafariWebKitDriver(f"http://127.0.0.1:{be.port}/") as safari:
                _run_full_drawing_e2e(safari)
