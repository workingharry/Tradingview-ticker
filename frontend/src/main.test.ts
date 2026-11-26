import { describe, it, expect, vi } from 'vitest';
import { alignBarTimestamp, isSymbolMatch, getBenchmarkForSymbol } from './main';
import { LRUPrefetchCache } from './cache/LRUPrefetchCache';

describe('Main Module Re-Analysis & Edge Case Guardrails', () => {
  describe('alignBarTimestamp', () => {
    it('returns raw timestamp for 1m timeframe', () => {
      expect(alignBarTimestamp(1732200045, '1m')).toBe(1732200045);
    });

    it('aligns 1D timestamps to 86400s day boundary', () => {
      // 86400 * 20000 = 1728000000 (midnight UTC)
      const midnight = 1728000000;
      expect(alignBarTimestamp(midnight + 3600, '1D')).toBe(midnight);
      expect(alignBarTimestamp(midnight + 86399, '1D')).toBe(midnight);
    });

    it('aligns 1W weekly timestamp to Monday 00:00:00 UTC', () => {
      // 1970-01-05 00:00:00 UTC is Monday = 345600
      const mon1970 = 345600;
      expect(alignBarTimestamp(mon1970, '1W')).toBe(mon1970);

      // Mid-week Wednesday 1970-01-07 12:00:00 UTC = 345600 + 2.5 * 86400 = 561600 -> Monday
      expect(alignBarTimestamp(mon1970 + 216000, '1W')).toBe(mon1970);

      // End of week Sunday 1970-01-11 23:59:59 UTC -> Monday
      expect(alignBarTimestamp(mon1970 + 604799, '1W')).toBe(mon1970);

      // Next week Monday 1970-01-12 00:00:00 UTC -> next Monday
      expect(alignBarTimestamp(mon1970 + 604800, '1W')).toBe(mon1970 + 604800);

      // Contemporary date: 2026-11-23 (Monday) is 1795392000
      // 2026-11-26 (Thursday) is 1795392000 + 3 * 86400 = 1795651200
      const monday2026 = 1795392000;
      expect(alignBarTimestamp(monday2026, '1W')).toBe(monday2026);
      expect(alignBarTimestamp(monday2026 + 3 * 86400, '1W')).toBe(monday2026);
      expect(alignBarTimestamp(monday2026 + 6 * 86400, '1W')).toBe(monday2026);
    });
  });

  describe('isSymbolMatch', () => {
    it('matches exact tickers ignoring case', () => {
      expect(isSymbolMatch('nvda', 'NVDA')).toBe(true);
      expect(isSymbolMatch('AAPL', 'aapl')).toBe(true);
    });

    it('matches bare ticker with exchange-prefixed ticker', () => {
      expect(isSymbolMatch('NVDA', 'NASDAQ:NVDA')).toBe(true);
      expect(isSymbolMatch('NASDAQ:NVDA', 'NVDA')).toBe(true);
      expect(isSymbolMatch('SPY', 'AMEX:SPY')).toBe(true);
      expect(isSymbolMatch('AMEX:SPY', 'SPY')).toBe(true);
      expect(isSymbolMatch('BTCUSDT', 'BINANCE:BTCUSDT')).toBe(true);
    });

    it('returns false for mismatched or empty tickers', () => {
      expect(isSymbolMatch('NVDA', 'AMD')).toBe(false);
      expect(isSymbolMatch('NASDAQ:NVDA', 'NASDAQ:AMD')).toBe(false);
      expect(isSymbolMatch('', 'NVDA')).toBe(false);
      expect(isSymbolMatch('NVDA', undefined)).toBe(false);
    });
  });

  describe('getBenchmarkForSymbol', () => {
    it('assigns BINANCE:BTCUSDT for crypto pairs and AMEX:SPY for equities', () => {
      expect(getBenchmarkForSymbol('BTCUSDT')).toBe('BINANCE:BTCUSDT');
      expect(getBenchmarkForSymbol('BINANCE:ETHUSDT')).toBe('BINANCE:BTCUSDT');
      expect(getBenchmarkForSymbol('SOLBUSD')).toBe('BINANCE:BTCUSDT');
      expect(getBenchmarkForSymbol('NVDA')).toBe('AMEX:SPY');
      expect(getBenchmarkForSymbol('NASDAQ:AAPL')).toBe('AMEX:SPY');
      expect(getBenchmarkForSymbol('SPY')).toBe('AMEX:SPY');
    });
  });

  describe('onGap Invalidation & Stale Cache Bypassing', () => {
    it('invalidates cache so gap healing re-fetches fresh data from server', async () => {
      const cache = new LRUPrefetchCache({ ttlMs: 300_000 });
      let fetchCount = 0;
      const mockFetch = vi.fn().mockImplementation(async () => {
        fetchCount++;
        return [
          { time: 100, open: 10, high: 11, low: 9, close: 10, volume: 100 },
          { time: 200, open: 10, high: 12, low: 10, close: 11, volume: 150 }
        ];
      });

      // Initial load: hits server
      await cache.load('NVDA', '1D', mockFetch);
      expect(fetchCount).toBe(1);

      // Normal load: hits cache (<5ms), does not call fetchFn
      await cache.load('NVDA', '1D', mockFetch);
      expect(fetchCount).toBe(1);

      // On Gap detected: invalidate entry using isSymbolMatch
      cache.invalidate((sym, tf) => isSymbolMatch(sym, 'NASDAQ:NVDA') && tf === '1D');

      // Subsequent load after gap detection: must call fetchFn again!
      await cache.load('NVDA', '1D', mockFetch);
      expect(fetchCount).toBe(2);
    });

    it('handles lowercase timeframe (15m, 1h) case-insensitively during onGap and forceRefresh', async () => {
      const cache = new LRUPrefetchCache({ ttlMs: 300_000 });
      let fetchCount = 0;
      const mockFetch = vi.fn().mockImplementation(async () => {
        fetchCount++;
        return [
          { time: 100, open: 10, high: 11, low: 9, close: 10, volume: 100 }
        ];
      });

      const tf = '15m'; // lowercase
      await cache.load('TSLA', tf, mockFetch);
      expect(fetchCount).toBe(1);

      // Verify cached
      await cache.load('TSLA', tf, mockFetch);
      expect(fetchCount).toBe(1);

      // Invalidate using uppercase normalized check (matching main.ts fix)
      cache.invalidate((sym, cachedTf) => isSymbolMatch(sym, 'TSLA') && cachedTf === tf.trim().toUpperCase());

      // Subsequent load must re-fetch
      await cache.load('TSLA', tf, mockFetch);
      expect(fetchCount).toBe(2);
    });
  });

  describe('Watchlist Equivalent Symbol Deduplication', () => {
    it('prevents duplicate insertions when adding equivalent prefixed symbol', () => {
      const activeWatchlist = ['NVDA', 'TSLA', 'AAPL'];

      const addSymbol = (newSym: string) => {
        const clean = newSym.trim().toUpperCase();
        const existingIdx = activeWatchlist.findIndex((s) => isSymbolMatch(s, clean));
        if (existingIdx === -1) {
          activeWatchlist.push(clean);
        }
      };

      // Attempt to add NASDAQ:NVDA when NVDA already exists
      addSymbol('NASDAQ:NVDA');
      expect(activeWatchlist).toEqual(['NVDA', 'TSLA', 'AAPL']);

      // Attempt to add AMD (new)
      addSymbol('AMD');
      expect(activeWatchlist).toEqual(['NVDA', 'TSLA', 'AAPL', 'AMD']);

      // Attempt to add NASDAQ:AMD when AMD exists
      addSymbol('NASDAQ:AMD');
      expect(activeWatchlist).toEqual(['NVDA', 'TSLA', 'AAPL', 'AMD']);
    });
  });
});
