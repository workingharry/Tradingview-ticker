import { createMultiPaneChart, type MultiPaneChartBundle } from './chart/createMultiPaneChart';
import { RiskRewardPrimitive } from './chart/primitives/RiskRewardPrimitive';
import { apiFetch, LiveFeed, type LiveMessage, type FeedState } from './api/client';
import type { Time } from 'lightweight-charts';

let chartBundle: MultiPaneChartBundle | null = null;
let currentSymbol = 'NVDA';
let currentTimeframe = '1D';
let currentBars: any[] = [];
let liveFeed: LiveFeed | null = null;

// Concurrency guards for rapid symbol switching
let currentLoadGen = 0;
let activeAbortController: AbortController | null = null;

async function initTerminal() {
  const container = document.getElementById('chartContainer');
  if (!container) return;

  chartBundle = createMultiPaneChart(container);

  setupLiveFeed();
  await loadHistory(currentSymbol, currentTimeframe);

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

function updateStatusDot(state: FeedState) {
  const dot = document.getElementById('statusDot');
  if (!dot) return;
  if (state === 'open') {
    dot.className = 'status-dot connected';
  } else if (state === 'connecting' || state === 'reconnecting') {
    dot.className = 'status-dot connecting';
  } else {
    dot.className = 'status-dot';
  }
}

function setupLiveFeed() {
  if (liveFeed) {
    liveFeed.stop();
  }

  liveFeed = new LiveFeed({
    path: '/ws/live',
    onStateChange: (state) => {
      updateStatusDot(state);
    },
    onGap: (gap) => {
      console.warn('[LiveFeed] Bar gap detected:', gap);
      // Re-fetch latest history to heal gap if it affects current symbol
      if (isSymbolMatch(gap.symbol, currentSymbol)) {
        loadHistory(currentSymbol, currentTimeframe);
      }
    }
  });

  liveFeed.subscribe((msg: LiveMessage) => {
    if (msg.type === 'bar' && msg.bar && chartBundle && isSymbolMatch(msg.symbol, currentSymbol)) {
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
  });

  liveFeed.start();
}

function isSymbolMatch(msgSymbol?: string, targetSymbol?: string): boolean {
  if (!msgSymbol || !targetSymbol) return false;
  const s1 = msgSymbol.toUpperCase();
  const s2 = targetSymbol.toUpperCase();
  if (s1 === s2) return true;
  if (s1.endsWith(':' + s2) || s2.endsWith(':' + s1)) return true;
  return false;
}

async function loadHistory(symbol: string, timeframe: string) {
  if (!chartBundle) return;

  // 1. Abort any previous in-flight request for a superseded symbol
  if (activeAbortController) {
    activeAbortController.abort();
  }
  const controller = new AbortController();
  activeAbortController = controller;
  const loadGen = ++currentLoadGen;

  try {
    const data = await apiFetch<any>(
      `/api/history?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=300`,
      { signal: controller.signal }
    );

    // 2. Generation guard: If a newer symbol was requested while this fetch was in flight, discard response
    if (loadGen !== currentLoadGen) {
      return;
    }

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

      // Demonstrative RS line
      const firstClose = candleData[0].close || 1;
      const rsData = candleData.map((b: any) => ({
        time: b.time,
        value: Number(((b.close / firstClose) * 100).toFixed(2))
      }));
      chartBundle.rsSeries.setData(rsData);

      chartBundle.chart.timeScale().fitContent();
    }
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      // Intentionally aborted due to rapid symbol switch
      return;
    }
    console.error('[loadHistory] Failed to load history for', symbol, err);
  } finally {
    if (activeAbortController === controller) {
      activeAbortController = null;
    }
  }
}

function attachDemoRiskReward() {
  if (!chartBundle) return;
  const candleSeries = chartBundle.candleSeries;

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

function normalizeTime(t: any): Time {
  if (typeof t === 'number') {
    return (t > 1e11 ? Math.floor(t / 1000) : t) as Time;
  }
  return t as Time;
}

window.addEventListener('DOMContentLoaded', initTerminal);
