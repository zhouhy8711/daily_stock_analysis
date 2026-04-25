import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Activity, BarChart3, LineChart, X } from 'lucide-react';
import { stocksApi, type KLineData, type StockQuote } from '../../api/stocks';
import { Badge, Button, EmptyState, InlineAlert } from '../common';
import { DashboardStateBlock } from '../dashboard';

type IndicatorAnalysisModalProps = {
  stockCode: string;
  stockName: string;
  reportCurrentPrice?: number;
  reportChangePct?: number;
  onClose: () => void;
};

type ChartPoint = KLineData & {
  ma5?: number;
  ma10?: number;
  ma20?: number;
  volumeMa5?: number;
};

type HistoryState = {
  stockCode: string;
  history: KLineData[];
  quote: StockQuote | null;
  isLoading: boolean;
  error: string | null;
};

const EMPTY_HISTORY: KLineData[] = [];

function formatNumber(value?: number | null, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  return value.toFixed(digits);
}

function formatCompactNumber(value?: number | null): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  if (Math.abs(value) >= 100000000) {
    return `${(value / 100000000).toFixed(2)}亿`;
  }
  if (Math.abs(value) >= 10000) {
    return `${(value / 10000).toFixed(2)}万`;
  }
  return value.toFixed(0);
}

function formatPct(value?: number | null): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return '--';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function movingAverage(data: KLineData[], index: number, period: number, pick: (item: KLineData) => number | null | undefined): number | undefined {
  if (index + 1 < period) {
    return undefined;
  }
  const slice = data.slice(index + 1 - period, index + 1);
  const values = slice
    .map(pick)
    .filter((value): value is number => typeof value === 'number' && !Number.isNaN(value));
  if (values.length !== period) {
    return undefined;
  }
  return values.reduce((sum, value) => sum + value, 0) / period;
}

function buildChartPoints(data: KLineData[]): ChartPoint[] {
  return data.map((item, index) => ({
    ...item,
    ma5: movingAverage(data, index, 5, (point) => point.close),
    ma10: movingAverage(data, index, 10, (point) => point.close),
    ma20: movingAverage(data, index, 20, (point) => point.close),
    volumeMa5: movingAverage(data, index, 5, (point) => point.volume ?? undefined),
  }));
}

function getRecentReturn(points: ChartPoint[], period: number): number | undefined {
  if (points.length <= period) {
    return undefined;
  }
  const latest = points[points.length - 1];
  const previous = points[points.length - 1 - period];
  if (!previous?.close) {
    return undefined;
  }
  return ((latest.close - previous.close) / previous.close) * 100;
}

function getTrendLabel(latest?: ChartPoint): string {
  if (!latest?.ma5 || !latest.ma10 || !latest.ma20) {
    return '数据不足';
  }
  if (latest.close > latest.ma5 && latest.ma5 > latest.ma10 && latest.ma10 > latest.ma20) {
    return '多头排列';
  }
  if (latest.close < latest.ma5 && latest.ma5 < latest.ma10 && latest.ma10 < latest.ma20) {
    return '空头排列';
  }
  if (latest.close > latest.ma20) {
    return '震荡偏强';
  }
  if (latest.close < latest.ma20) {
    return '震荡偏弱';
  }
  return '横盘震荡';
}

