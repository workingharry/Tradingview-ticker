import { createMultiPaneChart, type MultiPaneChartBundle } from './chart/createMultiPaneChart';
import { RiskRewardPrimitive } from './chart/primitives/RiskRewardPrimitive';
import { apiFetch, LiveFeed, type LiveMessage, type FeedState } from './api/client';
import { LRUPrefetchCache, normalizeTime, type ParsedHistoryData } from './cache/LRUPrefetchCache';
import { CommandPalette } from './ui/CommandPalette';
import type { Time } from 'lightweight-charts';

export const DEFAULT_WATCHLIST = ['NVDA', 'TSLA', 'AAPL', 'MSFT', 'AMD', 'META', 'GOOGL', 'AMZN', 'SPY', 'QQQ', 'IWM', 'SMH'];

let chartBundle: MultiPaneChartBundle | null = null;
let currentSymbol = 'NVDA';
let currentTimeframe = '1D';
let currentBars: any[] = [];
let liveFeed: LiveFeed | null = null;
let commandPalette: CommandPalette | null = null;
let activeWatchlist: string[] = [...DEFAULT_WATCHLIST];

// Concurrency guards for rapid symbol switching
let currentLoadGen = 0;
let activeAbortController: AbortController | null = null;

// High performance LRU cache for instant (<50ms) symbol flipping
const prefetchCache = new LRUPrefetchCache({
  capacity: 60,
  ttlMs: 300_000, // 5 minutes TTL
  maxPrefetchConcurrency: 2
});

