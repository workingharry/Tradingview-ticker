import { createMultiPaneChart, type MultiPaneChartBundle } from './chart/createMultiPaneChart';
import { RiskRewardPrimitive } from './chart/primitives/RiskRewardPrimitive';
import { apiFetch, openLiveWebSocket } from './api/client';
import type { Time } from 'lightweight-charts';

let chartBundle: MultiPaneChartBundle | null = null;
let currentSymbol = 'NVDA';
let currentTimeframe = '1D';
let currentBars: any[] = [];
let liveSocket: WebSocket | null = null;

async function initTerminal() {
  const container = document.getElementById('chartContainer');
  if (!container) return;

  chartBundle = createMultiPaneChart(container);

  await loadHistory(currentSymbol, currentTimeframe);
  connectWebSocket();

  // Attach button events
  const btnRR = document.getElementById('btnAttachRR');
  btnRR?.addEventListener('click', attachDemoRiskReward);

  const symbolInput = document.getElementById('symbolInput') as HTMLInputElement | null;
  symbolInput?.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
      const val = symbolInput.value.trim().toUpperCase();
      if (val && val !== currentSymbol) {
        currentSymbol = val;
        await loadHistory(currentSymbol, currentTimeframe);
      }
    }
  });

  const tfButtons = document.querySelectorAll('.tf-btn');
  tfButtons.forEach((btn) => {
    btn.addEventListener('click', async () => {
      tfButtons.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const tf = btn.getAttribute('data-tf') || '1D';
      currentTimeframe = tf;
      await loadHistory(currentSymbol, currentTimeframe);
    });
  });

  // Hotkey: Shift+R to attach Risk/Reward Box
  window.addEventListener('keydown', (e) => {
    if (e.shiftKey && (e.key === 'R' || e.key === 'r')) {
      e.preventDefault();
      attachDemoRiskReward();
    }
  });
}

async function loadHistory(symbol: string, timeframe: string) {
  if (!chartBundle) return;
  try {
    const data = await apiFetch<any>(`/api/history?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=300`);
    const bars: any[] = Array.isArray(data) ? data : data?.bars || [];

    if (bars.length > 0) {
      currentBars = bars;
      const candleData = bars.map((b: any) => ({
        time: normalizeTime(b.time),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close
      }));

      const volumeData = bars.map((b: any) => ({
        time: normalizeTime(b.time),
        value: b.volume || 0,
        color: b.close >= b.open ? 'rgba(34, 197, 94, 0.45)' : 'rgba(239, 68, 68, 0.45)'
      }));

      chartBundle.candleSeries.setData(candleData);
      chartBundle.volumeSeries.setData(volumeData);

      // Simple mock/seed RS line for demonstrative multi-pane view
      const firstClose = candleData[0].close || 1;
      const rsData = candleData.map((b: any) => ({
        time: b.time,
        value: Number(((b.close / firstClose) * 100).toFixed(2))
      }));
      chartBundle.rsSeries.setData(rsData);

      chartBundle.chart.timeScale().fitContent();
    }
  } catch (err) {
    console.error('[loadHistory] Failed to load history for', symbol, err);
  }
}

function attachDemoRiskReward() {
  if (!chartBundle) return;
  const candleSeries = chartBundle.candleSeries;

  // Default price levels around current last price
  const lastBar = currentBars.length > 0 ? currentBars[currentBars.length - 1] : { time: Math.floor(Date.now() / 1000) as Time, close: 100 };
  const lastPrice = lastBar.close || 100;
  const entryPrice = Number(lastPrice.toFixed(2));
  const stopPrice = Number((entryPrice * 0.95).toFixed(2)); // 5% stop
  const targetPrice = Number((entryPrice * 1.15).toFixed(2)); // 15% target (3:1 R:R)
  const startTime = normalizeTime(lastBar.time);
  const endTime = (typeof startTime === 'number' ? (startTime + 86400 * 20) : startTime) as Time;

  const primitive = new RiskRewardPrimitive({
    id: `rr_${Date.now()}`,
    startTime,
    endTime,
    entryPrice,
    stopPrice,
    targetPrice,
    accountSize: 100000,
    riskPercent: 1.0,
    maxAllocationPercent: 25.0,
    adr20Percent: 4.5
  }, {
    onChange: (state, metrics) => {
      console.log('[RiskReward] Updated:', state.id, metrics);
    },
    onRemove: (id) => {
      console.log('[RiskReward] Removed:', id);
    }
  });

  candleSeries.attachPrimitive(primitive);
}

async function connectWebSocket() {
  try {
    liveSocket = await openLiveWebSocket('/ws/live');
    liveSocket.onopen = () => {
      const dot = document.getElementById('statusDot');
      if (dot) dot.className = 'status-dot connected';
    };
    liveSocket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const isMatch = msg.symbol === currentSymbol ||
                        (typeof msg.symbol === 'string' && msg.symbol.endsWith(':' + currentSymbol)) ||
                        (typeof currentSymbol === 'string' && currentSymbol.endsWith(':' + msg.symbol));
        if (msg.type === 'bar' && isMatch && chartBundle) {
          const b = msg.bar;
          const barTime = normalizeTime(b.time);
          if (currentBars.length > 0 && normalizeTime(currentBars[currentBars.length - 1].time) === barTime) {
            currentBars[currentBars.length - 1] = b;
          } else {
            currentBars.push(b);
          }
          chartBundle.candleSeries.update({
            time: barTime,
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close
          });
          chartBundle.volumeSeries.update({
            time: barTime,
            value: b.volume || 0,
            color: b.close >= b.open ? 'rgba(34, 197, 94, 0.45)' : 'rgba(239, 68, 68, 0.45)'
          });
        }
      } catch (e) {
        console.warn('[WS] Parse error', e);
      }
    };
    liveSocket.onclose = () => {
      const dot = document.getElementById('statusDot');
      if (dot) dot.className = 'status-dot';
      setTimeout(connectWebSocket, 3000);
    };
  } catch (err) {
    console.warn('[WS] Connection failed', err);
  }
}

function normalizeTime(t: any): Time {
  if (typeof t === 'number') {
    return (t > 1e11 ? Math.floor(t / 1000) : t) as Time;
  }
  return t as Time;
}

window.addEventListener('DOMContentLoaded', initTerminal);