function buildPath(
  points: ChartPoint[],
  width: number,
  top: number,
  height: number,
  minPrice: number,
  maxPrice: number,
  pick: (point: ChartPoint) => number | undefined,
): string {
  const values = points
    .map((point, index) => ({ value: pick(point), index }))
    .filter((item): item is { value: number; index: number } => typeof item.value === 'number' && !Number.isNaN(item.value));
  if (values.length === 0) {
    return '';
  }
  const step = width / Math.max(points.length - 1, 1);
  const range = Math.max(maxPrice - minPrice, 0.01);
  return values.map(({ value, index }, pathIndex) => {
    const x = index * step;
    const y = top + ((maxPrice - value) / range) * height;
    return `${pathIndex === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
}

const ChartLegend: React.FC = () => (
  <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-text">
    <span className="inline-flex items-center gap-1"><i className="h-2 w-4 rounded bg-success" />阳线</span>
    <span className="inline-flex items-center gap-1"><i className="h-2 w-4 rounded bg-danger" />阴线</span>
    <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5 bg-amber-400" />MA5</span>
    <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5 bg-cyan" />MA10</span>
    <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5 bg-purple" />MA20</span>
  </div>
);

const CandlestickChart: React.FC<{ points: ChartPoint[] }> = ({ points }) => {
  const width = 960;
  const priceTop = 24;
  const priceHeight = 310;
  const volumeTop = 362;
  const volumeHeight = 110;
  const visible = points.slice(-80);
  const priceValues = visible.flatMap((point) => [point.high, point.low, point.ma5, point.ma10, point.ma20])
    .filter((value): value is number => typeof value === 'number' && !Number.isNaN(value));
  const maxPrice = Math.max(...priceValues);
  const minPrice = Math.min(...priceValues);
  const pricePadding = Math.max((maxPrice - minPrice) * 0.08, 0.01);
  const chartMax = maxPrice + pricePadding;
  const chartMin = Math.max(0, minPrice - pricePadding);
  const maxVolume = Math.max(...visible.map((point) => point.volume ?? 0), 1);
  const step = width / Math.max(visible.length - 1, 1);
  const bodyWidth = Math.max(3, Math.min(9, step * 0.55));
  const priceRange = Math.max(chartMax - chartMin, 0.01);

  const yForPrice = (value: number) => priceTop + ((chartMax - value) / priceRange) * priceHeight;
  const yForVolume = (value?: number | null) => volumeTop + volumeHeight - ((value ?? 0) / maxVolume) * volumeHeight;

  const ma5Path = buildPath(visible, width, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma5);
  const ma10Path = buildPath(visible, width, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma10);
  const ma20Path = buildPath(visible, width, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma20);

  return (
    <div className="rounded-xl border border-subtle bg-surface/75 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <ChartLegend />
        <span className="text-[11px] text-muted-text">最近 {visible.length} 个交易日</span>
      </div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} 500`}
          role="img"
          aria-label="K线图"
          className="h-[24rem] min-w-[54rem] w-full rounded-lg bg-background/60"
        >
          {[0, 1, 2, 3, 4].map((line) => {
            const y = priceTop + (priceHeight / 4) * line;
            const value = chartMax - ((chartMax - chartMin) / 4) * line;
            return (
              <g key={`grid-${line}`}>
                <line x1="0" y1={y} x2={width} y2={y} stroke="currentColor" className="text-border/60" strokeWidth="1" />
                <text x="6" y={y - 5} className="fill-muted-text text-[10px]">{formatNumber(value, 2)}</text>
              </g>
            );
          })}

          {visible.map((point, index) => {
            const x = index * step;
            const isUp = point.close >= point.open;
            const colorClass = isUp ? 'text-success' : 'text-danger';
            const highY = yForPrice(point.high);
            const lowY = yForPrice(point.low);
            const openY = yForPrice(point.open);
            const closeY = yForPrice(point.close);
            const bodyTop = Math.min(openY, closeY);
            const bodyHeight = Math.max(Math.abs(closeY - openY), 1);
            const volumeY = yForVolume(point.volume);
            const volumeH = volumeTop + volumeHeight - volumeY;
            return (
              <g key={`${point.date}-${index}`} className={colorClass}>
                <line x1={x} y1={highY} x2={x} y2={lowY} stroke="currentColor" strokeWidth="1.5" />
                <rect
                  x={x - bodyWidth / 2}
                  y={bodyTop}
                  width={bodyWidth}
                  height={bodyHeight}
                  fill={isUp ? 'transparent' : 'currentColor'}
                  stroke="currentColor"
                  strokeWidth="1.4"
                />
                <rect
                  x={x - bodyWidth / 2}
                  y={volumeY}
                  width={bodyWidth}
                  height={Math.max(volumeH, 1)}
                  fill="currentColor"
                  opacity="0.42"
                />
                {index % 12 === 0 || index === visible.length - 1 ? (
                  <text x={x} y="492" textAnchor="middle" className="fill-muted-text text-[10px]">
                    {point.date.slice(5)}
                  </text>
                ) : null}
              </g>
            );
          })}

          <path d={ma5Path} fill="none" stroke="#f59e0b" strokeWidth="2" />
          <path d={ma10Path} fill="none" stroke="#22d3ee" strokeWidth="2" />
          <path d={ma20Path} fill="none" stroke="#a855f7" strokeWidth="2" />
          <line x1="0" y1={volumeTop} x2={width} y2={volumeTop} stroke="currentColor" className="text-border/70" />
          <text x="6" y={volumeTop + 16} className="fill-muted-text text-[10px]">成交量</text>
        </svg>
      </div>
    </div>
  );
};

