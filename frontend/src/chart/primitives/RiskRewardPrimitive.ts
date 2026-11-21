import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  PrimitiveHoveredItem,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time
} from 'lightweight-charts';
import { calculatePositionMetrics, type PositionSizerMetrics } from '../positionSizer';

// 直接從官方介面推導 CanvasRenderingTarget2D，免去額外宣告 fancy-canvas 依賴
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer['draw']>[0];
type DragHandle = 'entry' | 'stop' | 'target' | 'close' | null;

export interface RiskRewardState {
  id: string;
  startTime: Time;
  endTime: Time;
  entryPrice: number;
  stopPrice: number;
  targetPrice: number;
  accountSize: number;
  riskPercent: number;
  maxAllocationPercent: number;
  adr20Percent: number;
}

interface LayoutCoords {
  x1: number;
  x2: number;
  yEntry: number;
  yStop: number;
  yTarget: number;
  closeBtn: { x: number; y: number; size: number };
}

export interface RiskRewardCallbacks {
  onChange?: (state: RiskRewardState, metrics: PositionSizerMetrics) => void;
  onRemove?: (id: string) => void;
}

export class RiskRewardPrimitive implements ISeriesPrimitive<Time> {
  private _state: RiskRewardState;
  private _callbacks: RiskRewardCallbacks;
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<SeriesType, Time> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _container: HTMLElement | null = null;

  private _coords: LayoutCoords | null = null;
  private _metrics: PositionSizerMetrics;
  private _activeDrag: DragHandle = null;
  private _paneView: RiskRewardPaneView;

  constructor(initialState: RiskRewardState, callbacks: RiskRewardCallbacks = {}) {
    this._state = { ...initialState };
    this._callbacks = callbacks;
    this._metrics = calculatePositionMetrics(this._state);
    this._paneView = new RiskRewardPaneView(this);
  }

  public attached(param: SeriesAttachedParameter<Time>): void {
    this._chart = param.chart;
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
    this._container = this._chart.chartElement();

    this._container.addEventListener('pointerdown', this._onPointerDown);
    window.addEventListener('pointermove', this._onPointerMove);
    window.addEventListener('pointerup', this._onPointerUp);
    window.addEventListener('pointercancel', this._onPointerUp);
    this.updateAllViews();
  }

  public detached(): void {
    if (this._container) {
      this._container.removeEventListener('pointerdown', this._onPointerDown);
    }
    window.removeEventListener('pointermove', this._onPointerMove);
    window.removeEventListener('pointerup', this._onPointerUp);
    window.removeEventListener('pointercancel', this._onPointerUp);
    this._unlockChartInteraction();

    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    this._container = null;
  }

  public updateState(patch: Partial<RiskRewardState>): void {
    this._state = { ...this._state, ...patch };
    this._metrics = calculatePositionMetrics(this._state);
    this.updateAllViews();
    this._requestUpdate?.();
  }

  public updateAllViews(): void {
    if (!this._chart || !this._series) {
      this._coords = null;
      return;
    }
    const ts = this._chart.timeScale();
    let xStart = ts.timeToCoordinate(this._state.startTime);
    let xEnd = ts.timeToCoordinate(this._state.endTime);
    const yEntry = this._series.priceToCoordinate(this._state.entryPrice);
    const yStop = this._series.priceToCoordinate(this._state.stopPrice);
    const yTarget = this._series.priceToCoordinate(this._state.targetPrice);

    if (xStart !== null && xEnd === null) {
      const startLogical = ts.coordinateToLogical(xStart);
      if (startLogical !== null) {
        xEnd = ts.logicalToCoordinate((startLogical + 20) as any);
      }
      if (xEnd === null) {
        xEnd = (xStart + 160) as any;
      }
    } else if (xStart === null && xEnd !== null) {
      const endLogical = ts.coordinateToLogical(xEnd);
      if (endLogical !== null) {
        xStart = ts.logicalToCoordinate((endLogical - 20) as any);
      }
      if (xStart === null) {
        xStart = Math.max(10, xEnd - 160) as any;
      }
    } else if (xStart === null && xEnd === null) {
      const width = ts.width();
      xStart = Math.max(10, width - 260) as any;
      xEnd = Math.max((xStart ?? 0) + 120, width - 60) as any;
    }

    if (xStart === null || xEnd === null || yEntry === null || yStop === null || yTarget === null) {
      this._coords = null;
      return;
    }

    const x1 = Math.min(xStart, xEnd);
    const x2 = Math.max(xStart, xEnd);
    const topY = Math.min(yTarget, yEntry, yStop);
    const btnSize = 18;

    this._coords = {
      x1,
      x2: Math.max(x2, x1 + 120),
      yEntry,
      yStop,
      yTarget,
      closeBtn: { x: Math.max(x2, x1 + 120) - btnSize - 4, y: topY + 4, size: btnSize }
    };
  }

