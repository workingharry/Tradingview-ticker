import { createMultiPaneChart, type MultiPaneChartBundle } from './chart/createMultiPaneChart';
import { apiFetch, LiveFeed, type LiveMessage, type FeedState } from './api/client';
import { LRUPrefetchCache, normalizeTime, type ParsedHistoryData } from './cache/LRUPrefetchCache';
import { CommandPalette } from './ui/CommandPalette';
import { DrawingSyncEngine } from './chart/DrawingSyncEngine';
import type { Time } from 'lightweight-charts';

export const DEFAULT_WATCHLIST = ['NVDA', 'TSLA', 'AAPL', 'MSFT', 'AMD', 'META', 'GOOGL', 'AMZN', 'SPY', 'QQQ', 'IWM', 'SMH'];

let chartBundle: MultiPaneChartBundle | null = null;
let currentSymbol = 'NVDA';
let currentTimeframe = '1D';
let currentBars: any[] = [];
let currentBenchmarkClose: number | null = null;
const latestBenchmarkCloseBySymbol = new Map<string, number>();
let liveFeed: LiveFeed | null = null;
let commandPalette: CommandPalette | null = null;
let drawingSyncEngine: DrawingSyncEngine | null = null;
let activeWatchlist: string[] = [...DEFAULT_WATCHLIST];

const TF_SECONDS: Record<string, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '1D': 86400,
  '1W': 604800
};

export function alignBarTimestamp(barTime: number, timeframe: string): number {
  if (timeframe === '1m') return barTime;
  if (timeframe === '1W') {
    // Unix Epoch (1970-01-01) was Thursday. Monday 00:00:00 UTC offset is 3 days (259200 seconds).
    return Math.floor((barTime + 259200) / 604800) * 604800 - 259200;
  }
  const tfSec = TF_SECONDS[timeframe] || 60;
  return barTime - (barTime % tfSec);
}

export function getBenchmarkForSymbol(symbol: string): string {
  const s = symbol.trim().toUpperCase();
  if (s.startsWith('BINANCE:') || s.includes('USDT') || s.includes('BUSD')) {
    return 'BINANCE:BTCUSDT';
  }
  return 'AMEX:SPY';
}

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

  drawingSyncEngine = new DrawingSyncEngine({
    getSeries: () => chartBundle?.candleSeries || null
  });

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

  // Shift+R hotkey is handled inside setupKeyboardNavigation() so it never hijacks typing in inputs.
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
      } else if (actionId === 'clear_drawings') {
        drawingSyncEngine?.clearAllDrawings();
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

    // Keyboard fast symbol flipper & Risk/Reward shortcut
    if (e.code === 'Space') {
      e.preventDefault();
      flipWatchlistSymbol(e.shiftKey ? -1 : 1);
    } else if (e.code === 'ArrowDown') {
      e.preventDefault();
      flipWatchlistSymbol(1);
    } else if (e.code === 'ArrowUp') {
      e.preventDefault();
      flipWatchlistSymbol(-1);
    } else if (e.shiftKey && (e.key === 'R' || e.key === 'r')) {
      e.preventDefault();
      attachDemoRiskReward();
    }
  });
}