const StatTile: React.FC<{ label: string; value: React.ReactNode; tone?: 'success' | 'danger' | 'neutral' }> = ({
  label,
  value,
  tone = 'neutral',
}) => {
  const toneClass = tone === 'success' ? 'text-success' : tone === 'danger' ? 'text-danger' : 'text-foreground';
  return (
    <div className="rounded-xl border border-subtle bg-surface/70 px-3 py-2">
      <div className="text-[11px] text-muted-text">{label}</div>
      <div className={`mt-1 text-base font-semibold tabular-nums ${toneClass}`}>{value}</div>
    </div>
  );
};

export const IndicatorAnalysisModal: React.FC<IndicatorAnalysisModalProps> = ({
  stockCode,
  stockName,
  reportCurrentPrice,
  reportChangePct,
  onClose,
}) => {
  const [historyState, setHistoryState] = useState<HistoryState>({
    stockCode,
    history: [],
    quote: null,
    isLoading: true,
    error: null,
  });

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    let ignore = false;
    Promise.allSettled([
      stocksApi.getHistory(stockCode, 120),
      stocksApi.getQuote(stockCode),
    ])
      .then(([historyResult, quoteResult]) => {
        if (!ignore) {
          const historyResponse = historyResult.status === 'fulfilled' ? historyResult.value : null;
          const quoteResponse = quoteResult.status === 'fulfilled' ? quoteResult.value : null;
          setHistoryState({
            stockCode,
            history: historyResponse?.data ?? [],
            quote: quoteResponse,
            isLoading: false,
            error: historyResult.status === 'rejected'
              ? historyResult.reason instanceof Error
                ? historyResult.reason.message
                : '指标数据加载失败'
              : null,
          });
        }
      })
      .catch((err: unknown) => {
        if (!ignore) {
          setHistoryState({
            stockCode,
            history: [],
            quote: null,
            isLoading: false,
            error: err instanceof Error ? err.message : '指标数据加载失败',
          });
        }
      });
    return () => {
      ignore = true;
    };
  }, [stockCode]);

  const isLoading = historyState.stockCode !== stockCode || historyState.isLoading;
  const error = historyState.stockCode === stockCode ? historyState.error : null;
  const history = historyState.stockCode === stockCode ? historyState.history : EMPTY_HISTORY;
  const quote = historyState.stockCode === stockCode ? historyState.quote : null;
  const points = useMemo(() => buildChartPoints(history), [history]);
  const latest = points.at(-1);
  const previous = points.at(-2);
  const displayOpen = quote?.open ?? latest?.open;
  const displayHigh = quote?.high ?? latest?.high;
  const displayLow = quote?.low ?? latest?.low;
  const displayClose = quote?.currentPrice ?? latest?.close;
  const displayVolume = quote?.volume ?? latest?.volume;
  const displayAmount = quote?.amount ?? latest?.amount;
  const displayPrevClose = quote?.prevClose ?? previous?.close;
  const latestChangePct = reportChangePct
    ?? quote?.changePercent
    ?? latest?.changePercent
    ?? (latest && previous?.close ? ((latest.close - previous.close) / previous.close) * 100 : undefined);
  const latestPrice = reportCurrentPrice ?? quote?.currentPrice ?? latest?.close;
  const dayAmplitude = displayHigh && displayLow && displayPrevClose
    ? ((displayHigh - displayLow) / displayPrevClose) * 100
    : undefined;
  const volumeRatio = displayVolume && latest?.volumeMa5 ? displayVolume / latest.volumeMa5 : undefined;
  const support = points.length > 0 ? Math.min(...points.slice(-20).map((point) => point.low)) : undefined;
  const resistance = points.length > 0 ? Math.max(...points.slice(-20).map((point) => point.high)) : undefined;
  const return5 = getRecentReturn(points, 5);
  const return20 = getRecentReturn(points, 20);
  const trendLabel = getTrendLabel(latest);
  const maDistance = latest?.ma20 && latest.close
    ? ((latest.close - latest.ma20) / latest.ma20) * 100
    : undefined;

  return (
    <div
      data-testid="indicator-analysis-modal"
      className="fixed inset-0 z-[70] flex items-center justify-center bg-background/75 p-2 backdrop-blur-sm md:p-5"
      role="dialog"
      aria-modal="true"
      aria-label="指标分析"
    >
      <div className="glass-card flex max-h-full w-full max-w-7xl flex-col overflow-hidden shadow-2xl">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-subtle px-4 py-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-foreground">指标分析</h2>
              <Badge variant="info" size="sm">{stockCode}</Badge>
              <span className="truncate text-sm text-secondary-text">{stockName}</span>
            </div>
            <p className="mt-1 text-xs text-muted-text">日 K、成交量、均线、量能与关键价位</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭指标分析浮窗">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
          {isLoading ? (
            <DashboardStateBlock loading title="加载指标数据中..." className="min-h-[24rem]" />
          ) : error ? (
            <InlineAlert variant="danger" title="指标数据加载失败" message={error} />
          ) : points.length === 0 ? (
            <EmptyState
              title="暂无 K 线数据"
              description="当前股票暂未返回历史行情，稍后刷新或更换股票再试。"
              className="min-h-[24rem]"
              icon={<BarChart3 className="h-5 w-5" />}
            />
          ) : (
            <div className="space-y-4">
              <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <StatTile label="最新价" value={formatNumber(latestPrice, 2)} tone={(latestChangePct ?? 0) >= 0 ? 'success' : 'danger'} />
                <StatTile label="涨跌幅" value={formatPct(latestChangePct)} tone={(latestChangePct ?? 0) >= 0 ? 'success' : 'danger'} />
                <StatTile label="开 / 高 / 低 / 现" value={`${formatNumber(displayOpen)} / ${formatNumber(displayHigh)} / ${formatNumber(displayLow)} / ${formatNumber(displayClose)}`} />
                <StatTile label="成交量 / 成交额" value={`${formatCompactNumber(displayVolume)} / ${formatCompactNumber(displayAmount)}`} />
              </section>

              <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <StatTile label="MA5 / MA10 / MA20" value={`${formatNumber(latest?.ma5)} / ${formatNumber(latest?.ma10)} / ${formatNumber(latest?.ma20)}`} />
                <StatTile label="MA20 乖离率" value={formatPct(maDistance)} tone={(maDistance ?? 0) >= 0 ? 'success' : 'danger'} />
                <StatTile label="振幅 / 量比" value={`${formatPct(dayAmplitude)} / ${formatNumber(volumeRatio, 2)}`} />
                <StatTile label="昨收 / 涨跌额" value={`${formatNumber(displayPrevClose)} / ${formatNumber(quote?.change)}`} tone={(latestChangePct ?? 0) >= 0 ? 'success' : 'danger'} />
              </section>

              <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <StatTile label="5日 / 20日收益" value={`${formatPct(return5)} / ${formatPct(return20)}`} tone={(return20 ?? 0) >= 0 ? 'success' : 'danger'} />
                <StatTile label="行情更新时间" value={formatDateTime(quote?.updateTime)} />
                <StatTile label="历史样本" value={`${points.length} 个交易日`} />
                <StatTile label="数据周期" value="日 K" />
              </section>

              <section className="grid gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(18rem,0.8fr)]">
                <CandlestickChart points={points} />
                <div className="space-y-3">
                  <div className="rounded-xl border border-subtle bg-surface/70 p-4">
                    <div className="mb-3 flex items-center gap-2">
                      <Activity className="h-4 w-4 text-primary" />
                      <h3 className="text-sm font-semibold text-foreground">趋势摘要</h3>
                    </div>
                    <div className="space-y-3 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-muted-text">均线结构</span>
                        <Badge variant={trendLabel.includes('强') || trendLabel.includes('多头') ? 'success' : trendLabel.includes('弱') || trendLabel.includes('空头') ? 'danger' : 'warning'}>
                          {trendLabel}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-muted-text">近20日支撑</span>
                        <span className="font-semibold tabular-nums text-foreground">{formatNumber(support)}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-muted-text">近20日压力</span>
                        <span className="font-semibold tabular-nums text-foreground">{formatNumber(resistance)}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-muted-text">样本区间</span>
                        <span className="font-mono text-xs text-secondary-text">
                          {points[0]?.date} - {latest?.date}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-xl border border-subtle bg-surface/70 p-4">
                    <div className="mb-3 flex items-center gap-2">
                      <LineChart className="h-4 w-4 text-primary" />
                      <h3 className="text-sm font-semibold text-foreground">数据说明</h3>
                    </div>
                    <p className="text-sm leading-6 text-secondary-text">
                      当前展示基于历史日 K 计算的技术指标。五档盘口、逐笔成交与实时分时明细尚未接入前端数据源，因此这里优先展示项目已有的 OHLC、成交量、成交额与均线指标。
                    </p>
                  </div>
                </div>
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
