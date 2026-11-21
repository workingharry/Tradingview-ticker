import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DrawingSyncEngine, type StoredDrawing } from './DrawingSyncEngine';
import type { RiskRewardState } from './primitives/RiskRewardPrimitive';

describe('DrawingSyncEngine', () => {
  let mockSeries: any;
  let attachedPrimitives: any[] = [];
  let detachedPrimitives: any[] = [];
  let apiSaveFn: any;
  let apiDeleteFn: any;
  let apiClearFn: any;
  let apiFetchFn: any;

  beforeEach(() => {
    attachedPrimitives = [];
    detachedPrimitives = [];

    mockSeries = {
      attachPrimitive: vi.fn((p) => attachedPrimitives.push(p)),
      detachPrimitive: vi.fn((p) => detachedPrimitives.push(p)),
      priceToCoordinate: vi.fn(() => 100)
    };

    apiSaveFn = vi.fn().mockResolvedValue({ status: 'ok', drawing: { revision: 1 } });
    apiDeleteFn = vi.fn().mockResolvedValue({ status: 'ok' });
    apiClearFn = vi.fn().mockResolvedValue({ status: 'ok' });
    apiFetchFn = vi.fn().mockResolvedValue([]);
  });

  const createEngine = (debounceMs = 50) => {
    return new DrawingSyncEngine({
      getSeries: () => mockSeries,
      debounceMs,
      apiSaveFn,
      apiDeleteFn,
      apiClearFn,
      apiFetchFn
    });
  };

  const sampleState: RiskRewardState = {
    id: 'rr_1',
    startTime: 100 as any,
    endTime: 200 as any,
    entryPrice: 100,
    stopPrice: 95,
    targetPrice: 115,
    accountSize: 100000,
    riskPercent: 1.0,
    maxAllocationPercent: 25.0,
    adr20Percent: 4.5
  };

  it('loads and re-instantiates existing drawings for a symbol', async () => {
    const existing: StoredDrawing[] = [
      {
        id: 'rr_existing',
        symbol: 'NVDA',
        pane_id: 'candle',
        tool_type: 'risk_reward',
        timeframe_scope: 'ALL',
        revision: 3,
        data: sampleState
      }
    ];
    apiFetchFn.mockResolvedValueOnce(existing);

    const engine = createEngine();
    await engine.loadDrawings('NVDA');

    expect(apiFetchFn).toHaveBeenCalledWith('NVDA');
    expect(engine.getActiveCount()).toBe(1);
    expect(engine.getRevision('rr_existing')).toBe(3);
    expect(mockSeries.attachPrimitive).toHaveBeenCalledTimes(1);
  });

  it('adds a new RiskReward primitive and persists immediately to server', async () => {
    const engine = createEngine();
    await engine.loadDrawings('NVDA');

    const primitive = engine.addRiskReward(sampleState);

    expect(primitive).not.toBeNull();
    expect(engine.getActiveCount()).toBe(1);
    expect(mockSeries.attachPrimitive).toHaveBeenCalledTimes(1);

    expect(apiSaveFn).toHaveBeenCalledTimes(1);
    expect(apiSaveFn).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'rr_1',
        symbol: 'NVDA',
        tool_type: 'risk_reward',
        revision: 1,
        data: sampleState
      })
    );
  });

  it('debounces rapid updates on drag / resize', async () => {
    vi.useFakeTimers();
    const engine = createEngine(100);
    await engine.loadDrawings('NVDA');

    // Fire 3 quick updates within 50ms
    engine.queueDebouncedSave({ ...sampleState, entryPrice: 101 });
    engine.queueDebouncedSave({ ...sampleState, entryPrice: 102 });
    engine.queueDebouncedSave({ ...sampleState, entryPrice: 103 });

    expect(apiSaveFn).not.toHaveBeenCalled();

    // Fast-forward 120ms
    vi.advanceTimersByTime(120);

    expect(apiSaveFn).toHaveBeenCalledTimes(1);
    expect(apiSaveFn).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({ entryPrice: 103 })
      })
    );

    vi.useRealTimers();
  });

  it('removes drawing locally and calls remote delete', async () => {
    const engine = createEngine();
    await engine.loadDrawings('NVDA');
    engine.addRiskReward(sampleState);

    expect(engine.getActiveCount()).toBe(1);

    await engine.removeDrawingLocallyAndRemotely('rr_1');

    expect(engine.getActiveCount()).toBe(0);
    expect(mockSeries.detachPrimitive).toHaveBeenCalledTimes(1);
    expect(apiDeleteFn).toHaveBeenCalledWith('rr_1');
  });

  it('detaches previous primitives when switching to a different symbol', async () => {
    const engine = createEngine();
    await engine.loadDrawings('NVDA');
    engine.addRiskReward(sampleState);

    expect(engine.getActiveCount()).toBe(1);

    // Switch to AAPL
    apiFetchFn.mockResolvedValueOnce([]);
    await engine.loadDrawings('AAPL');

    expect(mockSeries.detachPrimitive).toHaveBeenCalledTimes(1);
    expect(engine.getActiveCount()).toBe(0);
    expect(engine.getCurrentSymbol()).toBe('AAPL');
  });

  it('clears all drawings for active symbol', async () => {
    const engine = createEngine();
    await engine.loadDrawings('NVDA');
    engine.addRiskReward(sampleState);

    await engine.clearAllDrawings();

    expect(engine.getActiveCount()).toBe(0);
    expect(mockSeries.detachPrimitive).toHaveBeenCalledTimes(1);
    expect(apiClearFn).toHaveBeenCalledWith('NVDA');
  });

  it('discards stale fetch responses when symbol switches rapidly (generation isolation)', async () => {
    let resolveNvda: any;
    const nvdaPromise = new Promise<StoredDrawing[]>((resolve) => {
      resolveNvda = resolve;
    });

    apiFetchFn.mockImplementation((sym: string) => {
      if (sym === 'NVDA') return nvdaPromise;
      return Promise.resolve([]);
    });

    const engine = createEngine();

    // 1. Trigger load for NVDA (delayed)
    const p1 = engine.loadDrawings('NVDA');

    // 2. Quickly switch to AAPL
    const p2 = engine.loadDrawings('AAPL');
    await p2;

    expect(engine.getCurrentSymbol()).toBe('AAPL');

    // 3. Stale NVDA response finally arrives
    resolveNvda([
      {
        id: 'stale_nvda_drawing',
        symbol: 'NVDA',
        pane_id: 'candle',
        tool_type: 'risk_reward',
        timeframe_scope: 'ALL',
        data: sampleState
      }
    ]);
    await p1;

    // Stale NVDA drawings must NOT be attached to AAPL
    expect(engine.getActiveCount()).toBe(0);
  });

  it('aborts debounced save if symbol was switched before timer expired', async () => {
    vi.useFakeTimers();
    const engine = createEngine(100);
    await engine.loadDrawings('NVDA');

    // Queue save on NVDA
    engine.queueDebouncedSave({ ...sampleState, entryPrice: 105 });

    // Switch to TSLA before timer completes
    await engine.loadDrawings('TSLA');

    // Advance timer
    vi.advanceTimersByTime(150);

    // Should NOT have persisted NVDA edit to TSLA
    expect(apiSaveFn).not.toHaveBeenCalled();

    vi.useRealTimers();
  });

  it('tracks revision bumps returned from server', async () => {
    apiSaveFn.mockResolvedValueOnce({
      status: 'ok',
      drawing: { id: 'rr_1', revision: 5 }
    });

    const engine = createEngine();
    await engine.loadDrawings('NVDA');
    engine.addRiskReward(sampleState);

    // Wait for promise tick
    await new Promise((r) => setTimeout(r, 10));

    expect(engine.getRevision('rr_1')).toBe(5);
  });
});