async function initTerminal() {
  const container = document.getElementById('chartContainer');
  if (!container) return;

  chartBundle = createMultiPaneChart(container);

  setupLiveFeed();
  setupCommandPalette();
  setupKeyboardNavigation();

  await switchSymbol(currentSymbol, currentTimeframe);

  // Attach button events
  const btnRR = document.getElementById('btnAttachRR');
  btnRR?.addEventListener('click', attachDemoRiskReward);

  const btnPalette = document.getElementById('btnCommandPalette');
  btnPalette?.addEventListener('click', () => {
    commandPalette?.open();
  });

  const symbolInput = document.getElementById('symbolInput') as HTMLInputElement | null;
  symbolInput?.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
      const val = symbolInput.value.trim().toUpperCase();
      if (val && val !== currentSymbol) {
        await switchSymbol(val, currentTimeframe);
      }
    }
  });

  const tfButtons = document.querySelectorAll('.tf-btn');
  tfButtons.forEach((btn) => {
    btn.addEventListener('click', async () => {
      const tf = btn.getAttribute('data-tf') || '1D';
      await switchTimeframe(tf);
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

function setupCommandPalette() {
  commandPalette = new CommandPalette({
    symbols: activeWatchlist,
    timeframes: ['15m', '1h', '1D', '1W'],
    onSelectSymbol: (symbol) => {
      switchSymbol(symbol, currentTimeframe);
    },
    onSelectTimeframe: (tf) => {
      switchTimeframe(tf);
    },
    onAction: (actionId) => {
      if (actionId === 'attach_rr') {
        attachDemoRiskReward();
      } else if (actionId === 'fit_content') {
        chartBundle?.chart.timeScale().fitContent();
      } else if (actionId === 'flip_next') {
        flipWatchlistSymbol(1);
      } else if (actionId === 'flip_prev') {
        flipWatchlistSymbol(-1);
      }
    }
  });
}

function setupKeyboardNavigation() {
  window.addEventListener('keydown', (e) => {
    // Skip if Command Palette is open
    if (commandPalette && commandPalette.isOpen()) {
      return;
    }

    // Skip if user is actively typing in an input or textarea
    const activeEl = document.activeElement;
    if (
      activeEl instanceof HTMLInputElement ||
      activeEl instanceof HTMLTextAreaElement ||
      (activeEl as HTMLElement)?.isContentEditable
    ) {
      return;
    }

    // Keyboard fast symbol flipper
    if (e.code === 'Space') {
      e.preventDefault();
      flipWatchlistSymbol(e.shiftKey ? -1 : 1);
    } else if (e.code === 'ArrowDown') {
      e.preventDefault();
      flipWatchlistSymbol(1);
    } else if (e.code === 'ArrowUp') {
      e.preventDefault();
      flipWatchlistSymbol(-1);
    }
  });
}

export function flipWatchlistSymbol(direction: 1 | -1): void {
  if (activeWatchlist.length === 0) return;

  const currentIdx = activeWatchlist.findIndex(
    (s) => s.toUpperCase() === currentSymbol.toUpperCase()
  );

  let nextIdx: number;
  if (currentIdx === -1) {
    nextIdx = 0;
  } else {
    nextIdx = (currentIdx + direction + activeWatchlist.length) % activeWatchlist.length;
  }

  const targetSymbol = activeWatchlist[nextIdx];
  switchSymbol(targetSymbol, currentTimeframe);
}

export async function switchTimeframe(tf: string): Promise<void> {
  if (tf === currentTimeframe) return;

  currentTimeframe = tf;

  const tfButtons = document.querySelectorAll('.tf-btn');
  tfButtons.forEach((btn) => {
    if (btn.getAttribute('data-tf') === tf) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  await loadHistory(currentSymbol, currentTimeframe);
}

export async function switchSymbol(symbol: string, timeframe: string = currentTimeframe): Promise<void> {
  const cleanSymbol = symbol.trim().toUpperCase();
  if (!cleanSymbol) return;

  currentSymbol = cleanSymbol;

  // Sync symbol input display
  const symbolInput = document.getElementById('symbolInput') as HTMLInputElement | null;
  if (symbolInput && symbolInput.value.toUpperCase() !== cleanSymbol) {
    symbolInput.value = cleanSymbol;
  }

  // Add to active watchlist if not already present
  if (!activeWatchlist.includes(cleanSymbol)) {
    activeWatchlist.push(cleanSymbol);
    commandPalette?.updateSymbols(activeWatchlist);
  }

  // 1. Check synchronous LRU cache for <50ms zero-latency rendering
  const cached = prefetchCache.get(cleanSymbol, timeframe);
  if (cached) {
    applyChartData(cached);
    // Trigger speculative prefetch for adjacent watchlist neighbors
    speculativePrefetchNeighbors(cleanSymbol, timeframe);
    return;
  }

  // 2. Cache miss: Fetch via loadHistory with generation & abort guards
  await loadHistory(cleanSymbol, timeframe);

  // 3. Speculatively prefetch adjacent symbols
  speculativePrefetchNeighbors(cleanSymbol, timeframe);
}

function speculativePrefetchNeighbors(symbol: string, timeframe: string): void {
  const idx = activeWatchlist.findIndex((s) => s.toUpperCase() === symbol.toUpperCase());
  if (idx === -1 || activeWatchlist.length <= 1) return;

  const len = activeWatchlist.length;
  const next1 = activeWatchlist[(idx + 1) % len];
  const next2 = activeWatchlist[(idx + 2) % len];
  const prev1 = activeWatchlist[(idx - 1 + len) % len];

  prefetchCache.prefetch([next1, next2, prev1], timeframe, currentLoadGen, fetchHistoryBars);
}

async function fetchHistoryBars(symbol: string, timeframe: string, signal?: AbortSignal): Promise<any[]> {
  const data = await apiFetch<any>(
    `/api/history?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=300`,
    { signal }
  );
  return Array.isArray(data) ? data : data?.bars || [];
}

function applyChartData(parsed: ParsedHistoryData): void {
  if (!chartBundle) return;

  currentBars = parsed.bars;
  chartBundle.candleSeries.setData(parsed.candleData);
  chartBundle.volumeSeries.setData(parsed.volumeData);
  chartBundle.rsSeries.setData(parsed.rsData);
  chartBundle.chart.timeScale().fitContent();
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
    const parsed = await prefetchCache.load(
      symbol,
      timeframe,
      fetchHistoryBars,
      controller.signal
    );

    // 2. Generation guard: If a newer symbol was requested while this fetch was in flight, discard response
    if (loadGen !== currentLoadGen) {
      return;
    }

    if (parsed.candleData.length > 0) {
      applyChartData(parsed);
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

window.addEventListener('DOMContentLoaded', initTerminal);
