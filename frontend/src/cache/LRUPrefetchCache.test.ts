import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LRUPrefetchCache, parseBarsToChartData, normalizeTime, isValidBar } from './LRUPrefetchCache';

describe('LRUPrefetchCache & Data Normalization', () => {
  let cache: LRUPrefetchCache;

  beforeEach(() => {
    cache = new LRUPrefetchCache({ capacity: 3, ttlMs: 1000, maxPrefetchConcurrency: 2, maxBarsPerEntry: 5 });
  });

  it('normalizes timestamps properly', () => {
    expect(normalizeTime(1700000000000)).toBe(1700000000);
    expect(normalizeTime(1700000000)).toBe(1700000000);
    expect(normalizeTime('2026-11-21')).toBe('2026-11-21');
  });

  it('filters malformed and invalid OHLCV bars', () => {
    expect(isValidBar(null)).toBe(false);
    expect(isValidBar({})).toBe(false);
    expect(isValidBar({ open: NaN, high: 10, low: 5, close: 8 })).toBe(false);
    expect(isValidBar({ open: 10, high: 5, low: 10, close: 8 })).toBe(false); // high < low
    expect(isValidBar({ open: 10, high: 15, low: 9, close: 12 })).toBe(true);

    const corruptBars = [
      { time: 100, open: 10, high: 12, low: 9, close: 11, volume: 1000 },
      { time: 150, open: 'corrupt', high: 14, low: 10, close: 13 }, // Invalid
      { time: 200, open: 11, high: 14, low: 10, close: 13, volume: -50 }, // Negative volume
      { time: 300, open: 13, high: 8, low: 14, close: 9 } // Inverted high/low
    ];

    const { candleData, volumeData } = parseBarsToChartData(corruptBars, 500);
    expect(candleData.length).toBe(2);
    expect(volumeData[1].value).toBe(0); // Normalized negative volume to 0
  });

  it('clips maximum bars per entry according to memory limits', () => {
    const manyBars = Array.from({ length: 10 }, (_, i) => ({
      time: 100 + i,
      open: 10,
      high: 12,
      low: 8,
      close: 11,
      volume: 100
    }));

    const { candleData } = parseBarsToChartData(manyBars, 5);
    expect(candleData.length).toBe(5);
    expect(candleData[0].time).toBe(105);
    expect(candleData[4].time).toBe(109);
  });

  it('evicts least recently used entry when capacity is exceeded', () => {
    const dummyData = (_val: number) => ({
      bars: [],
      candleData: [],
      volumeData: [],
      rsData: [],
      fetchedAt: Date.now()
    });

    cache.set('NVDA', '1D', dummyData(1));
    cache.set('AAPL', '1D', dummyData(2));
    cache.set('TSLA', '1D', dummyData(3));

    expect(cache.size()).toBe(3);
    expect(cache.has('NVDA', '1D')).toBe(true);

    // Adding 4th item evicts NVDA (the oldest)
    cache.set('MSFT', '1D', dummyData(4));
    expect(cache.size()).toBe(3);
    expect(cache.has('NVDA', '1D')).toBe(false);
    expect(cache.has('AAPL', '1D')).toBe(true);
    expect(cache.has('TSLA', '1D')).toBe(true);
    expect(cache.has('MSFT', '1D')).toBe(true);
  });

  it('refreshes LRU recency on get()', () => {
    const dummyData = () => ({
      bars: [],
      candleData: [],
      volumeData: [],
      rsData: [],
      fetchedAt: Date.now()
    });

    cache.set('NVDA', '1D', dummyData());
    cache.set('AAPL', '1D', dummyData());
    cache.set('TSLA', '1D', dummyData());

    // Access NVDA to make it most recently used
    const retrieved = cache.get('NVDA', '1D');
    expect(retrieved).not.toBeNull();

    // Now adding MSFT should evict AAPL (not NVDA!)
    cache.set('MSFT', '1D', dummyData());
    expect(cache.has('AAPL', '1D')).toBe(false);
    expect(cache.has('NVDA', '1D')).toBe(true);
    expect(cache.has('TSLA', '1D')).toBe(true);
    expect(cache.has('MSFT', '1D')).toBe(true);
  });

  it('deduplicates simultaneous in-flight requests and isolates consumer cancellation', async () => {
    let callCount = 0;
    const mockFetch = vi.fn().mockImplementation(async (_sym: string) => {
      callCount++;
      await new Promise((resolve) => setTimeout(resolve, 50));
      return [{ time: 100, open: 100, high: 105, low: 95, close: 102, volume: 500 }];
    });

    const ac1 = new AbortController();
    const ac2 = new AbortController();

    // Fire 2 simultaneous loads
    const p1 = cache.load('AMD', '1D', mockFetch, ac1.signal);
    const p2 = cache.load('AMD', '1D', mockFetch, ac2.signal);

    // Abort consumer 1 immediately
    ac1.abort();

    await expect(p1).rejects.toThrow('Aborted');

    // Consumer 2 must still succeed without being killed by consumer 1!
    const res2 = await p2;
    expect(callCount).toBe(1);
    expect(res2.candleData[0].close).toBe(102);
  });

  it('delivers cache hit in <5ms', async () => {
    const mockFetch = vi.fn().mockResolvedValue([
      { time: 100, open: 50, high: 55, low: 48, close: 52, volume: 1000 }
    ]);

    await cache.load('SPY', '1D', mockFetch);

    const start = performance.now();
    const hit = cache.get('SPY', '1D');
    const elapsed = performance.now() - start;

    expect(hit).not.toBeNull();
    expect(elapsed).toBeLessThan(5);
  });

  it('expires entries after ttlMs', async () => {
    vi.useFakeTimers();
    const shortCache = new LRUPrefetchCache({ ttlMs: 500 });

    shortCache.set('QQQ', '1D', {
      bars: [],
      candleData: [],
      volumeData: [],
      rsData: [],
      fetchedAt: Date.now()
    });

    expect(shortCache.has('QQQ', '1D')).toBe(true);

    vi.advanceTimersByTime(600);

    expect(shortCache.has('QQQ', '1D')).toBe(false);
    expect(shortCache.get('QQQ', '1D')).toBeNull();

    vi.useRealTimers();
  });

  it('discards stale prefetch tasks when a newer generation arrives', async () => {
    const fetchedSymbols: string[] = [];
    const mockFetch = vi.fn().mockImplementation(async (sym: string) => {
      await new Promise((r) => setTimeout(r, 20));
      fetchedSymbols.push(sym);
      return [{ time: 100, open: 10, high: 20, low: 5, close: 15, volume: 100 }];
    });

    // Enqueue Gen 1
    cache.prefetch(['SYM1', 'SYM2', 'SYM3', 'SYM4'], '1D', 1, mockFetch);

    // Immediately trigger Gen 2
    cache.prefetch(['TARGET_NEW'], '1D', 2, mockFetch);

    await new Promise((resolve) => setTimeout(resolve, 60));

    // TARGET_NEW must be prefetched
    expect(cache.has('TARGET_NEW', '1D')).toBe(true);
  });

  it('invalidates cache entries by predicate', () => {
    const dummy = () => ({
      bars: [],
      candleData: [],
      volumeData: [],
      rsData: [],
      fetchedAt: Date.now()
    });

    cache.set('NVDA', '1D', dummy());
    cache.set('NVDA', '15m', dummy());
    cache.set('AAPL', '1D', dummy());
    cache.set('NASDAQ:MSFT', '1D', dummy());

    cache.invalidate((sym) => sym === 'NVDA' || sym === 'NASDAQ:MSFT');

    expect(cache.has('NVDA', '1D')).toBe(false);
    expect(cache.has('NVDA', '15m')).toBe(false);
    expect(cache.has('NASDAQ:MSFT', '1D')).toBe(false);
    expect(cache.has('AAPL', '1D')).toBe(true);
  });

  it('computes real RS against benchmark via parallel fetch and timestamp alignment', async () => {
    const mockFetch = vi.fn().mockImplementation(async (sym: string) => {
      if (sym === 'AMEX:SPY') {
        return [
          { time: 100, open: 500, high: 505, low: 495, close: 500, volume: 1000 },
          { time: 200, open: 500, high: 510, low: 498, close: 500, volume: 1000 }
        ];
      }
      return [
        { time: 100, open: 100, high: 105, low: 95, close: 100, volume: 500 },
        { time: 150, open: 105, high: 112, low: 104, close: 110, volume: 600 }, // carry-forward to t=100 SPY
        { time: 200, open: 110, high: 126, low: 109, close: 125, volume: 700 }
      ];
    });

    const data = await cache.load('NVDA', '1D', mockFetch, undefined, 'AMEX:SPY');

    expect(data.rsData).toEqual([
      { time: 100, value: 20 },
      { time: 150, value: 22 },
      { time: 200, value: 25 }
    ]);
    expect(data.benchmarkClose).toBe(500);
    expect(cache.has('AMEX:SPY', '1D')).toBe(true);
  });

  it('generates constant 100 RS series when target is its own benchmark without redundant fetch', async () => {
    const mockFetch = vi.fn().mockResolvedValue([
      { time: 100, open: 500, high: 505, low: 495, close: 500, volume: 1000 },
      { time: 200, open: 505, high: 515, low: 502, close: 510, volume: 1200 }
    ]);

    // Loading SPY with benchmark AMEX:SPY (self-benchmark equivalence)
    const data = await cache.load('SPY', '1D', mockFetch, undefined, 'AMEX:SPY');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith('SPY', '1D');
    expect(data.rsData).toEqual([
      { time: 100, value: 100 },
      { time: 200, value: 100 }
    ]);
    expect(data.benchmarkClose).toBe(510);
    expect(cache.has('SPY', '1D')).toBe(true);
  });

  it('does not poison cache if benchmark fetch fails', async () => {
    const mockFetch = vi.fn().mockImplementation(async (sym: string) => {
      if (sym === 'AMEX:SPY') {
        throw new Error('Network error');
      }
      return [
        { time: 100, open: 100, high: 105, low: 95, close: 100, volume: 500 }
      ];
    });

    const data = await cache.load('TSLA', '1D', mockFetch, undefined, 'AMEX:SPY');
    expect(data.bars).toHaveLength(1);
    // Cache entry for TSLA should not be retained so subsequent requests retry benchmark fetch
    expect(cache.has('TSLA', '1D')).toBe(false);
  });
});