export function flipWatchlistSymbol(direction: 1 | -1): void {
  if (activeWatchlist.length === 0) return;

  const currentIdx = activeWatchlist.findIndex(
    (s) => isSymbolMatch(s, currentSymbol)
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

  // Add to active watchlist if not already present (using isSymbolMatch for equivalent ticker deduplication)
  const existingIdx = activeWatchlist.findIndex((s) => isSymbolMatch(s, cleanSymbol));
  if (existingIdx === -1) {
    activeWatchlist.push(cleanSymbol);
    commandPalette?.updateSymbols(activeWatchlist);
  }

  // 1. Check synchronous LRU cache for <50ms zero-latency rendering
  const cached = prefetchCache.get(cleanSymbol, timeframe);
  if (cached) {
    applyChartData(cached);
    // Trigger speculative prefetch for adjacent watchlist neighbors
    speculativePrefetchNeighbors(cleanSymbol, timeframe);
    await drawingSyncEngine?.loadDrawings(cleanSymbol);
    return;
  }

  // 2. Cache miss: Fetch via loadHistory with generation & abort guards
  await loadHistory(cleanSymbol, timeframe);
  await drawingSyncEngine?.loadDrawings(cleanSymbol);

  // 3. Speculatively prefetch adjacent symbols
  speculativePrefetchNeighbors(cleanSymbol, timeframe);
}

function speculativePrefetchNeighbors(symbol: string, timeframe: string): void {
  const idx = activeWatchlist.findIndex((s) => isSymbolMatch(s, symbol));
  if (idx === -1 || activeWatchlist.length <= 1) return;

  const len = activeWatchlist.length;
  const next1 = activeWatchlist[(idx + 1) % len];
  const next2 = activeWatchlist[(idx + 2) % len];
  const prev1 = activeWatchlist[(idx - 1 + len) % len];

  prefetchCache.prefetch(
    [next1, next2, prev1],
    timeframe,
    currentLoadGen,
    fetchHistoryBars,
    getBenchmarkForSymbol(symbol)
  );
}

async function fetchHistoryBars(symbol: string, timeframe: string, signal?: AbortSignal): Promise<any[]> {
  const data = await apiFetch<any>(
    `/api/history?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=300&seed=true`,
    { signal }
  );
  return Array.isArray(data) ? data : data?.bars || [];
}

function applyChartData(parsed: ParsedHistoryData): void {
  if (!chartBundle) return;

  currentBars = parsed.bars;
  const benchSym = getBenchmarkForSymbol(currentSymbol);
  if (typeof parsed.benchmarkClose === 'number' && Number.isFinite(parsed.benchmarkClose) && parsed.benchmarkClose > 0) {
    currentBenchmarkClose = parsed.benchmarkClose;
    latestBenchmarkCloseBySymbol.set(benchSym, parsed.benchmarkClose);
  } else {
    currentBenchmarkClose = latestBenchmarkCloseBySymbol.get(benchSym) ?? null;
  }

  chartBundle.candleSeries.setData(parsed.candleData);
  chartBundle.volumeSeries.setData(parsed.volumeData);
  chartBundle.rsSeries.setData(parsed.rsData);
  if (parsed.candleData.length > 0) {
    chartBundle.chart.timeScale().fitContent();
  }
}

export async function loadHistory(symbol: string, timeframe: string, forceRefresh: boolean = false) {
  if (!chartBundle) return;

  if (forceRefresh) {
    prefetchCache.invalidate((s, tf) => isSymbolMatch(s, symbol) && tf === timeframe.trim().toUpperCase());
  }

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
      controller.signal,
      getBenchmarkForSymbol(symbol)
    );

    // 2. Generation guard: If a newer symbol was requested while this fetch was in flight, discard response
    if (loadGen !== currentLoadGen) {
      return;
    }

    applyChartData(parsed);
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
      // Invalidate stale cache for current symbol and re-fetch latest history from server
      if (isSymbolMatch(gap.symbol, currentSymbol)) {
        prefetchCache.invalidate((s, tf) => isSymbolMatch(s, currentSymbol) && tf === currentTimeframe.trim().toUpperCase());
        loadHistory(currentSymbol, currentTimeframe, true);
      }
    }
  });

  liveFeed.subscribe((msg: LiveMessage) => {
    // Track latest benchmark close from live bar/quote stream for real-time RS calculation
    if (msg.symbol) {
      const livePrice = msg.type === 'bar' ? Number(msg.bar?.close) : Number(msg.data?.price);
      if (Number.isFinite(livePrice) && livePrice > 0) {
        if (isSymbolMatch(msg.symbol, 'AMEX:SPY')) {
          latestBenchmarkCloseBySymbol.set('AMEX:SPY', livePrice);
          if (getBenchmarkForSymbol(currentSymbol) === 'AMEX:SPY') {
            currentBenchmarkClose = livePrice;
          }
        } else if (isSymbolMatch(msg.symbol, 'BINANCE:BTCUSDT')) {
          latestBenchmarkCloseBySymbol.set('BINANCE:BTCUSDT', livePrice);
          if (getBenchmarkForSymbol(currentSymbol) === 'BINANCE:BTCUSDT') {
            currentBenchmarkClose = livePrice;
          }
        }
      }
    }

    if (msg.type === 'bar' && msg.bar && chartBundle && isSymbolMatch(msg.symbol, currentSymbol)) {
      const b = msg.bar;
      const rawClose = Number(b.close);
      if (!Number.isFinite(rawClose) || rawClose <= 0) return;

      let barTime = normalizeTime(b.time);
      let open = Number(b.open);
      let high = Number(b.high);
      let low = Number(b.low);
      const close = rawClose;
      let volume = Number(b.volume || 0);

      const lastBar = currentBars.length > 0 ? currentBars[currentBars.length - 1] : null;
      const lastTime = lastBar ? normalizeTime(lastBar.time) : null;
      const tfSec = TF_SECONDS[currentTimeframe] || 60;

      // Align 1m WebSocket bars to active timeframe & enforce LWC monotonic timestamp invariant
      if (typeof barTime === 'number' && typeof lastTime === 'number') {
        if (currentTimeframe !== '1m' && tfSec > 60) {
          const bucketTime = alignBarTimestamp(barTime, currentTimeframe);
          const lastBucket = alignBarTimestamp(lastTime, currentTimeframe);
          let targetTime = bucketTime;
          if (bucketTime === lastBucket || Math.abs(barTime - lastTime) < tfSec || bucketTime < lastTime) {
            targetTime = lastTime;
          }
          if (targetTime === lastTime && lastBar) {
            open = Number(lastBar.open);
            high = Math.max(Number(lastBar.high), high, close);
            low = Math.min(Number(lastBar.low), low, close);
            volume = Math.max(Number(lastBar.volume || 0), volume);
          } else {
            open = close;
            high = close;
            low = close;
          }
          barTime = targetTime as Time;
        } else if (barTime < lastTime) {
          return;
        }
      }

      const mergedBar = { time: barTime, open, high, low, close, volume };
      if (lastBar && lastTime === barTime) {
        currentBars[currentBars.length - 1] = mergedBar;
      } else {
        currentBars.push(mergedBar);
      }

      chartBundle.candleSeries.update({
        time: barTime,
        open,
        high,
        low,
        close
      });

      chartBundle.volumeSeries.update({
        time: barTime,
        value: volume,
        color: close >= open ? 'rgba(34, 197, 94, 0.45)' : 'rgba(239, 68, 68, 0.45)'
      });

      const benchSym = getBenchmarkForSymbol(currentSymbol);
      const benchClose = currentBenchmarkClose ?? latestBenchmarkCloseBySymbol.get(benchSym) ?? null;
      if (benchClose && benchClose > 0) {
        const rsVal = Number(((close / benchClose) * 100).toFixed(4));
        if (Number.isFinite(rsVal)) {
          chartBundle.rsSeries.update({ time: barTime, value: rsVal });
        }
      } else if (isSymbolMatch(currentSymbol, benchSym)) {
        chartBundle.rsSeries.update({ time: barTime, value: 100 });
      }
    }
  });

  liveFeed.start();
}

