import type { ISeriesApi, SeriesType, Time } from 'lightweight-charts';
import { RiskRewardPrimitive, type RiskRewardState } from './primitives/RiskRewardPrimitive';
import { apiFetch } from '../api/client';

export interface StoredDrawing {
  id: string;
  symbol: string;
  pane_id: string;
  tool_type: string;
  timeframe_scope: string;
  data: any;
  revision?: number;
  created_at?: number;
  updated_at?: number;
  is_deleted?: boolean;
}

export interface DrawingSyncEngineOptions {
  getSeries: () => ISeriesApi<SeriesType, Time> | null;
  debounceMs?: number;
  apiSaveFn?: (drawing: StoredDrawing) => Promise<any>;
  apiDeleteFn?: (id: string) => Promise<any>;
  apiClearFn?: (symbol: string) => Promise<any>;
  apiFetchFn?: (symbol: string) => Promise<StoredDrawing[]>;
}

export class DrawingSyncEngine {
  private getSeries: () => ISeriesApi<SeriesType, Time> | null;
  private debounceMs: number;
  private currentSymbol = '';
  private currentGeneration = 0;
  private activePrimitives = new Map<string, RiskRewardPrimitive>();
  private saveDebounceTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private revisions = new Map<string, number>();
  private isDestroyed = false;

  private apiSaveFn: (drawing: StoredDrawing) => Promise<any>;
  private apiDeleteFn: (id: string) => Promise<any>;
  private apiClearFn: (symbol: string) => Promise<any>;
  private apiFetchFn: (symbol: string) => Promise<StoredDrawing[]>;

  constructor(options: DrawingSyncEngineOptions) {
    this.getSeries = options.getSeries;
    this.debounceMs = options.debounceMs ?? 300;

    this.apiSaveFn =
      options.apiSaveFn ||
      (async (d) => {
        return apiFetch('/api/drawings', {
          method: 'POST',
          body: JSON.stringify(d),
          headers: { 'Content-Type': 'application/json' }
        });
      });

    this.apiDeleteFn =
      options.apiDeleteFn ||
      (async (id) => {
        return apiFetch(`/api/drawings/${encodeURIComponent(id)}`, {
          method: 'DELETE'
        });
      });

    this.apiClearFn =
      options.apiClearFn ||
      (async (sym) => {
        return apiFetch(`/api/drawings?symbol=${encodeURIComponent(sym)}`, {
          method: 'DELETE'
        });
      });

    this.apiFetchFn =
      options.apiFetchFn ||
      (async (sym) => {
        const res = await apiFetch<{ status: string; drawings: StoredDrawing[] }>(
          `/api/drawings?symbol=${encodeURIComponent(sym)}`
        );
        return res?.drawings || [];
      });
  }

  public getCurrentSymbol(): string {
    return this.currentSymbol;
  }

  public getActiveCount(): number {
    return this.activePrimitives.size;
  }

  public getRevision(id: string): number {
    return this.revisions.get(id) || 1;
  }

  public async loadDrawings(symbol: string): Promise<void> {
    if (this.isDestroyed) return;
    const cleanSym = symbol.trim().toUpperCase();
    if (!cleanSym) return;

    const generation = ++this.currentGeneration;

    // 1. If symbol is changing, detach all old primitives and cancel timers
    if (this.currentSymbol !== cleanSym) {
      this.detachAllFromChart();
      this.currentSymbol = cleanSym;
    }

    try {
      const drawings = await this.apiFetchFn(cleanSym);
      if (this.currentGeneration !== generation || this.currentSymbol !== cleanSym || this.isDestroyed) {
        return; // Discard stale response
      }

      const series = this.getSeries();
      if (!series) return;

      for (const d of drawings) {
        if (d.is_deleted) continue;
        if (this.activePrimitives.has(d.id)) continue;

        if (d.revision) {
          this.revisions.set(d.id, d.revision);
        }

        if (d.tool_type === 'risk_reward') {
          const rawState = d.data as RiskRewardState;
          if (rawState && rawState.entryPrice) {
            this.instantiateAndAttach(rawState, false);
          }
        }
      }
    } catch (err) {
      console.error('[DrawingSyncEngine] Failed to load drawings for', cleanSym, err);
    }
  }

