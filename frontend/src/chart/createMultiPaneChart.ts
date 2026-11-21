import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  CrosshairMode,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time
} from 'lightweight-charts';

export interface MultiPaneChartBundle {
  chart: IChartApi;
  candleSeries: ISeriesApi<'Candlestick', Time>;
  volumeSeries: ISeriesApi<'Histogram', Time>;
  rsSeries: ISeriesApi<'Line', Time>;
  markersPlugin: ISeriesMarkersPluginApi<Time>;
  dispose: () => void;
}

export function createMultiPaneChart(container: HTMLElement): MultiPaneChartBundle {
  const chart = createChart(container, {
    autoSize: true,
    layout: {
      textColor: '#94a3b8',
      background: { type: ColorType.Solid, color: '#0b0e14' },
      attributionLogo: true,
      panes: {
        separatorColor: '#1e293b',
        separatorHoverColor: 'rgba(56, 189, 248, 0.3)',
        enableResize: true
      }
    },
    grid: {
      vertLines: { color: '#1e293b' },
      horzLines: { color: '#1e293b' }
    },
    crosshair: { mode: CrosshairMode.Normal },
    timeScale: { borderColor: '#1e293b', timeVisible: true }
  });

  // Pane 0: 主圖 K 線（paneIndex 為第 3 參數）
  const candleSeries = chart.addSeries(CandlestickSeries, {
    upColor: '#22c55e',
    downColor: '#ef4444',
    borderVisible: false,
    wickUpColor: '#22c55e',
    wickDownColor: '#ef4444'
  }, 0);

  // Pane 1: 成交量副圖（獨立窗格使用預設右側座標軸）
  const volumeSeries = chart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' },
    lastValueVisible: false,
    priceLineVisible: false
  }, 1);

  // Pane 2: RS 相對強度副圖
  const rsSeries = chart.addSeries(LineSeries, {
    color: '#38bdf8',
    lineWidth: 2,
    title: 'RS vs SPY',
    priceLineVisible: false
  }, 2);

  // 透過 IPaneApi 設定相對伸展權重（65% / 15% / 20%）
  const panes = chart.panes();
  panes[0]?.setStretchFactor(65);
  panes[1]?.setStretchFactor(15);
  panes[2]?.setStretchFactor(20);

  // v5 標記改用 createSeriesMarkers（用於 RS 新高藍點與財報標記）
  const markersPlugin = createSeriesMarkers(candleSeries, []);

  return {
    chart,
    candleSeries,
    volumeSeries,
    rsSeries,
    markersPlugin,
    dispose: () => {
      markersPlugin.detach();
      chart.remove();
    }
  };
}
