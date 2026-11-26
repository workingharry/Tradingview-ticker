import type { Time } from 'lightweight-charts';

export interface CandleDataPoint {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface VolumeDataPoint {
  time: Time;
  value: number;
  color: string;
}

export interface RSDataPoint {
  time: Time;
  value: number;
}

export interface ParsedHistoryData {
  bars: any[];
  candleData: CandleDataPoint[];
  volumeData: VolumeDataPoint[];
  rsData: RSDataPoint[];
  benchmarkClose?: number;
  fetchedAt: number;
}

export type FetchHistoryFn = (symbol: string, timeframe: string, signal?: AbortSignal) => Promise<any[]>;

export interface LRUCacheOptions {
  capacity?: number;
  ttlMs?: number;
  maxPrefetchConcurrency?: number;
  maxBarsPerEntry?: number;
}

export function normalizeTime(t: any): Time {
  if (typeof t === 'number') {
    return (t > 1e11 ? Math.floor(t / 1000) : t) as Time;
  }
  return t as Time;
}

export function isValidBar(b: any): boolean {
  if (!b || typeof b !== 'object') return false;
  const o = Number(b.open);
  const h = Number(b.high);
  const l = Number(b.low);
  const c = Number(b.close);
  if (!Number.isFinite(o) || !Number.isFinite(h) || !Number.isFinite(l) || !Number.isFinite(c)) {
    return false;
  }
  if (h < l) return false;
  return true;
}

function toComparableTime(t: Time): number | null {
  if (typeof t === 'number') return Number.isFinite(t) ? t : null;
  if (typeof t === 'string') {
    const ms = Date.parse(t);
    return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
  }
  return null;
}

export function calcRelativeStrength(
  candleData: CandleDataPoint[],
  benchmarkBars: any[]
): { rsData: RSDataPoint[]; benchmarkClose?: number } {
  if (!candleData.length || !Array.isArray(benchmarkBars) || !benchmarkBars.length) {
    return { rsData: [] };
  }

  const benchMap = new Map<string, number>();
  const validBench: Array<{ timeNum: number; close: number }> = [];

  for (let i = 0; i < benchmarkBars.length; i++) {
    const b = benchmarkBars[i];
    if (!b || typeof b !== 'object') continue;
    const close = Number(b.close);
    if (!Number.isFinite(close) || close <= 0) continue;
    const normT = normalizeTime(b.time);
    benchMap.set(String(normT), close);
    const timeNum = toComparableTime(normT);
    if (timeNum !== null) {
      validBench.push({ timeNum, close });
    }
  }

  if (!validBench.length && benchMap.size === 0) {
    return { rsData: [] };
  }

  validBench.sort((a, b) => a.timeNum - b.timeNum);

  const rsData: RSDataPoint[] = [];
  let bIdx = 0;
  let lastBenchClose: number | undefined;

  for (let i = 0; i < candleData.length; i++) {
    const c = candleData[i];
    let bClose = benchMap.get(String(c.time));
    if (bClose === undefined && validBench.length > 0) {
      const cTimeNum = toComparableTime(c.time);
      if (cTimeNum !== null) {
        while (bIdx + 1 < validBench.length && validBench[bIdx + 1].timeNum <= cTimeNum) {
          bIdx++;
        }
        if (validBench[bIdx].timeNum <= cTimeNum) {
          bClose = validBench[bIdx].close;
        }
      }
    }

    if (bClose !== undefined && Number.isFinite(bClose) && bClose > 0) {
      const rsVal = Number(((c.close / bClose) * 100).toFixed(4));
      if (Number.isFinite(rsVal)) {
        rsData.push({ time: c.time, value: rsVal });
        lastBenchClose = bClose;
      }
    }
  }

  return { rsData, benchmarkClose: lastBenchClose };
}

export function parseBarsToChartData(
  bars: any[],
  maxBars: number = 500,
  benchmarkBars?: any[]
): {
  candleData: CandleDataPoint[];
  volumeData: VolumeDataPoint[];
  rsData: RSDataPoint[];
  benchmarkClose?: number;
} {
  const candleData: CandleDataPoint[] = [];
  const volumeData: VolumeDataPoint[] = [];

  const slice = bars.length > maxBars ? bars.slice(bars.length - maxBars) : bars;

  for (let i = 0; i < slice.length; i++) {
    const b = slice[i];
    if (!isValidBar(b)) continue;

    const time = normalizeTime(b.time);
    const open = Number(b.open);
    const high = Number(b.high);
    const low = Number(b.low);
    const close = Number(b.close);
    const rawVol = Number(b.volume || 0);
    const volume = Number.isFinite(rawVol) && rawVol >= 0 ? rawVol : 0;

    candleData.push({ time, open, high, low, close });
    volumeData.push({
      time,
      value: volume,
      color: close >= open ? 'rgba(34, 197, 94, 0.45)' : 'rgba(239, 68, 68, 0.45)'
    });
  }

  if (benchmarkBars && benchmarkBars.length > 0) {
    const { rsData, benchmarkClose } = calcRelativeStrength(candleData, benchmarkBars);
    return { candleData, volumeData, rsData, benchmarkClose };
  }

  const firstClose = candleData.length > 0 && candleData[0].close > 0 ? candleData[0].close : 1;
  const rsData: RSDataPoint[] = candleData.map((b) => ({
    time: b.time,
    value: Number(((b.close / firstClose) * 100).toFixed(2))
  }));

  return { candleData, volumeData, rsData };
}

interface PrefetchQueueItem {
  symbol: string;
  timeframe: string;
  generation: number;
  fetchFn: FetchHistoryFn;
  benchmarkSymbol?: string;
}

export class LRUPrefetchCache {
  private readonly capacity: number;
  private readonly ttlMs: number;
  private readonly maxPrefetchConcurrency: number;
  private readonly maxBarsPerEntry: number;