  public addRiskReward(state: RiskRewardState): RiskRewardPrimitive | null {
    if (this.isDestroyed || !this.currentSymbol) return null;
    const primitive = this.instantiateAndAttach(state, true);
    return primitive;
  }

  private instantiateAndAttach(state: RiskRewardState, triggerImmediateSave: boolean): RiskRewardPrimitive | null {
    const series = this.getSeries();
    if (!series) return null;

    const primitive = new RiskRewardPrimitive(state, {
      onChange: (updatedState) => {
        this.queueDebouncedSave(updatedState);
      },
      onRemove: (id) => {
        this.removeDrawingLocallyAndRemotely(id);
      }
    });

    series.attachPrimitive(primitive);
    this.activePrimitives.set(state.id, primitive);

    if (triggerImmediateSave) {
      this.persistToServer({
        id: state.id,
        symbol: this.currentSymbol,
        pane_id: 'candle',
        tool_type: 'risk_reward',
        timeframe_scope: 'ALL',
        revision: this.revisions.get(state.id) || 1,
        data: state
      });
    }

    return primitive;
  }

  public queueDebouncedSave(state: RiskRewardState): void {
    if (this.isDestroyed || !this.currentSymbol) return;

    const targetSymbol = this.currentSymbol;
    const drawingId = state.id;

    const existingTimer = this.saveDebounceTimers.get(drawingId);
    if (existingTimer) {
      clearTimeout(existingTimer);
    }

    const timer = setTimeout(() => {
      this.saveDebounceTimers.delete(drawingId);
      // Generation & symbol check: do not save if symbol has switched while debouncing
      if (this.currentSymbol !== targetSymbol || this.isDestroyed) {
        return;
      }

      this.persistToServer({
        id: drawingId,
        symbol: targetSymbol,
        pane_id: 'candle',
        tool_type: 'risk_reward',
        timeframe_scope: 'ALL',
        revision: this.revisions.get(drawingId) || 1,
        data: state
      });
    }, this.debounceMs);

    this.saveDebounceTimers.set(drawingId, timer);
  }

  private async persistToServer(drawing: StoredDrawing): Promise<void> {
    try {
      const res = await this.apiSaveFn(drawing);
      if (res && res.drawing && typeof res.drawing.revision === 'number') {
        this.revisions.set(drawing.id, res.drawing.revision);
      }
    } catch (err) {
      console.error('[DrawingSyncEngine] Failed to save drawing:', drawing.id, err);
    }
  }

  public async removeDrawingLocallyAndRemotely(id: string): Promise<void> {
    // 1. Cancel any pending debounce save
    const timer = this.saveDebounceTimers.get(id);
    if (timer) {
      clearTimeout(timer);
      this.saveDebounceTimers.delete(id);
    }

    // 2. Detach from series
    const primitive = this.activePrimitives.get(id);
    if (primitive) {
      const series = this.getSeries();
      try {
        series?.detachPrimitive(primitive);
      } catch {
        // Safe detach
      }
      this.activePrimitives.delete(id);
    }
    this.revisions.delete(id);

    // 3. Delete from backend SQLite
    try {
      await this.apiDeleteFn(id);
    } catch (err) {
      console.error('[DrawingSyncEngine] Failed to delete drawing:', id, err);
    }
  }

  public async clearAllDrawings(): Promise<void> {
    if (!this.currentSymbol) return;

    const sym = this.currentSymbol;
    this.detachAllFromChart();

    try {
      await this.apiClearFn(sym);
    } catch (err) {
      console.error('[DrawingSyncEngine] Failed to clear drawings for:', sym, err);
    }
  }

  public detachAllFromChart(): void {
    // Clear debounce timers
    for (const timer of this.saveDebounceTimers.values()) {
      clearTimeout(timer);
    }
    this.saveDebounceTimers.clear();

    const series = this.getSeries();
    for (const primitive of this.activePrimitives.values()) {
      try {
        series?.detachPrimitive(primitive);
      } catch {
        // Safe detach
      }
    }
    this.activePrimitives.clear();
    this.revisions.clear();
  }

  public reAttachAllToChart(): void {
    const series = this.getSeries();
    if (!series) return;

    for (const primitive of this.activePrimitives.values()) {
      try {
        series.attachPrimitive(primitive);
      } catch {
        // Safe re-attach
      }
    }
  }

  public destroy(): void {
    this.isDestroyed = true;
    this.detachAllFromChart();
  }
}