export function isSymbolMatch(msgSymbol?: string, targetSymbol?: string): boolean {
  if (!msgSymbol || !targetSymbol) return false;
  const s1 = msgSymbol.toUpperCase();
  const s2 = targetSymbol.toUpperCase();
  if (s1 === s2) return true;
  if (s1.endsWith(':' + s2) || s2.endsWith(':' + s1)) return true;
  return false;
}

function attachDemoRiskReward() {
  if (!chartBundle || !drawingSyncEngine) return;

  const lastBar = currentBars.length > 0 ? currentBars[currentBars.length - 1] : { time: Math.floor(Date.now() / 1000) as Time, close: 100 };
  const lastPrice = lastBar.close || 100;
  const entryPrice = Number(lastPrice.toFixed(2));
  const stopPrice = Number((entryPrice * 0.95).toFixed(2)); // 5% stop
  const targetPrice = Number((entryPrice * 1.15).toFixed(2)); // 15% target (3:1 R:R)
  const startTime = normalizeTime(lastBar.time);
  const endTime = (typeof startTime === 'number' ? (startTime + 86400 * 20) : startTime) as Time;

  drawingSyncEngine.addRiskReward({
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
  });
}

if (typeof window !== 'undefined') {
  window.addEventListener('DOMContentLoaded', initTerminal);
}