  public paneViews(): readonly IPrimitivePaneView[] {
    return [this._paneView];
  }

  public hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const handle = this._detectHandle(x, y);
    if (!handle) return null;
    return {
      externalId: `${this._state.id}:${handle}`,
      zOrder: 'top',
      cursorStyle: handle === 'close' ? 'pointer' : 'ns-resize'
    };
  }

  public getRenderSnapshot(): { coords: LayoutCoords | null; metrics: PositionSizerMetrics; state: RiskRewardState } {
    return { coords: this._coords, metrics: this._metrics, state: this._state };
  }

  private _detectHandle(x: number, y: number): DragHandle {
    const c = this._coords;
    if (!c) return null;

    // 1. 優先判斷專屬關閉按鈕 (Seam 5 防遮蔽契約)
    const b = c.closeBtn;
    if (x >= b.x && x <= b.x + b.size && y >= b.y && y <= b.y + b.size) {
      return 'close';
    }
    if (x < c.x1 - 6 || x > c.x2 + 6) return null;

    // 2. 檢查三條水平控制線 (±6 CSS px 容差)
    const tol = 6;
    if (Math.abs(y - c.yTarget) <= tol) return 'target';
    if (Math.abs(y - c.yEntry) <= tol) return 'entry';
    if (Math.abs(y - c.yStop) <= tol) return 'stop';
    return null;
  }

  private _getLocalPoint(e: PointerEvent): { x: number; y: number } | null {
    if (!this._container) return null;
    const rect = this._container.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  private _onPointerDown = (e: PointerEvent): void => {
    if (e.button !== 0) return;
    const pt = this._getLocalPoint(e);
    if (!pt) return;

    const handle = this._detectHandle(pt.x, pt.y);
    if (!handle) return;

    e.stopPropagation();
    if (handle === 'close') {
      this._series?.detachPrimitive(this);
      this._callbacks.onRemove?.(this._state.id);
      return;
    }

    this._activeDrag = handle;
    // 關鍵：拖曳控制線期間鎖定圖表捲動與縮放
    this._chart?.applyOptions({ handleScroll: false, handleScale: false });
  };

  private _onPointerMove = (e: PointerEvent): void => {
    if (!this._activeDrag || !this._series) return;
    const pt = this._getLocalPoint(e);
    if (!pt) return;

    const nextPrice = this._series.coordinateToPrice(pt.y);
    if (nextPrice === null || !Number.isFinite(nextPrice) || nextPrice <= 0) return;

    if (this._activeDrag === 'entry') this._state.entryPrice = nextPrice;
    else if (this._activeDrag === 'stop') this._state.stopPrice = nextPrice;
    else if (this._activeDrag === 'target') this._state.targetPrice = nextPrice;

    this._metrics = calculatePositionMetrics(this._state);
    this.updateAllViews();
    this._requestUpdate?.();
  };

  private _onPointerUp = (): void => {
    if (!this._activeDrag) return;
    this._activeDrag = null;
    this._unlockChartInteraction();
    this._callbacks.onChange?.({ ...this._state }, this._metrics);
  };

  private _unlockChartInteraction(): void {
    this._chart?.applyOptions({ handleScroll: true, handleScale: true });
  }
}

class RiskRewardPaneView implements IPrimitivePaneView {
  private _renderer: RiskRewardRenderer;

  constructor(source: RiskRewardPrimitive) {
    this._renderer = new RiskRewardRenderer(source);
  }

  public zOrder(): PrimitivePaneViewZOrder {
    return 'top';
  }

  public renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }
}

