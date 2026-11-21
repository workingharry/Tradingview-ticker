import { describe, it, expect, vi } from 'vitest';
import type { Time } from 'lightweight-charts';
import {
  findFloorBarTime,
  findFloorBar,
  normalizeToUtcSec,
  CrosshairSyncManager,
  type ChartSyncParticipant
} from './CrosshairSyncManager';

describe('CrosshairSyncManager', () => {
  describe('normalizeToUtcSec', () => {
    it('normalizes seconds timestamp correctly', () => {
      expect(normalizeToUtcSec(1700000000)).toBe(1700000000);
    });

    it('normalizes millisecond timestamp to seconds', () => {
      expect(normalizeToUtcSec(1700000000000)).toBe(1700000000);
    });

    it('normalizes date string to UTC seconds', () => {
      const sec = normalizeToUtcSec('2026-11-20T00:00:00Z');
      expect(sec).toBe(Math.floor(new Date('2026-11-20T00:00:00Z').getTime() / 1000));
    });

    it('normalizes business day object to UTC seconds', () => {
      const sec = normalizeToUtcSec({ year: 2026, month: 11, day: 20 });
      expect(sec).toBe(Math.floor(Date.UTC(2026, 10, 20) / 1000));
    });
  });

  describe('findFloorBar and findFloorBarTime', () => {
    const hourlyBars = [
      { time: 10000, close: 100 }, // 10:00
      { time: 13600, close: 102 }, // 11:00
      { time: 17200, close: 105 }, // 12:00
    ];

    it('matches exact bar time and returns O(1) floor details', () => {
      expect(findFloorBarTime(hourlyBars, 13600)).toBe(13600);
      const res = findFloorBar(hourlyBars, 13600);
      expect(res).toEqual({ time: 13600, index: 1, close: 102 });
    });

    it('downward-aligns intermediate 15m time to the hourly floor bar', () => {
      // 10:15 (10900) should align to 10:00 (10000)
      expect(findFloorBarTime(hourlyBars, 10900)).toBe(10000);
      const res1 = findFloorBar(hourlyBars, 10900);
      expect(res1?.index).toBe(0);
      expect(res1?.close).toBe(100);

      // 11:45 (16300) should align to 11:00 (13600)
      expect(findFloorBarTime(hourlyBars, 16300)).toBe(13600);
      const res2 = findFloorBar(hourlyBars, 16300);
      expect(res2?.index).toBe(1);
      expect(res2?.close).toBe(102);
    });

    it('returns null when target time is before the first bar', () => {
      expect(findFloorBarTime(hourlyBars, 9000)).toBeNull();
      expect(findFloorBar(hourlyBars, 9000)).toBeNull();
    });

    it('returns latest bar when target time is after the last bar', () => {
      expect(findFloorBarTime(hourlyBars, 25000)).toBe(17200);
      const res = findFloorBar(hourlyBars, 25000);
      expect(res?.index).toBe(2);
      expect(res?.close).toBe(105);
    });
  });

  describe('CrosshairSyncManager interaction and recursion lock', () => {
    it('propagates crosshair position to other charts with floor time alignment and prevents recursion', () => {
      const manager = new CrosshairSyncManager();

      let handlerA: any = null;

      const mockChartA: any = {
        subscribeCrosshairMove: vi.fn((fn) => { handlerA = fn; }),
        unsubscribeCrosshairMove: vi.fn(),
        setCrosshairPosition: vi.fn(),
        clearCrosshairPosition: vi.fn()
      };

      const mockChartB: any = {
        subscribeCrosshairMove: vi.fn(),
        unsubscribeCrosshairMove: vi.fn(),
        setCrosshairPosition: vi.fn(),
        clearCrosshairPosition: vi.fn()
      };

      const mockSeriesA: any = {
        coordinateToPrice: vi.fn(() => 150.5),
        priceToCoordinate: vi.fn(() => 200)
      };

      const mockSeriesB: any = {
        coordinateToPrice: vi.fn(() => 150.5),
        priceToCoordinate: vi.fn(() => 200)
      };

      const barsA = [{ time: 10000 as Time, close: 140 }, { time: 10900 as Time, close: 150 }];
      const barsB = [{ time: 10000 as Time, close: 140 }, { time: 13600 as Time, close: 155 }];

      const pA: ChartSyncParticipant = {
        id: 'paneA',
        chart: mockChartA,
        series: mockSeriesA,
        getBars: () => barsA
      };

      const pB: ChartSyncParticipant = {
        id: 'paneB',
        chart: mockChartB,
        series: mockSeriesB,
        getBars: () => barsB
      };

      manager.register(pA);
      manager.register(pB);

      // Trigger crosshair move on Chart A at time 10900 (15m candle)
      handlerA({
        time: 10900,
        point: { x: 50, y: 120 }
      });

      // Chart B should receive setCrosshairPosition with floor bar 10000
      expect(mockChartB.setCrosshairPosition).toHaveBeenCalledWith(150.5, 10000, mockSeriesB);

      // Chart A should NOT have received setCrosshairPosition (recursion prevention)
      expect(mockChartA.setCrosshairPosition).not.toHaveBeenCalled();

      // Trigger mouse leave on Chart A
      handlerA({
        time: undefined,
        point: undefined
      });

      expect(mockChartB.clearCrosshairPosition).toHaveBeenCalled();

      // Clean up
      manager.clearAll();
      expect(mockChartA.unsubscribeCrosshairMove).toHaveBeenCalled();
      expect(mockChartB.unsubscribeCrosshairMove).toHaveBeenCalled();
    });

    it('decouples price coordinate on multi-ticker participants to prevent dimension distortion', () => {
      const manager = new CrosshairSyncManager();

      let handlerNVDA: any = null;

      const mockChartNVDA: any = {
        subscribeCrosshairMove: vi.fn((fn) => { handlerNVDA = fn; }),
        unsubscribeCrosshairMove: vi.fn(),
        setCrosshairPosition: vi.fn(),
        clearCrosshairPosition: vi.fn()
      };

      const mockChartSPY: any = {
        subscribeCrosshairMove: vi.fn(),
        unsubscribeCrosshairMove: vi.fn(),
        setCrosshairPosition: vi.fn(),
        clearCrosshairPosition: vi.fn()
      };

      const mockSeriesNVDA: any = {
        coordinateToPrice: vi.fn(() => 140.0), // NVDA price $140
      };

      const mockSeriesSPY: any = {
        coordinateToPrice: vi.fn(() => 595.0), // SPY price $595
      };

      const barsNVDA = [{ time: 10000 as Time, close: 140 }];
      const barsSPY = [{ time: 10000 as Time, close: 592.5 }]; // SPY native close is 592.5

      const pNVDA: ChartSyncParticipant = {
        id: 'nvdaPane',
        chart: mockChartNVDA,
        series: mockSeriesNVDA,
        getBars: () => barsNVDA,
        symbol: 'NVDA'
      };

      const pSPY: ChartSyncParticipant = {
        id: 'spyPane',
        chart: mockChartSPY,
        series: mockSeriesSPY,
        getBars: () => barsSPY,
        symbol: 'SPY' // Different symbol!
      };

      manager.register(pNVDA);
      manager.register(pSPY);

      handlerNVDA({
        time: 10000,
        point: { x: 50, y: 120 }
      });

      // Crucial assertion: SPY must receive SPY's native bar close (592.5), NOT NVDA's $140!
      expect(mockChartSPY.setCrosshairPosition).toHaveBeenCalledWith(592.5, 10000, mockSeriesSPY);
    });
  });
});
