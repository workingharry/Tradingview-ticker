import type {
  IChartApi,
  ISeriesApi,
  SeriesType,
  Time,
  MouseEventParams
} from 'lightweight-charts';

export interface ChartSyncParticipant {
  id: string;
  chart: IChartApi;
  series: ISeriesApi<SeriesType, Time>;
  getBars: () => Array<{ time: Time; close?: number }>;
  symbol?: string;
  syncPrice?: boolean; // When false or when symbols differ, crosshair aligns to bar close instead of foreign price
}

export interface FloorBarResult {
  time: any;
  index: number;
  close?: number;
}

export function normalizeToUtcSec(time: any): number {
  if (typeof time === 'number') {
    return time > 1e11 ? Math.floor(time / 1000) : time;
  }
  if (typeof time === 'string') {
    const parsed = Date.parse(time);
    return isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
  }
  if (typeof time === 'object' && time !== null) {
    if ('year' in time && 'month' in time && 'day' in time) {
      const d = Date.UTC(time.year, time.month - 1, time.day);
      return Math.floor(d / 1000);
    }
  }
  return 0;
}

/**
 * Binary search to find the floor bar (highest timestamp <= targetSec).
 * Returns O(1) floor result with index and close price without subsequent array scans.
 */
export function findFloorBar(bars: Array<{ time: any; close?: number }>, targetSec: number): FloorBarResult | null {
  if (!bars || bars.length === 0 || targetSec <= 0) return null;
  let low = 0;
  let high = bars.length - 1;
  let bestIndex = -1;

  while (low <= high) {
    const mid = (low + high) >> 1;
    const barSec = normalizeToUtcSec(bars[mid].time);
    if (barSec <= targetSec) {
      bestIndex = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }

  if (bestIndex === -1) return null;
  const bestBar = bars[bestIndex];
  return {
    time: bestBar.time,
    index: bestIndex,
    close: bestBar.close
  };
}

/**
 * Backward-compatible helper returning solely the Time of the floor bar.
 */
export function findFloorBarTime(bars: Array<{ time: any; close?: number }>, targetSec: number): any {
  const res = findFloorBar(bars, targetSec);
  return res ? res.time : null;
}

export class CrosshairSyncManager {
  private _participants: Map<string, ChartSyncParticipant> = new Map();
  private _handlers: Map<string, (param: MouseEventParams<Time>) => void> = new Map();
  private _isSyncing = false;

  public register(participant: ChartSyncParticipant): () => void {
    const id = participant.id;
    if (this._participants.has(id)) {
      this.unregister(id);
    }

    this._participants.set(id, participant);

    const handler = (param: MouseEventParams<Time>) => {
      this._handleCrosshairMove(id, param);
    };

    this._handlers.set(id, handler);
    participant.chart.subscribeCrosshairMove(handler);

    return () => {
      this.unregister(id);
    };
  }

  public unregister(id: string): void {
    const participant = this._participants.get(id);
    const handler = this._handlers.get(id);

    if (participant && handler) {
      try {
        participant.chart.unsubscribeCrosshairMove(handler);
      } catch {
        // Ignore errors if chart is already disposed
      }
    }

    this._participants.delete(id);
    this._handlers.delete(id);
  }

  public clearAll(): void {
    for (const id of Array.from(this._participants.keys())) {
      this.unregister(id);
    }
  }

  private _handleCrosshairMove(sourceId: string, param: MouseEventParams<Time>): void {
    if (this._isSyncing) return;

    try {
      this._isSyncing = true;

      // Mouse left chart or no point hovered
      if (!param || !param.point || !param.time) {
        for (const [id, p] of this._participants.entries()) {
          if (id !== sourceId) {
            try {
              p.chart.clearCrosshairPosition();
            } catch {
              // Ignore disposed chart error
            }
          }
        }
        return;
      }

      const sourceUtcSec = normalizeToUtcSec(param.time);
      if (sourceUtcSec <= 0) return;

      const sourceParticipant = this._participants.get(sourceId);
      let sourcePrice: number | null = null;
      if (sourceParticipant) {
        sourcePrice = sourceParticipant.series.coordinateToPrice(param.point.y);
      }

      for (const [id, p] of this._participants.entries()) {
        if (id === sourceId) continue;

        try {
          const bars = p.getBars();
          const floor = findFloorBar(bars, sourceUtcSec);

          if (floor !== null) {
            // Multi-ticker / decoupled crosshair protection:
            // When symbols differ or syncPrice === false, align to the target instrument's native bar close
            const isDifferentSymbol = Boolean(
              sourceParticipant?.symbol && p.symbol && sourceParticipant.symbol !== p.symbol
            );
            const decouplePrice = p.syncPrice === false || isDifferentSymbol;

            let targetPrice: number;
            if (decouplePrice || sourcePrice === null || isNaN(sourcePrice)) {
              targetPrice = floor.close ?? 100;
            } else {
              targetPrice = sourcePrice;
            }

            p.chart.setCrosshairPosition(targetPrice, floor.time, p.series);
          } else {
            p.chart.clearCrosshairPosition();
          }
        } catch {
          // Ignore disposed chart error
        }
      }
    } finally {
      this._isSyncing = false;
    }
  }
}