class RiskRewardRenderer implements IPrimitivePaneRenderer {
  private _source: RiskRewardPrimitive;

  constructor(source: RiskRewardPrimitive) {
    this._source = source;
  }

  public draw(target: CanvasRenderingTarget2D): void {
    const { coords: c, metrics: m } = this._source.getRenderSnapshot();
    if (!c) return;

    // 1. 物理像素空間 (Bitmap)：繪製無模糊銳利矩形與線條
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hpr, verticalPixelRatio: vpr }) => {
      ctx.save();
      const x1 = Math.round(c.x1 * hpr);
      const x2 = Math.round(c.x2 * hpr);
      const w = x2 - x1;
      const yEntry = Math.round(c.yEntry * vpr);
      const yStop = Math.round(c.yStop * vpr);
      const yTarget = Math.round(c.yTarget * vpr);

      // 獲利區（綠）與風險區（紅）
      ctx.fillStyle = 'rgba(34, 197, 94, 0.16)';
      ctx.fillRect(x1, Math.min(yTarget, yEntry), w, Math.abs(yEntry - yTarget));

      ctx.fillStyle = 'rgba(239, 68, 68, 0.18)';
      ctx.fillRect(x1, Math.min(yEntry, yStop), w, Math.abs(yStop - yEntry));

      // 三條控制線 (Target / Entry / Stop)
      const drawLine = (y: number, color: string) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = Math.max(1, Math.round(1.5 * vpr));
        ctx.beginPath();
        ctx.moveTo(x1, y + 0.5);
        ctx.lineTo(x2, y + 0.5);
        ctx.stroke();
      };
      drawLine(yTarget, '#22c55e');
      drawLine(yEntry, '#94a3b8');
      drawLine(yStop, '#ef4444');

      // 專屬關閉按鈕底框
      const bx = Math.round(c.closeBtn.x * hpr);
      const by = Math.round(c.closeBtn.y * vpr);
      const bw = Math.round(c.closeBtn.size * hpr);
      const bh = Math.round(c.closeBtn.size * vpr);
      ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
      ctx.fillRect(bx, by, bw, bh);
      ctx.strokeStyle = '#475569';
      ctx.strokeRect(bx + 0.5, by + 0.5, bw, bh);

      ctx.restore();
    });

    // 2. CSS 像素空間 (Media)：繪製清晰文字 HUD 與「×」圖示
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      ctx.save();
      ctx.font = '600 11px -apple-system, BlinkMacSystemFont, monospace';
      ctx.textBaseline = 'middle';

      // 關閉按鈕文字
      ctx.fillStyle = '#e2e8f0';
      ctx.textAlign = 'center';
      ctx.fillText('×', c.closeBtn.x + c.closeBtn.size / 2, c.closeBtn.y + c.closeBtn.size / 2 + 0.5);

      // 倉位與 ADR 風險 HUD 條
      ctx.textAlign = 'left';
      const adrTag =
        m.adrMultiplier === null ? '' :
        m.adrStatus === 'tight' ? ` · ⚠ ${m.adrMultiplier.toFixed(2)}×ADR(過窄)` :
        m.adrStatus === 'wide'  ? ` · 🔴 ${m.adrMultiplier.toFixed(2)}×ADR(過寬)` :
                                  ` · ${m.adrMultiplier.toFixed(2)}×ADR`;

      const capTag = m.cappedByAllocation ? ' [CAP]' : '';
      const label = m.valid
        ? `${m.side} | R:R ${m.rrRatio.toFixed(2)} | ${m.shares} 股 (${m.allocationPercent.toFixed(1)}%${capTag})${adrTag}`
        : '無效價位（Entry = Stop）';

      const hudColor =
        m.adrStatus === 'wide' ? '#f87171' :
        m.adrStatus === 'tight' ? '#facc15' : '#f8fafc';

      ctx.fillStyle = 'rgba(15, 23, 42, 0.88)';
      const textWidth = ctx.measureText(label).width;
      ctx.fillRect(c.x1 + 6, c.yEntry - 10, textWidth + 12, 20);

      ctx.fillStyle = hudColor;
      ctx.fillText(label, c.x1 + 12, c.yEntry);
      ctx.restore();
    });
  }
}
