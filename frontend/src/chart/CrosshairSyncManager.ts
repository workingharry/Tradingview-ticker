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
 * Binary search to find the floor bar time (highest timestamp <= targetSec).
 * Essential for aligning higher timeframes (1h, 1D) with lower timeframes (15m).
 */
export function findFloorBarTime(bars: Array<{ time: any }>, targetSec: number): any {
  if (!bars || bars.length === 0 || targetSec <= 0) return null;
  let low = 0;
  let high = bars.length - 1;
  let best: any = null;

  while (low <= high) {
    const mid = (low + high) >> 1;
    const barSec = normalizeToUtcSec(bars[mid].time);
    if (barSec <= targetSec) {
      best = bars[mid].time;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return best;
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
          const floorTime = findFloorBarTime(bars, sourceUtcSec);

          if (floorTime !== null) {
            // If source price is valid, align crosshair at source price, otherwise use matching bar close
            let targetPrice = sourcePrice;
            if (targetPrice === null || isNaN(targetPrice)) {
              const matchedBar = bars.find((b) => b.time === floorTime);
              targetPrice = matchedBar?.close ?? 100;
            }
            p.chart.setCrosshairPosition(targetPrice, floorTime, p.series);
          } else {
            p.chart.clearCrosshairPosition();
          }
        } catch {
          // Ignore sync errors on busy/resizing charts
        }
      }
    } finally {
      this._isSyncing = false;
    }
  }
}