  private cache = new Map<string, ParsedHistoryData>();
  private inFlight = new Map<string, Promise<ParsedHistoryData>>();
  private prefetchQueue: PrefetchQueueItem[] = [];
  private activePrefetchCount = 0;
  private currentPrefetchGen = 0;
  private isDisposed = false;

  constructor(options: LRUCacheOptions = {}) {
    this.capacity = options.capacity ?? 50;
    this.ttlMs = options.ttlMs ?? 300_000; // 5 minutes default
    this.maxPrefetchConcurrency = options.maxPrefetchConcurrency ?? 2;
    this.maxBarsPerEntry = options.maxBarsPerEntry ?? 500;
  }

  public buildKey(symbol: string, timeframe: string): string {
    return `${symbol.trim().toUpperCase()}:${timeframe.trim().toUpperCase()}`;
  }

  public has(symbol: string, timeframe: string): boolean {
    if (this.isDisposed) return false;
    const key = this.buildKey(symbol, timeframe);
    const entry = this.cache.get(key);
    if (!entry) return false;
    if (Date.now() - entry.fetchedAt > this.ttlMs) {
      this.cache.delete(key);
      return false;
    }
    return true;
  }

  public get(symbol: string, timeframe: string): ParsedHistoryData | null {
    if (this.isDisposed) return null;
    const key = this.buildKey(symbol, timeframe);
    const entry = this.cache.get(key);
    if (!entry) return null;

    if (Date.now() - entry.fetchedAt > this.ttlMs) {
      this.cache.delete(key);
      return null;
    }

    // Refresh LRU position by re-inserting
    this.cache.delete(key);
    this.cache.set(key, entry);
    return entry;
  }

  public set(symbol: string, timeframe: string, data: ParsedHistoryData): void {
    if (this.isDisposed) return;
    const key = this.buildKey(symbol, timeframe);
    if (this.cache.has(key)) {
      this.cache.delete(key);
    } else if (this.cache.size >= this.capacity) {
      // Evict oldest entry (first item in Map iterator)
      const oldestKey = this.cache.keys().next().value;
      if (oldestKey) {
        this.cache.delete(oldestKey);
      }
    }
    this.cache.set(key, data);
  }

  public async load(
    symbol: string,
    timeframe: string,
    fetchFn: FetchHistoryFn,
    signal?: AbortSignal,
    benchmarkSymbol?: string
  ): Promise<ParsedHistoryData> {
    if (this.isDisposed) {
      throw new Error('LRUPrefetchCache is disposed');
    }

    const cached = this.get(symbol, timeframe);
    if (cached) {
      return cached;
    }

    const key = this.buildKey(symbol, timeframe);
    const pending = this.inFlight.get(key);

    // If fetch is already in flight, decouple consumer signal from shared promise
    if (pending) {
      if (signal) {
        return this.wrapWithSignal(pending, signal);
      }
      return pending;
    }

    const cleanSym = symbol.trim().toUpperCase();
    const cleanBench = benchmarkSymbol?.trim().toUpperCase();
    const isSelfBenchmark =
      !!cleanBench &&
      (cleanBench === cleanSym ||
        cleanBench.endsWith(':' + cleanSym) ||
        cleanSym.endsWith(':' + cleanBench));
    const shouldFetchBench = !!cleanBench && !isSelfBenchmark;

    const fetchPromise = (async () => {
      try {
        const [rawBars, benchData] = await Promise.all([
          fetchFn(symbol, timeframe),
          shouldFetchBench
            ? this.load(cleanBench!, timeframe, fetchFn).catch(() => null)
            : Promise.resolve(null)
        ]);
        const benchBars = isSelfBenchmark ? rawBars : benchData?.bars;
        const { candleData, volumeData, rsData, benchmarkClose } = parseBarsToChartData(
          rawBars,
          this.maxBarsPerEntry,
          benchBars
        );
        const parsed: ParsedHistoryData = {
          bars: rawBars,
          candleData,
          volumeData,
          rsData,
          benchmarkClose,
          fetchedAt: Date.now()
        };
        // Avoid poisoning the cache with degraded RS data if benchmark fetch failed
        if (!shouldFetchBench || (benchBars && benchBars.length > 0)) {
          this.set(symbol, timeframe, parsed);
        }
        return parsed;
      } finally {
        this.inFlight.delete(key);
      }
    })();

    this.inFlight.set(key, fetchPromise);

    if (signal) {
      return this.wrapWithSignal(fetchPromise, signal);
    }
    return fetchPromise;
  }

  private wrapWithSignal<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
    if (signal.aborted) {
      return Promise.reject(new DOMException('Aborted', 'AbortError'));
    }

    return new Promise<T>((resolve, reject) => {
      const abortHandler = () => {
        reject(new DOMException('Aborted', 'AbortError'));
      };
      signal.addEventListener('abort', abortHandler, { once: true });

      promise
        .then((res) => {
          signal.removeEventListener('abort', abortHandler);
          resolve(res);
        })
        .catch((err) => {
          signal.removeEventListener('abort', abortHandler);
          reject(err);
        });
    });
  }

  public prefetch(
    symbols: string[],
    timeframe: string,
    generation: number,
    fetchFn: FetchHistoryFn,
    benchmarkSymbol?: string
  ): void {
    if (this.isDisposed) return;

    // Discard any older generation queue items when a new generation arrives
    if (generation > this.currentPrefetchGen) {
      this.currentPrefetchGen = generation;
      this.cancelPrefetch();
    }

    for (const sym of symbols) {
      const cleanSym = sym.trim().toUpperCase();
      if (!cleanSym) continue;

      const key = this.buildKey(cleanSym, timeframe);
      if (this.has(cleanSym, timeframe) || this.inFlight.has(key)) {
        continue;
      }

      const alreadyQueued = this.prefetchQueue.some(
        (q) => q.symbol === cleanSym && q.timeframe === timeframe.trim().toUpperCase()
      );
      if (!alreadyQueued) {
        this.prefetchQueue.push({
          symbol: cleanSym,
          timeframe: timeframe.trim().toUpperCase(),
          generation,
          fetchFn,
          benchmarkSymbol
        });
      }
    }

    this.processPrefetchQueue();
  }

  public cancelPrefetch(): void {
    this.prefetchQueue = [];
  }

  private processPrefetchQueue(): void {
    if (this.isDisposed) return;

    while (
      this.activePrefetchCount < this.maxPrefetchConcurrency &&
      this.prefetchQueue.length > 0
    ) {
      const item = this.prefetchQueue.shift();
      if (!item) break;

      // Drop stale generation prefetch tasks
      if (item.generation < this.currentPrefetchGen) {
        continue;
      }

      if (this.has(item.symbol, item.timeframe)) {
        continue;
      }

      this.activePrefetchCount++;
      this.load(item.symbol, item.timeframe, item.fetchFn, undefined, item.benchmarkSymbol)
        .catch(() => {
          // Non-critical background failure is silently swallowed
        })
        .finally(() => {
          this.activePrefetchCount--;
          this.processPrefetchQueue();
        });
    }
  }

  public invalidate(predicate: (symbol: string, timeframe: string) => boolean): void {
    for (const key of Array.from(this.cache.keys())) {
      const sepIdx = key.lastIndexOf(':');
      const sym = sepIdx === -1 ? key : key.slice(0, sepIdx);
      const tf = sepIdx === -1 ? '' : key.slice(sepIdx + 1);
      if (predicate(sym, tf)) {
        this.cache.delete(key);
      }
    }
  }

  public size(): number {
    return this.cache.size;
  }

  public clear(): void {
    this.cache.clear();
    this.inFlight.clear();
    this.cancelPrefetch();
    this.activePrefetchCount = 0;
  }

  public dispose(): void {
    this.isDisposed = true;
    this.clear();
  }
}
