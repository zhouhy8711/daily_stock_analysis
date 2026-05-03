import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  ArrowLeft,
  BarChart3,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Layers3,
  LineChart,
  Radio,
  Users,
  X,
} from 'lucide-react';
import {
  stocksApi,
  type ChipDistributionMetrics,
  type KLineData,
  type KLinePeriod,
  type MajorHolder,
  type StockIndicatorMetrics,
  type StockQuote,
} from '../../api/stocks';
import { Badge, Button, EmptyState, InlineAlert } from '../common';
import { DashboardStateBlock } from '../dashboard';

type IndicatorAnalysisModalProps = {
  stockCode: string;
  stockName: string;
  reportCurrentPrice?: number;
  reportChangePct?: number;
  onClose: () => void;
};

type IndicatorAnalysisViewProps = {
  stockCode: string;
  stockName: string;
  reportCurrentPrice?: number;
  reportChangePct?: number;
  onClose?: () => void;
  variant?: 'page' | 'modal';
};

type ChartPoint = KLineData & {
  ma5?: number;
  ma10?: number;
  ma20?: number;
  ma30?: number;
  ma60?: number;
  volumeMa5?: number;
  volumeMa10?: number;
  amountMa5?: number;
  amountMa10?: number;
  ema12?: number;
  ema26?: number;
  dif?: number;
  dea?: number;
  macd?: number;
  rsi6?: number;
  rsi12?: number;
};

type HistoryState = {
  stockCode: string;
  period: KLinePeriod;
  history: KLineData[];
  quote: StockQuote | null;
  metrics: StockIndicatorMetrics | null;
  metricsError: string | null;
  isLoading: boolean;
  error: string | null;
};

type HistoryCache = Partial<Record<KLinePeriod, HistoryState>>;

type StructureTrendPoint = {
  date: string;
  close: number;
  avgCost?: number;
  profitRatio?: number;
  concentration90?: number;
  forceScore: number;
  force5?: number;
  force20?: number;
};

type MainForceVariant = 'success' | 'danger' | 'warning';

type MainForceSignal = {
  label: string;
  variant: MainForceVariant;
  summary: string;
  source: string;
  force5?: number;
  force20?: number;
  buyCount: number;
  sellCount: number;
};

type HolderOption = {
  key: string;
  label: string;
  holders: MajorHolder[];
};

type HolderScopeProfile = {
  isAll: boolean;
  intensity: number;
  bias: number;
  costBias: number;
  momentumWeight: number;
  ratio?: number;
};

type OrderFlowMetric = {
  label: string;
  value?: number;
  ratio: number;
  tone: MainForceVariant | 'neutral';
};

type ChartMenuState = {
  x: number;
  y: number;
};

type TooltipPlacementKey = 'price' | 'volume' | 'momentum';

type TooltipPosition = {
  x: number;
  y: number;
};

type TooltipPositions = Partial<Record<TooltipPlacementKey, TooltipPosition>>;

type HoverStepDirection = -1 | 1;

type VolumeIndicatorMode = 'volume' | 'amount' | 'volumeMa';
type MomentumIndicatorMode = 'macd' | 'rsi';
type ChipPanelScope = 'all' | 'main';
type ChipRangeLevel = '90' | '70';
type SidePanelTab = 'chip' | 'flow';

const EMPTY_HISTORY: KLineData[] = [];
const EMPTY_HOLDERS: MajorHolder[] = [];
const ALL_HOLDERS_KEY = 'all';
const KLINE_PERIOD_OPTIONS: Array<{ value: KLinePeriod; label: string; days: number }> = [
  { value: 'daily', label: '日K', days: 120 },
  { value: '1m', label: '1分', days: 3 },
  { value: '5m', label: '5分', days: 3 },
  { value: '15m', label: '15分', days: 3 },
  { value: '30m', label: '30分', days: 3 },
  { value: '60m', label: '60分', days: 3 },
];
const INTRADAY_PERIOD_OPTIONS = KLINE_PERIOD_OPTIONS.filter((option) => option.value !== 'daily');
const MAX_VISIBLE_KLINE_POINTS = 80;
const MIN_VISIBLE_KLINE_POINTS = 24;
const MAX_EXPANDED_KLINE_POINTS = 160;
const ONE_MINUTE_REFRESH_MS = 10_000;
const LINE_COLORS = {
  close: 'var(--indicator-line-close)',
  avgCost: 'var(--indicator-line-avg-cost)',
  force: 'var(--indicator-line-force)',
  profit: 'var(--indicator-line-profit)',
  ma5: 'var(--indicator-line-ma5)',
  ma10: 'var(--indicator-line-ma10)',
  ma20: 'var(--indicator-line-ma20)',
  ma30: 'var(--indicator-line-ma30)',
  ma60: 'var(--indicator-line-ma60)',
};
const TERMINAL_COLORS = {
  bg: 'var(--indicator-chart-bg)',
  panel: 'var(--indicator-chart-panel)',
  panel2: 'var(--indicator-chart-panel-strong)',
  redGrid: 'var(--indicator-chart-grid)',
  redGridSoft: 'var(--indicator-chart-grid-soft)',
  axis: 'var(--indicator-chart-axis)',
  axisText: 'var(--indicator-chart-axis-text)',
  text: 'var(--indicator-chart-text)',
  muted: 'var(--indicator-chart-muted)',
  cyan: 'var(--indicator-chart-cyan)',
  orange: 'var(--indicator-chart-orange)',
  green: 'var(--indicator-chart-green)',
  blue: 'var(--indicator-chart-blue)',
  yellow: 'var(--indicator-chart-yellow)',
  purple: 'var(--indicator-chart-purple)',
  white: 'var(--indicator-chart-contrast)',
  selectedText: 'var(--indicator-chart-selected-text)',
  activeTabBg: 'var(--indicator-chart-active-tab-bg)',
  priceTagBg: 'var(--indicator-chart-price-tag-bg)',
  priceTagText: 'var(--indicator-chart-price-tag-text)',
  tooltipBg: 'var(--indicator-chart-tooltip-bg)',
  tooltipBorder: 'var(--indicator-chart-tooltip-border)',
  tooltipText: 'var(--indicator-chart-tooltip-text)',
  tooltipMuted: 'var(--indicator-chart-tooltip-muted)',
  chipBlue: 'var(--indicator-chart-chip-blue)',
  positiveStroke: 'var(--indicator-chart-positive-stroke)',
  negativeStroke: 'var(--indicator-chart-negative-stroke)',
  shadow: 'var(--indicator-chart-shadow)',
};
const BASE_HOLDER_PROFILE: HolderScopeProfile = {
  isAll: true,
  intensity: 1,
  bias: 0,
  costBias: 0,
  momentumWeight: 0,
};

function getPeriodMeta(period: KLinePeriod) {
  const option = KLINE_PERIOD_OPTIONS.find((item) => item.value === period) ?? KLINE_PERIOD_OPTIONS[0];
  const isDaily = option.value === 'daily';
  return {
    ...option,
    isDaily,
    sampleUnit: isDaily ? '个交易日' : '根K线',
    recentLabel: isDaily ? '最近' : '当前窗口',
    supportLabel: isDaily ? '近20日支撑' : '近20根支撑',
    resistanceLabel: isDaily ? '近20日压力' : '近20根压力',
    returnLabel: isDaily ? '5日 / 20日收益' : '5根 / 20根收益',
    description: isDaily
      ? '当前展示基于历史日 K 计算的技术指标，可切换上方分钟周期查看分段 K 线。五档盘口和逐笔成交暂不在此图中展示。'
      : '当前展示分钟 K 线数据，按所选周期分段展示 OHLC、成交量、成交额与均线；可拖动下方时间窗口回看返回区间内的更早分钟数据。五档盘口和逐笔成交暂不在此图中展示。',
  };
}

function getMarketKind(stockCode: string): 'cn' | 'hk' | 'us' {
  const code = stockCode.trim().toUpperCase();
  if (code.endsWith('.HK') || code.startsWith('HK') || /^\d{5}$/.test(code)) {
    return 'hk';
  }
  if (/^[A-Z][A-Z0-9.-]*$/.test(code)) {
    return 'us';
  }
  return 'cn';
}

function getTimeMeta(stockCode: string) {
  const market = getMarketKind(stockCode);
  if (market === 'us') {
    return {
      klineLabel: '美东 ET',
      quoteLabel: '北京时间',
    };
  }
  if (market === 'hk') {
    return {
      klineLabel: '港股时间',
      quoteLabel: '北京时间',
    };
  }
  return {
    klineLabel: '北京时间',
    quoteLabel: '北京时间',
  };
}

function createHistoryState(
  stockCode: string,
  period: KLinePeriod,
  overrides: Partial<HistoryState> = {},
): HistoryState {
  return {
    stockCode,
    period,
    history: [],
    quote: null,
    metrics: null,
    metricsError: null,
    isLoading: true,
    error: null,
    ...overrides,
  };
}

function getKLineDateOnly(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  const match = value.match(/^\d{4}-\d{2}-\d{2}/);
  return match?.[0] ?? null;
}

function getLatestHistoryDate(history: KLineData[]): string | null {
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const date = getKLineDateOnly(history[index]?.date);
    if (date) {
      return date;
    }
  }
  return null;
}

function normalizeHistoryForPeriod(
  history: KLineData[],
  period: KLinePeriod,
  market: 'cn' | 'hk' | 'us',
  dailyCutoffDate?: string | null,
): KLineData[] {
  if (period === 'daily' || market === 'us' || !dailyCutoffDate) {
    return history;
  }

  const uniqueDates = Array.from(new Set(
    history.map((item) => getKLineDateOnly(item.date)).filter((date): date is string => Boolean(date)),
  ));
  if (uniqueDates.length === 1 && uniqueDates[0] && uniqueDates[0] > dailyCutoffDate) {
    const staleDate = uniqueDates[0];
    return history.map((item) => ({
      ...item,
      date: item.date.replace(staleDate, dailyCutoffDate),
    }));
  }

  return history.filter((item) => {
    const itemDate = getKLineDateOnly(item.date);
    return !itemDate || itemDate <= dailyCutoffDate;
  });
}

function formatAxisDate(value: string, period: KLinePeriod): string {
  if (period === 'daily') {
    return value.slice(5);
  }
  return value.includes(' ') ? value.slice(5) : value;
}

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

function formatPlainPct(value?: number | null, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  return `${value.toFixed(digits)}%`;
}

function formatRatioPct(value?: number | null, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  const pctValue = Math.abs(value) <= 1 ? value * 100 : value;
  return `${pctValue.toFixed(digits)}%`;
}

function formatSignedRatioPct(value?: number | null, digits = 1): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`;
}

function formatSignedNumber(value?: number | null, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
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

function relativeStrengthIndex(data: KLineData[], index: number, period: number): number | undefined {
  if (index < period) {
    return undefined;
  }

  let gains = 0;
  let losses = 0;
  for (let offset = index - period + 1; offset <= index; offset += 1) {
    const previous = data[offset - 1];
    const current = data[offset];
    if (!previous || !current) {
      return undefined;
    }
    const change = current.close - previous.close;
    if (change >= 0) {
      gains += change;
    } else {
      losses += Math.abs(change);
    }
  }

  if (gains === 0 && losses === 0) {
    return 50;
  }
  if (losses === 0) {
    return 100;
  }
  const rs = gains / losses;
  return 100 - (100 / (1 + rs));
}

function buildChartPoints(data: KLineData[]): ChartPoint[] {
  let ema12: number | undefined;
  let ema26: number | undefined;
  let dea = 0;

  return data.map((item, index) => {
    const close = item.close;
    ema12 = typeof ema12 === 'number' ? (close * (2 / 13)) + (ema12 * (11 / 13)) : close;
    ema26 = typeof ema26 === 'number' ? (close * (2 / 27)) + (ema26 * (25 / 27)) : close;
    const dif = ema12 - ema26;
    dea = index === 0 ? dif : (dif * (2 / 10)) + (dea * (8 / 10));

    return {
      ...item,
      ma5: movingAverage(data, index, 5, (point) => point.close),
      ma10: movingAverage(data, index, 10, (point) => point.close),
      ma20: movingAverage(data, index, 20, (point) => point.close),
      ma30: movingAverage(data, index, 30, (point) => point.close),
      ma60: movingAverage(data, index, 60, (point) => point.close),
      volumeMa5: movingAverage(data, index, 5, (point) => point.volume ?? undefined),
      volumeMa10: movingAverage(data, index, 10, (point) => point.volume ?? undefined),
      amountMa5: movingAverage(data, index, 5, (point) => point.amount ?? undefined),
      amountMa10: movingAverage(data, index, 10, (point) => point.amount ?? undefined),
      ema12,
      ema26,
      dif,
      dea,
      macd: (dif - dea) * 2,
      rsi6: relativeStrengthIndex(data, index, 6),
      rsi12: relativeStrengthIndex(data, index, 12),
    };
  });
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

function getPointChange(point: ChartPoint, previous?: ChartPoint): number | undefined {
  if (!previous?.close) {
    return undefined;
  }
  return point.close - previous.close;
}

function getPointChangePct(point: ChartPoint, previous?: ChartPoint): number | undefined {
  if (typeof point.changePercent === 'number' && !Number.isNaN(point.changePercent)) {
    return point.changePercent;
  }
  if (!previous?.close) {
    return undefined;
  }
  return ((point.close - previous.close) / previous.close) * 100;
}

function getPointAmplitude(point: ChartPoint, previous?: ChartPoint): number | undefined {
  const base = previous?.close ?? point.open;
  if (!base) {
    return undefined;
  }
  return ((point.high - point.low) / base) * 100;
}

function getPointVolumeRatio(point: ChartPoint): number | undefined {
  if (typeof point.volume !== 'number' || Number.isNaN(point.volume) || !point.volumeMa5) {
    return undefined;
  }
  return point.volume / point.volumeMa5;
}

function formatCostRange(chip?: ChipDistributionMetrics | null): string {
  if (!chip) {
    return '--';
  }
  const low = formatNumber(chip.cost90Low);
  const high = formatNumber(chip.cost90High);
  return low === '--' && high === '--' ? '--' : `${low} - ${high}`;
}

function formatChipRange(chip: ChipDistributionMetrics | null, rangeLevel: ChipRangeLevel): string {
  if (!chip) {
    return '--';
  }
  const low = formatNumber(rangeLevel === '90' ? chip.cost90Low : chip.cost70Low);
  const high = formatNumber(rangeLevel === '90' ? chip.cost90High : chip.cost70High);
  return low === '--' && high === '--' ? '--' : `${low} - ${high}`;
}

function getChipConcentration(chip: ChipDistributionMetrics | null, rangeLevel: ChipRangeLevel): number | null | undefined {
  return rangeLevel === '90' ? chip?.concentration90 : chip?.concentration70;
}

function normalizeDateKey(date?: string | null): string | null {
  return typeof date === 'string' && date.length >= 10 ? date.slice(0, 10) : null;
}

function pickChipSnapshot(chip: ChipDistributionMetrics | null, date?: string | null): ChipDistributionMetrics | null {
  if (!chip) {
    return null;
  }
  const dateKey = normalizeDateKey(date);
  if (!dateKey) {
    return chip;
  }
  const snapshots = (chip.snapshots ?? [])
    .map((snapshot) => ({ snapshot, dateKey: normalizeDateKey(snapshot.date) }))
    .filter((item): item is { snapshot: ChipDistributionMetrics; dateKey: string } => item.dateKey !== null);
  const exact = snapshots.find((item) => item.dateKey === dateKey);
  if (exact) {
    return exact.snapshot;
  }
  if (snapshots.length === 0) {
    return chip;
  }
  const targetTime = Date.parse(dateKey);
  if (!Number.isNaN(targetTime)) {
    return snapshots.reduce((nearest, item) => {
      const nearestDistance = Math.abs(Date.parse(nearest.dateKey) - targetTime);
      const itemDistance = Math.abs(Date.parse(item.dateKey) - targetTime);
      return itemDistance < nearestDistance ? item : nearest;
    }).snapshot;
  }
  return snapshots.find((item) => item.dateKey <= dateKey)?.snapshot ?? snapshots[0]?.snapshot ?? chip;
}

function isChipForDate(chip: ChipDistributionMetrics | null, date?: string | null): boolean {
  const chipDate = normalizeDateKey(chip?.date);
  const pointDate = normalizeDateKey(date);
  return !!chipDate && !!pointDate && chipDate === pointDate;
}

function findPointByDate(points: ChartPoint[], date?: string | null): ChartPoint | undefined {
  const dateKey = normalizeDateKey(date);
  if (!dateKey) {
    return undefined;
  }
  return points.find((point) => normalizeDateKey(point.date) === dateKey);
}

function formatHolderLabel(holder: MajorHolder): string {
  const ratio = formatPlainPct(holder.holdingRatio);
  return ratio === '--' ? holder.name : `${holder.name} ${ratio}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function clampTooltipPosition(
  position: TooltipPosition,
  tooltipWidth: number,
  tooltipHeight: number,
  boundsWidth: number,
  boundsHeight: number,
): TooltipPosition {
  return {
    x: clamp(position.x, 0, Math.max(boundsWidth - tooltipWidth, 0)),
    y: clamp(position.y, 0, Math.max(boundsHeight - tooltipHeight, 0)),
  };
}

function getSvgPointerPosition(svg: SVGSVGElement, event: Pick<PointerEvent, 'clientX' | 'clientY'>): TooltipPosition | null {
  const matrix = svg.getScreenCTM();
  if (matrix && typeof svg.createSVGPoint === 'function') {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const transformed = point.matrixTransform(matrix.inverse());
    return { x: transformed.x, y: transformed.y };
  }

  const rect = svg.getBoundingClientRect();
  const viewBox = svg.viewBox.baseVal;
  if (rect.width <= 0 || rect.height <= 0 || viewBox.width <= 0 || viewBox.height <= 0) {
    return null;
  }
  return {
    x: viewBox.x + ((event.clientX - rect.left) / rect.width) * viewBox.width,
    y: viewBox.y + ((event.clientY - rect.top) / rect.height) * viewBox.height,
  };
}

function startTooltipDrag(
  event: React.PointerEvent<SVGGElement>,
  currentPosition: TooltipPosition,
  onPositionChange: (position: TooltipPosition) => void,
) {
  if (event.button !== 0) {
    return;
  }
  const svg = event.currentTarget.ownerSVGElement;
  if (!svg) {
    return;
  }
  const start = getSvgPointerPosition(svg, event);
  if (!start) {
    return;
  }

  const offsetX = start.x - currentPosition.x;
  const offsetY = start.y - currentPosition.y;
  event.preventDefault();
  event.stopPropagation();
  event.currentTarget.setPointerCapture?.(event.pointerId);

  const handleMove = (moveEvent: PointerEvent) => {
    const next = getSvgPointerPosition(svg, moveEvent);
    if (!next) {
      return;
    }
    onPositionChange({
      x: next.x - offsetX,
      y: next.y - offsetY,
    });
  };

  const stopDragging = () => {
    window.removeEventListener('pointermove', handleMove);
    window.removeEventListener('pointerup', stopDragging);
    window.removeEventListener('pointercancel', stopDragging);
  };

  window.addEventListener('pointermove', handleMove);
  window.addEventListener('pointerup', stopDragging);
  window.addEventListener('pointercancel', stopDragging);
}

function isTooltipPanelTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('[data-indicator-tooltip-panel="true"]') !== null;
}

function getVisiblePointCount(total: number, zoomLevel: number): number {
  const base = Math.min(MAX_VISIBLE_KLINE_POINTS, total);
  const zoomed = Math.round(base / (1.45 ** zoomLevel));
  return clamp(zoomed, Math.min(MIN_VISIBLE_KLINE_POINTS, total), Math.min(MAX_EXPANDED_KLINE_POINTS, total));
}

function getValueToneClass(tone?: MainForceVariant | 'neutral'): string {
  if (tone === 'success') {
    return 'text-success';
  }
  if (tone === 'danger') {
    return 'text-danger';
  }
  if (tone === 'warning') {
    return 'text-warning';
  }
  return 'text-foreground';
}

function getSignedTone(value?: number | null): MainForceVariant | 'neutral' {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'neutral';
  }
  if (value > 0.03) {
    return 'success';
  }
  if (value < -0.03) {
    return 'danger';
  }
  return 'warning';
}

function buildSignedVolumeFlow(points: ChartPoint[]): number[] {
  let cumulative = 0;
  return points.map((point, index) => {
    const previous = points[index - 1];
    const volume = typeof point.volume === 'number' && !Number.isNaN(point.volume) ? point.volume : 0;
    if (previous) {
      const change = point.close - previous.close;
      cumulative += change > 0 ? volume : change < 0 ? -volume : 0;
    }
    return cumulative;
  });
}

function buildCumulativeVolume(points: ChartPoint[]): number[] {
  let cumulative = 0;
  return points.map((point) => {
    cumulative += typeof point.volume === 'number' && !Number.isNaN(point.volume) ? point.volume : 0;
    return cumulative;
  });
}

function getFlowChangeRatio(
  points: ChartPoint[],
  flows: number[],
  index: number,
  period: number,
): number | undefined {
  if (index <= 0 || flows.length === 0) {
    return undefined;
  }
  const startIndex = Math.max(index - period, 0);
  const volumeWindow = points.slice(startIndex + 1, index + 1);
  const volumeSum = volumeWindow.reduce((sum, point) => sum + (point.volume ?? 0), 0);
  if (volumeSum <= 0) {
    return undefined;
  }
  return (flows[index] - flows[startIndex]) / volumeSum;
}

function getHolderRatioSum(holders: MajorHolder[]): number | undefined {
  const values = holders
    .map((holder) => holder.holdingRatio)
    .filter((value): value is number => typeof value === 'number' && !Number.isNaN(value));
  if (values.length === 0) {
    return undefined;
  }
  return values.reduce((sum, value) => sum + value, 0);
}

function getHolderChangeRatio(holder: MajorHolder): number | undefined {
  if (typeof holder.changeRatio === 'number' && !Number.isNaN(holder.changeRatio)) {
    return holder.changeRatio;
  }
  const changeText = holder.change?.trim();
  if (!changeText) {
    return undefined;
  }
  const match = changeText.replace(/,/g, '').match(/-?\d+(\.\d+)?/);
  if (!match) {
    return undefined;
  }
  const value = Number(match[0]);
  return Number.isFinite(value) ? value : undefined;
}

function buildHolderOptions(holders: MajorHolder[]): HolderOption[] {
  return [
    {
      key: ALL_HOLDERS_KEY,
      label: '所有主力合计',
      holders,
    },
    ...holders.map((holder, index) => ({
      key: `holder-${index}`,
      label: formatHolderLabel(holder),
      holders: [holder],
    })),
  ];
}

function buildHolderScopeProfile(scopeHolders: MajorHolder[], allHolders: MajorHolder[]): HolderScopeProfile {
  if (scopeHolders.length === 0 || scopeHolders.length === allHolders.length) {
    return {
      ...BASE_HOLDER_PROFILE,
      ratio: getHolderRatioSum(scopeHolders),
    };
  }

  const scopeRatio = getHolderRatioSum(scopeHolders);
  const allRatio = getHolderRatioSum(allHolders);
  const ratioWeight = scopeRatio && allRatio && allRatio > 0
    ? clamp(scopeRatio / allRatio, 0.18, 1)
    : 0.45;
  const directions = scopeHolders
    .map(getHolderDirection)
    .filter((direction): direction is 1 | -1 | 0 => direction !== undefined);
  const directionAverage = directions.length > 0
    ? directions.reduce<number>((sum, direction) => sum + direction, 0) / directions.length
    : 0;
  const changeRatios = scopeHolders
    .map(getHolderChangeRatio)
    .filter((value): value is number => typeof value === 'number' && !Number.isNaN(value));
  const averageChange = changeRatios.length > 0
    ? changeRatios.reduce((sum, value) => sum + value, 0) / changeRatios.length
    : undefined;
  const bias = typeof averageChange === 'number'
    ? clamp(averageChange / 100, -0.18, 0.18)
    : directionAverage * 0.06;

  return {
    isAll: false,
    intensity: ratioWeight,
    bias,
    costBias: bias * 0.35,
    momentumWeight: 0.18 + ratioWeight * 0.25 + Math.abs(bias) * 0.8,
    ratio: scopeRatio,
  };
}

function buildScopedVolumeFlow(points: ChartPoint[], profile: HolderScopeProfile): number[] {
  const baseFlows = buildSignedVolumeFlow(points);
  if (profile.isAll) {
    return baseFlows;
  }
  const cumulativeVolumes = buildCumulativeVolume(points);
  return baseFlows.map((flow, index) => {
    const startIndex = Math.max(index - 5, 0);
    const baseClose = points[startIndex]?.close;
    const momentum = baseClose ? (points[index].close - baseClose) / baseClose : 0;
    return (flow * profile.intensity) + (cumulativeVolumes[index] * (profile.bias + momentum * profile.momentumWeight));
  });
}

function adjustChipForHolderScope(
  chip: ChipDistributionMetrics | null,
  profile: HolderScopeProfile,
): ChipDistributionMetrics | null {
  if (!chip || profile.isAll) {
    return chip;
  }
  const adjustCost = (value?: number | null) => (
    typeof value === 'number' && !Number.isNaN(value)
      ? value * (1 + profile.costBias)
      : value
  );
  const adjustedProfitRatio = typeof chip.profitRatio === 'number' && !Number.isNaN(chip.profitRatio)
    ? clamp(chip.profitRatio - profile.costBias * 3 + profile.bias * 0.45, 0, 1)
    : chip.profitRatio;

  return {
    ...chip,
    profitRatio: adjustedProfitRatio,
    avgCost: adjustCost(chip.avgCost),
    cost90Low: adjustCost(chip.cost90Low),
    cost90High: adjustCost(chip.cost90High),
    cost70Low: adjustCost(chip.cost70Low),
    cost70High: adjustCost(chip.cost70High),
  };
}

function buildStructureTrend(
  points: ChartPoint[],
  currentChip?: ChipDistributionMetrics | null,
  profile: HolderScopeProfile = BASE_HOLDER_PROFILE,
): StructureTrendPoint[] {
  if (points.length === 0) {
    return [];
  }

  const flows = buildScopedVolumeFlow(points, profile);
  const visibleStartIndex = Math.max(points.length - 80, 0);
  const visibleFlows = flows.slice(visibleStartIndex);
  const minFlow = Math.min(...visibleFlows);
  const maxFlow = Math.max(...visibleFlows);
  const flowRange = Math.max(maxFlow - minFlow, 1);

  return points.slice(visibleStartIndex).map((point, visibleIndex) => {
    const originalIndex = visibleStartIndex + visibleIndex;
    const chip = adjustChipForHolderScope(
      originalIndex === points.length - 1 && currentChip ? currentChip : null,
      profile,
    );
    return {
      date: point.date,
      close: point.close,
      avgCost: chip?.avgCost ?? undefined,
      profitRatio: chip?.profitRatio ?? undefined,
      concentration90: chip?.concentration90 ?? undefined,
      forceScore: ((flows[originalIndex] - minFlow) / flowRange) * 100,
      force5: getFlowChangeRatio(points, flows, originalIndex, 5),
      force20: getFlowChangeRatio(points, flows, originalIndex, 20),
    };
  });
}

function getHolderDirection(holder: MajorHolder): 1 | -1 | 0 | undefined {
  if (typeof holder.changeRatio === 'number' && !Number.isNaN(holder.changeRatio) && holder.changeRatio !== 0) {
    return holder.changeRatio > 0 ? 1 : -1;
  }

  const changeText = holder.change?.trim();
  if (!changeText) {
    return undefined;
  }
  if (/不变|持平/.test(changeText)) {
    return 0;
  }
  if (/增|加|新进|买入|买|进/.test(changeText)) {
    return 1;
  }
  if (/减|少|退出|卖出|卖/.test(changeText)) {
    return -1;
  }
  const match = changeText.replace(/,/g, '').match(/-?\d+(\.\d+)?/);
  if (!match) {
    return undefined;
  }
  const value = Number(match[0]);
  if (!Number.isFinite(value) || value === 0) {
    return 0;
  }
  return value > 0 ? 1 : -1;
}

function buildMainForceSignal(
  points: ChartPoint[],
  holders: MajorHolder[],
  profile: HolderScopeProfile = BASE_HOLDER_PROFILE,
): MainForceSignal {
  const holderDirections = holders
    .map(getHolderDirection)
    .filter((direction): direction is 1 | -1 | 0 => direction !== undefined);
  const buyCount = holderDirections.filter((direction) => direction > 0).length;
  const sellCount = holderDirections.filter((direction) => direction < 0).length;
  const holderNet = holderDirections.reduce<number>((sum, direction) => sum + direction, 0);
  const flows = buildScopedVolumeFlow(points, profile);
  const latestIndex = points.length - 1;
  const force5 = getFlowChangeRatio(points, flows, latestIndex, 5);
  const force20 = getFlowChangeRatio(points, flows, latestIndex, 20);

  if (holderDirections.length > 0) {
    if (holderNet > 0) {
      return {
        label: '主力买入增强',
        variant: 'success',
        summary: `${buyCount} 名增持 / ${sellCount} 名减持`,
        source: '持仓变动',
        force5,
        force20,
        buyCount,
        sellCount,
      };
    }
    if (holderNet < 0) {
      return {
        label: '主力卖出减弱',
        variant: 'danger',
        summary: `${buyCount} 名增持 / ${sellCount} 名减持`,
        source: '持仓变动',
        force5,
        force20,
        buyCount,
        sellCount,
      };
    }
    return {
      label: '主力持仓分歧',
      variant: 'warning',
      summary: `${buyCount} 名增持 / ${sellCount} 名减持`,
      source: '持仓变动',
      force5,
      force20,
      buyCount,
      sellCount,
    };
  }

  const shortFlow = force5 ?? 0;
  const mediumFlow = force20 ?? 0;
  if (shortFlow >= 0.08 && mediumFlow >= -0.03) {
    return {
      label: '主力买入增强',
      variant: 'success',
      summary: '近5日价量动能偏流入',
      source: '价量估算',
      force5,
      force20,
      buyCount,
      sellCount,
    };
  }
  if (shortFlow <= -0.08 && mediumFlow <= 0.03) {
    return {
      label: '主力卖出减弱',
      variant: 'danger',
      summary: '近5日价量动能偏流出',
      source: '价量估算',
      force5,
      force20,
      buyCount,
      sellCount,
    };
  }
  if (mediumFlow >= 0.12) {
    return {
      label: '主力中线吸筹',
      variant: 'success',
      summary: '近20日价量动能偏流入',
      source: '价量估算',
      force5,
      force20,
      buyCount,
      sellCount,
    };
  }
  if (mediumFlow <= -0.12) {
    return {
      label: '主力持续流出',
      variant: 'danger',
      summary: '近20日价量动能偏流出',
      source: '价量估算',
      force5,
      force20,
      buyCount,
      sellCount,
    };
  }
  return {
    label: '主力观望',
    variant: 'warning',
    summary: '买卖动能暂未形成一致方向',
    source: '价量估算',
    force5,
    force20,
    buyCount,
    sellCount,
  };
}

function getMetricTone(value?: number | null, deadZone = 0): MainForceVariant | 'neutral' {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'neutral';
  }
  if (value > deadZone) {
    return 'success';
  }
  if (value < -deadZone) {
    return 'danger';
  }
  return 'warning';
}

function getLatestChangePct(points: ChartPoint[], quote: StockQuote | null): number | undefined {
  const latest = points.at(-1);
  const previous = points.at(-2);
  return quote?.changePercent
    ?? latest?.changePercent
    ?? (latest && previous?.close ? ((latest.close - previous.close) / previous.close) * 100 : undefined);
}

function buildOrderFlowMetrics(
  points: ChartPoint[],
  quote: StockQuote | null,
): { rows: OrderFlowMetric[]; sourceLabel: string; updatedAt?: string | null; netTotal?: number } {
  const latest = points.at(-1);
  const amount = quote?.amount
    ?? latest?.amount
    ?? (latest?.close && latest?.volume ? latest.close * latest.volume : undefined);
  const changePct = getLatestChangePct(points, quote) ?? 0;
  const return5 = getRecentReturn(points, 5) ?? 0;
  const volumeRatio = quote?.volumeRatio ?? (
    latest?.volume && latest.volumeMa5 ? latest.volume / latest.volumeMa5 : undefined
  );
  const flowRatio = clamp(
    (changePct / 100) * 0.9
      + (return5 / 100) * 0.32
      + ((volumeRatio ?? 1) - 1) * 0.055,
    -0.26,
    0.26,
  );
  const netTotal = typeof amount === 'number' && Number.isFinite(amount) ? amount * flowRatio : undefined;

  const bucketConfig = [
    { label: '净特大单', weight: 0.44 },
    { label: '净大单', weight: 0.30 },
    { label: '净中单', weight: 0.18 },
    { label: '净小单', weight: -0.08 },
  ];
  const maxAbs = typeof netTotal === 'number'
    ? Math.max(...bucketConfig.map((bucket) => Math.abs(netTotal * bucket.weight)), 1)
    : 1;

  return {
    rows: bucketConfig.map((bucket) => {
      const value = typeof netTotal === 'number' ? netTotal * bucket.weight : undefined;
      return {
        label: bucket.label,
        value,
        ratio: typeof value === 'number' ? Math.min(Math.abs(value) / maxAbs, 1) : 0,
        tone: getMetricTone(value, (amount ?? 0) * 0.002),
      };
    }),
    sourceLabel: quote?.source ? `实时行情 · ${quote.source}` : '价量估算',
    updatedAt: quote?.updateTime,
    netTotal,
  };
}

function buildChipPeakRows(chip?: ChipDistributionMetrics | null, latestClose?: number | null) {
  const grouped = new Map<number, number>();
  (chip?.distribution ?? []).forEach((point) => {
    if (
      Number.isFinite(point.price)
      && point.price > 0
      && Number.isFinite(point.percent)
      && point.percent > 0
    ) {
      grouped.set(point.price, (grouped.get(point.price) ?? 0) + point.percent);
    }
  });

  if (grouped.size === 0) {
    return [];
  }

  const rowsAsc = Array.from(grouped.entries())
    .map(([price, percent]) => ({ price, percent }))
    .sort((a, b) => a.price - b.price);
  const gaps = rowsAsc
    .slice(1)
    .map((row, index) => row.price - rowsAsc[index].price)
    .filter((gap) => gap > 0);
  const priceRange = (rowsAsc.at(-1)?.price ?? rowsAsc[0].price) - rowsAsc[0].price;
  const minGap = gaps.length > 0 ? Math.min(...gaps) : 0.02;
  const tolerance = Math.max(minGap / 2, priceRange * 0.002, 0.01);
  const maxPercent = Math.max(...rowsAsc.map((row) => row.percent), 0.000001);

  return rowsAsc
    .map((row) => ({
      price: row.price,
      ratio: row.percent / maxPercent,
      isAvgCost: typeof chip?.avgCost === 'number' && Math.abs(row.price - chip.avgCost) <= tolerance,
      isCurrent: typeof latestClose === 'number' && Math.abs(row.price - latestClose) <= tolerance,
    }))
    .sort((a, b) => b.price - a.price);
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

function buildTrendPath<T>(
  points: T[],
  width: number,
  top: number,
  height: number,
  minValue: number,
  maxValue: number,
  pick: (point: T) => number | undefined,
): string {
  const values = points
    .map((point, index) => ({ value: pick(point), index }))
    .filter((item): item is { value: number; index: number } => typeof item.value === 'number' && !Number.isNaN(item.value));
  if (values.length === 0) {
    return '';
  }
  const step = width / Math.max(points.length - 1, 1);
  const range = Math.max(maxValue - minValue, 0.01);
  return values.map(({ value, index }, pathIndex) => {
    const x = index * step;
    const y = top + ((maxValue - value) / range) * height;
    return `${pathIndex === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
}

const ChartLegend: React.FC<{ latest?: ChartPoint; periodLabel: string }> = ({ latest, periodLabel }) => (
  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] leading-none">
    <span style={{ color: TERMINAL_COLORS.text }}>{periodLabel}</span>
    <span className="inline-flex items-center gap-1" style={{ color: TERMINAL_COLORS.cyan }}>
      <i className="h-2 w-4 border" style={{ borderColor: TERMINAL_COLORS.cyan, backgroundColor: TERMINAL_COLORS.cyan }} />阳线
    </span>
    <span className="inline-flex items-center gap-1" style={{ color: TERMINAL_COLORS.orange }}>
      <i className="h-2 w-4 border bg-transparent" style={{ borderColor: TERMINAL_COLORS.orange }} />阴线
    </span>
    <span style={{ color: LINE_COLORS.ma5 }}>MA5:{formatNumber(latest?.ma5)}</span>
    <span style={{ color: LINE_COLORS.ma10 }}>MA10:{formatNumber(latest?.ma10)}</span>
    <span style={{ color: LINE_COLORS.ma20 }}>MA20:{formatNumber(latest?.ma20)}</span>
    <span style={{ color: LINE_COLORS.ma30 }}>MA30:{formatNumber(latest?.ma30)}</span>
    <span style={{ color: LINE_COLORS.ma60 }}>MA60:{formatNumber(latest?.ma60)}</span>
  </div>
);

const TerminalTabs: React.FC<{
  labels: string[];
  activeIndex?: number;
  onSelect?: (index: number) => void;
}> = ({ labels, activeIndex = 0, onSelect }) => (
  <div className="flex min-w-max items-center border-t font-mono text-[10px] leading-none" style={{ borderColor: TERMINAL_COLORS.redGrid }}>
    {labels.map((label, index) => (
      <button
        key={`${label}-${index}`}
        type="button"
        onClick={() => onSelect?.(index)}
        className="border-r px-2 py-1"
        style={{
          borderColor: TERMINAL_COLORS.redGrid,
          backgroundColor: index === activeIndex ? TERMINAL_COLORS.activeTabBg : TERMINAL_COLORS.panel,
          color: index === activeIndex ? TERMINAL_COLORS.text : TERMINAL_COLORS.muted,
        }}
      >
        {label}
      </button>
    ))}
  </div>
);

const CandlestickChart: React.FC<{
  points: ChartPoint[];
  visible: ChartPoint[];
  visibleStartIndex: number;
  visibleCount: number;
  safeWindowStart: number;
  maxWindowStart: number;
  hoveredIndex: number | null;
  isHoverPinned: boolean;
  onHoverIndexChange: (index: number | null) => void;
  onHoverPinnedChange: (value: boolean) => void;
  onStepHoverIndex: (direction: HoverStepDirection) => void;
  onWindowStartChange: (value: number) => void;
  onOpenChartMenu: (event: React.MouseEvent) => void;
  tooltipPosition?: TooltipPosition;
  onTooltipPositionChange: (position: TooltipPosition) => void;
  period: KLinePeriod;
  periodLabel: string;
  recentLabel: string;
  sampleUnit: string;
  timeZoneLabel: string;
  selectedPeriod: KLinePeriod;
  onPeriodChange: (period: KLinePeriod) => void;
}> = ({
  points,
  visible,
  visibleStartIndex,
  visibleCount,
  safeWindowStart,
  maxWindowStart,
  hoveredIndex,
  isHoverPinned,
  onHoverIndexChange,
  onHoverPinnedChange,
  onStepHoverIndex,
  onWindowStartChange,
  onOpenChartMenu,
  tooltipPosition,
  onTooltipPositionChange,
  period,
  periodLabel,
  recentLabel,
  sampleUnit,
  timeZoneLabel,
  selectedPeriod,
  onPeriodChange,
}) => {
  const width = 1320;
  const axisWidth = 64;
  const plotRight = width - axisWidth;
  const priceTop = 18;
  const priceHeight = 264;
  const chartBottom = 306;
  const canPan = maxWindowStart > 0;
  const visibleFirst = visible[0];
  const visibleLast = visible.at(-1);
  const priceValues = visible.flatMap((point) => [
    point.high,
    point.low,
    point.ma5,
    point.ma10,
    point.ma20,
    point.ma30,
    point.ma60,
  ])
    .filter((value): value is number => typeof value === 'number' && !Number.isNaN(value));
  const maxPrice = Math.max(...priceValues);
  const minPrice = Math.min(...priceValues);
  const pricePadding = Math.max((maxPrice - minPrice) * 0.08, 0.01);
  const chartMax = maxPrice + pricePadding;
  const chartMin = Math.max(0, minPrice - pricePadding);
  const step = plotRight / Math.max(visible.length - 1, 1);
  const bodyWidth = Math.max(3, Math.min(9, step * 0.55));
  const priceRange = Math.max(chartMax - chartMin, 0.01);

  const yForPrice = (value: number) => priceTop + ((chartMax - value) / priceRange) * priceHeight;

  const ma5Path = buildPath(visible, plotRight, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma5);
  const ma10Path = buildPath(visible, plotRight, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma10);
  const ma20Path = buildPath(visible, plotRight, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma20);
  const ma30Path = buildPath(visible, plotRight, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma30);
  const ma60Path = buildPath(visible, plotRight, priceTop, priceHeight, chartMin, chartMax, (point) => point.ma60);
  const hoveredVisibleIndex = hoveredIndex !== null && hoveredIndex >= visibleStartIndex && hoveredIndex < visibleStartIndex + visible.length
    ? hoveredIndex - visibleStartIndex
    : null;
  const hoveredPoint = hoveredVisibleIndex === null ? null : visible[hoveredVisibleIndex];
  const hoveredPrevious = hoveredIndex === null ? undefined : points[hoveredIndex - 1];
  const hoveredX = hoveredVisibleIndex === null ? 0 : hoveredVisibleIndex * step;
  const tooltipWidth = 238;
  const tooltipHeight = 206;
  const tooltipX = hoveredX > plotRight - tooltipWidth - 18 ? hoveredX - tooltipWidth - 14 : hoveredX + 14;
  const tooltipY = priceTop + 12;
  const activeTooltipPosition = clampTooltipPosition(
    tooltipPosition ?? { x: tooltipX, y: tooltipY },
    tooltipWidth,
    tooltipHeight,
    width,
    chartBottom,
  );
  const latestCloseY = typeof visibleLast?.close === 'number' ? yForPrice(visibleLast.close) : null;

  const shiftWindow = (direction: -1 | 1) => {
    onWindowStartChange(clamp(safeWindowStart + direction * visibleCount, 0, maxWindowStart));
    onHoverPinnedChange(false);
    onHoverIndexChange(null);
  };

  const setVisibleHoverIndex = (index: number) => {
    onHoverIndexChange(visibleStartIndex + index);
  };

  const handleHitMouseDown = (event: React.MouseEvent<SVGRectElement>) => {
    if (!isHoverPinned || (event.button !== 3 && event.button !== 4)) {
      return;
    }
    event.preventDefault();
    onStepHoverIndex(event.button === 3 ? -1 : 1);
  };
  const handleTooltipPointerDown = (event: React.PointerEvent<SVGGElement>) => {
    onHoverPinnedChange(true);
    startTooltipDrag(event, activeTooltipPosition, (position) => {
      onTooltipPositionChange(clampTooltipPosition(position, tooltipWidth, tooltipHeight, width, chartBottom));
    });
  };

  return (
    <div
      className="overflow-hidden"
      style={{ backgroundColor: TERMINAL_COLORS.bg }}
      onContextMenu={onOpenChartMenu}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-2 py-1" style={{ borderColor: TERMINAL_COLORS.redGrid, backgroundColor: TERMINAL_COLORS.panel }}>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <div
            role="tablist"
            aria-label="K线周期"
            className="inline-flex items-center border font-mono text-[10px]"
            style={{ borderColor: TERMINAL_COLORS.redGrid }}
          >
            {KLINE_PERIOD_OPTIONS.map((option) => {
              const selected = selectedPeriod === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  onClick={() => onPeriodChange(option.value)}
                  className="border-r px-2 py-1 last:border-r-0"
                  style={{
                    borderColor: TERMINAL_COLORS.redGrid,
                    backgroundColor: selected ? TERMINAL_COLORS.cyan : TERMINAL_COLORS.panel,
                    color: selected ? TERMINAL_COLORS.selectedText : TERMINAL_COLORS.muted,
                  }}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          <ChartLegend latest={visibleLast} periodLabel={periodLabel} />
        </div>
        <span className="font-mono text-[11px]" style={{ color: TERMINAL_COLORS.muted }}>
          {recentLabel} {visible.length} {sampleUnit} · {periodLabel} · {timeZoneLabel}
        </span>
      </div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${chartBottom}`}
          role="img"
          aria-label="K线图"
          className="h-[12.5rem] min-w-[46rem] w-full"
          style={{ backgroundColor: TERMINAL_COLORS.bg }}
        >
          <rect x="0" y="0" width={width} height={chartBottom} fill={TERMINAL_COLORS.bg} />
          <rect x="0.5" y="0.5" width={plotRight - 1} height={chartBottom - 1} fill="none" stroke={TERMINAL_COLORS.redGrid} strokeWidth="1" />
          {[0, 1, 2, 3, 4].map((line) => {
            const y = priceTop + (priceHeight / 4) * line;
            const value = chartMax - ((chartMax - chartMin) / 4) * line;
            return (
              <g key={`grid-${line}`}>
                <line x1="0" y1={y} x2={plotRight} y2={y} stroke={TERMINAL_COLORS.redGrid} strokeDasharray="2 5" strokeWidth="0.8" opacity="0.82" />
                <text x={plotRight + 8} y={y + 4} fill={TERMINAL_COLORS.axisText} className="text-[11px] font-semibold tabular-nums">{formatNumber(value, 2)}</text>
              </g>
            );
          })}
          {[0.2, 0.4, 0.6, 0.8].map((ratio) => (
            <line
              key={`vertical-grid-${ratio}`}
              x1={plotRight * ratio}
              y1={priceTop}
              x2={plotRight * ratio}
              y2={priceTop + priceHeight}
              stroke={TERMINAL_COLORS.redGridSoft}
              strokeDasharray="2 6"
              opacity="0.5"
            />
          ))}
          <line x1={plotRight} y1="0" x2={plotRight} y2={chartBottom} stroke={TERMINAL_COLORS.axis} strokeWidth="1.2" />
          <line x1="0" y1={priceTop + priceHeight} x2={plotRight} y2={priceTop + priceHeight} stroke={TERMINAL_COLORS.axis} strokeWidth="1" />
          {latestCloseY !== null ? (
            <g>
              <line x1="0" y1={latestCloseY} x2={plotRight} y2={latestCloseY} stroke={TERMINAL_COLORS.white} strokeWidth="1.1" opacity="0.84" />
              <rect x={plotRight + 2} y={latestCloseY - 9} width={axisWidth - 4} height="18" fill={TERMINAL_COLORS.priceTagBg} rx="1" />
              <text x={plotRight + axisWidth / 2} y={latestCloseY + 4} textAnchor="middle" fill={TERMINAL_COLORS.priceTagText} className="text-[11px] font-semibold tabular-nums">
                {formatNumber(visibleLast?.close)}
              </text>
            </g>
          ) : null}

          {visible.map((point, index) => {
            const x = index * step;
            const isUp = point.close >= point.open;
            const candleColor = isUp ? TERMINAL_COLORS.cyan : TERMINAL_COLORS.orange;
            const highY = yForPrice(point.high);
            const lowY = yForPrice(point.low);
            const openY = yForPrice(point.open);
            const closeY = yForPrice(point.close);
            const bodyTop = Math.min(openY, closeY);
            const bodyHeight = Math.max(Math.abs(closeY - openY), 1);
            return (
              <g key={`${point.date}-${index}`}>
                <line x1={x} y1={highY} x2={x} y2={lowY} stroke={candleColor} strokeWidth="1.5" />
                <rect
                  x={x - bodyWidth / 2}
                  y={bodyTop}
                  width={bodyWidth}
                  height={bodyHeight}
                  fill={isUp ? candleColor : TERMINAL_COLORS.bg}
                  stroke={candleColor}
                  strokeWidth="1.4"
                  opacity={isUp ? 0.92 : 1}
                />
                {index % 12 === 0 || index === visible.length - 1 ? (
                  <text x={x} y={chartBottom - 10} textAnchor="middle" fill={TERMINAL_COLORS.axisText} className="text-[10px]">
                    {formatAxisDate(point.date, period)}
                  </text>
                ) : null}
                {index > 0 && index % 11 === 0 ? (
                  <text x={x} y={Math.max(12, highY - 8)} textAnchor="middle" fill={TERMINAL_COLORS.purple} className="text-[10px] font-bold">
                    {(index % 9) + 1}
                  </text>
                ) : null}
                {index > 0 && index % 17 === 0 ? (
                  <rect
                    x={x - 3.5}
                    y={Math.max(6, highY - 28)}
                    width="7"
                    height="7"
                    fill={TERMINAL_COLORS.cyan}
                    opacity="0.9"
                    transform={`rotate(45 ${x} ${Math.max(6, highY - 24.5)})`}
                  />
                ) : null}
              </g>
            );
          })}

          <path d={ma5Path} fill="none" stroke={LINE_COLORS.ma5} strokeWidth="2" />
          <path d={ma10Path} fill="none" stroke={LINE_COLORS.ma10} strokeWidth="2" />
          <path d={ma20Path} fill="none" stroke={LINE_COLORS.ma20} strokeWidth="2" />
          <path d={ma30Path} fill="none" stroke={LINE_COLORS.ma30} strokeWidth="2" />
          <path d={ma60Path} fill="none" stroke={LINE_COLORS.ma60} strokeWidth="2" />

          {visible.map((point, index) => {
            const x = index * step;
            const hitWidth = Math.max(step, bodyWidth + 8);
            return (
              <rect
                key={`hit-${point.date}-${index}`}
                data-testid={`indicator-chart-bar-${point.date}`}
                x={x - hitWidth / 2}
                y={priceTop}
                width={hitWidth}
                height={priceHeight}
                fill="transparent"
                pointerEvents="all"
                tabIndex={0}
                role="graphics-symbol"
                aria-label={`${point.date} 指标明细`}
                onMouseEnter={() => {
                  if (!isHoverPinned) {
                    setVisibleHoverIndex(index);
                  }
                }}
                onMouseLeave={(event) => {
                  if (!isHoverPinned && !isTooltipPanelTarget(event.relatedTarget)) {
                    onHoverIndexChange(null);
                  }
                }}
                onMouseDown={handleHitMouseDown}
                onClick={() => {
                  setVisibleHoverIndex(index);
                  onHoverPinnedChange(true);
                }}
                onFocus={() => {
                  if (!isHoverPinned) {
                    setVisibleHoverIndex(index);
                  }
                }}
                onBlur={() => {
                  if (!isHoverPinned) {
                    onHoverIndexChange(null);
                  }
                }}
              />
            );
          })}

          {hoveredPoint ? (
            <>
              <g pointerEvents="none">
                <line
                  x1={hoveredX}
                  y1={priceTop}
                  x2={hoveredX}
                  y2={priceTop + priceHeight}
                  stroke={TERMINAL_COLORS.white}
                  strokeDasharray="4 5"
                  strokeWidth="1"
                  opacity="0.45"
                />
                <rect
                  x={hoveredX - Math.max(bodyWidth, 8) / 2}
                  y={priceTop}
                  width={Math.max(bodyWidth, 8)}
                  height={priceHeight}
                  fill={TERMINAL_COLORS.cyan}
                  opacity="0.10"
                  rx="3"
                />
              </g>
              <g
                data-testid="indicator-chart-tooltip"
                data-indicator-tooltip-panel="true"
                pointerEvents="all"
                transform={`translate(${activeTooltipPosition.x}, ${activeTooltipPosition.y})`}
                onPointerDown={handleTooltipPointerDown}
                onMouseLeave={(event) => {
                  if (!isHoverPinned && event.buttons !== 1) {
                    onHoverIndexChange(null);
                  }
                }}
                style={{ cursor: 'move' }}
              >
                <rect
                  width={tooltipWidth}
                  height={tooltipHeight}
                  rx="4"
                  fill={TERMINAL_COLORS.tooltipBg}
                  stroke={TERMINAL_COLORS.tooltipBorder}
                  strokeWidth="1"
                  opacity="0.96"
                />
                <text x="14" y="22" fill={TERMINAL_COLORS.yellow} className="text-[12px] font-semibold">
                  {hoveredPoint.date}
                </text>
                <text x={tooltipWidth - 14} y="22" textAnchor="end" fill={hoveredPoint.close >= hoveredPoint.open ? TERMINAL_COLORS.cyan : TERMINAL_COLORS.orange} className="text-[11px]">
                  {hoveredPoint.close >= hoveredPoint.open ? '阳线' : '阴线'}
                </text>

                {[
                  ['开盘', formatNumber(hoveredPoint.open), '最高', formatNumber(hoveredPoint.high)],
                  ['收盘', formatNumber(hoveredPoint.close), '最低', formatNumber(hoveredPoint.low)],
                  ['涨跌额', formatSignedNumber(getPointChange(hoveredPoint, hoveredPrevious)), '涨跌幅', formatPct(getPointChangePct(hoveredPoint, hoveredPrevious))],
                  ['振幅', formatPct(getPointAmplitude(hoveredPoint, hoveredPrevious)), '量比', formatNumber(getPointVolumeRatio(hoveredPoint), 2)],
                  ['成交量', formatCompactNumber(hoveredPoint.volume), '成交额', formatCompactNumber(hoveredPoint.amount)],
                  ['MA5', formatNumber(hoveredPoint.ma5), 'MA10', formatNumber(hoveredPoint.ma10)],
                  ['MA20', formatNumber(hoveredPoint.ma20), '量均5日', formatCompactNumber(hoveredPoint.volumeMa5)],
                ].map(([leftLabel, leftValue, rightLabel, rightValue], rowIndex) => (
                  <g key={`${leftLabel}-${rightLabel}`} transform={`translate(14, ${42 + rowIndex * 22})`}>
                    <text x="0" y="0" fill={TERMINAL_COLORS.tooltipMuted} className="text-[10px]">{leftLabel}</text>
                    <text x="56" y="0" fill={TERMINAL_COLORS.tooltipText} className="text-[11px] font-medium tabular-nums">{leftValue}</text>
                    <text x="124" y="0" fill={TERMINAL_COLORS.tooltipMuted} className="text-[10px]">{rightLabel}</text>
                    <text x="210" y="0" textAnchor="end" fill={TERMINAL_COLORS.tooltipText} className="text-[11px] font-medium tabular-nums">{rightValue}</text>
                  </g>
                ))}
              </g>
            </>
          ) : null}
        </svg>
      </div>
      <div className="flex flex-wrap items-center gap-3 border-t px-2 py-1" style={{ borderColor: TERMINAL_COLORS.redGrid, backgroundColor: TERMINAL_COLORS.panel }}>
        <button
          type="button"
          aria-label="向前移动K线窗口"
          disabled={!canPan || safeWindowStart === 0}
          onClick={() => shiftWindow(-1)}
          className="inline-flex h-6 w-6 items-center justify-center border transition-colors disabled:cursor-not-allowed disabled:opacity-40"
          style={{ borderColor: TERMINAL_COLORS.redGrid, color: TERMINAL_COLORS.muted }}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div className="min-w-[12rem] flex-1">
          <div className="mb-1 flex items-center justify-between gap-2 font-mono text-[10px]" style={{ color: TERMINAL_COLORS.muted }}>
            <span>时间窗口</span>
            <span className="font-mono">
              {visibleFirst?.date ?? '--'} - {visibleLast?.date ?? '--'}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={maxWindowStart}
            value={safeWindowStart}
            disabled={!canPan}
            aria-label="K线时间窗口"
            onChange={(event) => {
              onWindowStartChange(Number(event.currentTarget.value));
              onHoverPinnedChange(false);
              onHoverIndexChange(null);
            }}
            className="h-1.5 w-full cursor-grab disabled:cursor-not-allowed disabled:opacity-40"
            style={{ accentColor: TERMINAL_COLORS.cyan }}
          />
        </div>
        <button
          type="button"
          aria-label="向后移动K线窗口"
          disabled={!canPan || safeWindowStart === maxWindowStart}
          onClick={() => shiftWindow(1)}
          className="inline-flex h-6 w-6 items-center justify-center border transition-colors disabled:cursor-not-allowed disabled:opacity-40"
          style={{ borderColor: TERMINAL_COLORS.redGrid, color: TERMINAL_COLORS.muted }}
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
};

const VolumeActivityChart: React.FC<{
  points: ChartPoint[];
  visible: ChartPoint[];
  visibleStartIndex: number;
  hoveredIndex: number | null;
  isHoverPinned: boolean;
  onHoverIndexChange: (index: number | null) => void;
  onHoverPinnedChange: (value: boolean) => void;
  onStepHoverIndex: (direction: HoverStepDirection) => void;
  onOpenChartMenu: (event: React.MouseEvent) => void;
  tooltipPosition?: TooltipPosition;
  onTooltipPositionChange: (position: TooltipPosition) => void;
  onTooltipPositionReset: () => void;
  period: KLinePeriod;
}> = ({
  points,
  visible,
  visibleStartIndex,
  hoveredIndex,
  isHoverPinned,
  onHoverIndexChange,
  onHoverPinnedChange,
  onStepHoverIndex,
  onOpenChartMenu,
  tooltipPosition,
  onTooltipPositionChange,
  onTooltipPositionReset,
  period,
}) => {
  const [mode, setMode] = useState<VolumeIndicatorMode>('volume');
  const width = 1600;
  const axisWidth = 64;
  const plotRight = width - axisWidth;
  const top = 18;
  const height = 112;
  const bottom = 154;
  const hasAmount = points.some((point) => typeof point.amount === 'number' && point.amount > 0);
  const hasVolumeMa = points.some((point) => typeof point.volumeMa5 === 'number' || typeof point.volumeMa10 === 'number');
  const tabs = [
    { label: '成交量', mode: 'volume' as const },
    ...(hasAmount ? [{ label: '成交额', mode: 'amount' as const }] : []),
    ...(hasVolumeMa ? [{ label: '均量', mode: 'volumeMa' as const }] : []),
  ];
  const activeMode = (mode === 'amount' && !hasAmount) || (mode === 'volumeMa' && !hasVolumeMa) ? 'volume' : mode;
  const pickBarValue = (point: ChartPoint) => {
    if (activeMode === 'amount') {
      return point.amount ?? 0;
    }
    if (activeMode === 'volumeMa') {
      return point.volumeMa5 ?? point.volume ?? 0;
    }
    return point.volume ?? 0;
  };
  const maxVolume = Math.max(...visible.map((point) => pickBarValue(point)), 1);
  const step = plotRight / Math.max(visible.length - 1, 1);
  const bodyWidth = Math.max(4, Math.min(11, step * 0.62));
  const labelStep = Math.max(Math.floor(visible.length / 7), 1);
  const volumeMa5Path = buildTrendPath(
    visible,
    plotRight,
    top,
    height,
    0,
    maxVolume,
    (point) => activeMode === 'amount' ? point.amountMa5 : point.volumeMa5,
  );
  const volumeMa10Path = buildTrendPath(
    visible,
    plotRight,
    top,
    height,
    0,
    maxVolume,
    (point) => activeMode === 'amount' ? point.amountMa10 : point.volumeMa10,
  );
  const hoveredVisibleIndex = hoveredIndex !== null && hoveredIndex >= visibleStartIndex && hoveredIndex < visibleStartIndex + visible.length
    ? hoveredIndex - visibleStartIndex
    : null;
  const hoveredPoint = hoveredVisibleIndex === null ? null : visible[hoveredVisibleIndex];
  const hoveredX = hoveredVisibleIndex === null ? 0 : hoveredVisibleIndex * step;
  const tooltipWidth = 210;
  const tooltipHeight = 108;
  const tooltipX = hoveredX > plotRight - tooltipWidth - 26 ? hoveredX - tooltipWidth - 12 : hoveredX + 12;
  const activeTooltipPosition = clampTooltipPosition(
    tooltipPosition ?? { x: tooltipX, y: top + 12 },
    tooltipWidth,
    tooltipHeight,
    width,
    bottom,
  );
  const latest = visible.at(-1);
  const setVisibleHoverIndex = (index: number) => {
    onHoverIndexChange(visibleStartIndex + index);
  };
  const handleHitMouseDown = (event: React.MouseEvent<SVGRectElement>) => {
    if (!isHoverPinned || (event.button !== 3 && event.button !== 4)) {
      return;
    }
    event.preventDefault();
    onStepHoverIndex(event.button === 3 ? -1 : 1);
  };
  const handleTooltipPointerDown = (event: React.PointerEvent<SVGGElement>) => {
    onHoverPinnedChange(true);
    startTooltipDrag(event, activeTooltipPosition, (position) => {
      onTooltipPositionChange(clampTooltipPosition(position, tooltipWidth, tooltipHeight, width, bottom));
    });
  };

  return (
    <div
      className="overflow-hidden"
      style={{ backgroundColor: TERMINAL_COLORS.bg }}
      onContextMenu={onOpenChartMenu}
    >
      <div className="flex flex-wrap items-center gap-3 border-b px-2 py-1 font-mono text-[11px]" style={{ borderColor: TERMINAL_COLORS.redGrid, backgroundColor: TERMINAL_COLORS.panel }}>
        <span style={{ color: TERMINAL_COLORS.text }}>成交量相关指标</span>
        <span style={{ color: TERMINAL_COLORS.axisText }}>
          {activeMode === 'amount' ? '成交额' : '总手'}:{formatCompactNumber(activeMode === 'amount' ? latest?.amount : latest?.volume)}
        </span>
        <span style={{ color: LINE_COLORS.ma10 }}>{activeMode === 'amount' ? 'MAAMT5' : 'MAVOL5'}:{formatCompactNumber(activeMode === 'amount' ? latest?.amountMa5 : latest?.volumeMa5)}</span>
        <span style={{ color: LINE_COLORS.ma5 }}>{activeMode === 'amount' ? 'MAAMT10' : 'MAVOL10'}:{formatCompactNumber(activeMode === 'amount' ? latest?.amountMa10 : latest?.volumeMa10)}</span>
      </div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${bottom}`}
          role="img"
          aria-label="成交量图"
          className="h-[5.25rem] min-w-[46rem] w-full"
          style={{ backgroundColor: TERMINAL_COLORS.bg }}
        >
          <rect x="0" y="0" width={width} height={bottom} fill={TERMINAL_COLORS.bg} />
          <rect x="0.5" y="0.5" width={plotRight - 1} height={bottom - 1} fill="none" stroke={TERMINAL_COLORS.redGrid} strokeWidth="1" />
          {[0, 1, 2, 3].map((line) => {
            const y = top + (height / 3) * line;
            const value = maxVolume - (maxVolume / 3) * line;
            return (
              <g key={`volume-grid-${line}`}>
                <line x1="0" y1={y} x2={plotRight} y2={y} stroke={TERMINAL_COLORS.redGrid} strokeDasharray="2 5" opacity="0.76" />
                <text x={plotRight + 8} y={y + 4} fill={TERMINAL_COLORS.text} className="text-[10px] font-semibold tabular-nums">{formatCompactNumber(value)}</text>
              </g>
            );
          })}
          {[0.2, 0.4, 0.6, 0.8].map((ratio) => (
            <line
              key={`volume-vertical-grid-${ratio}`}
              x1={plotRight * ratio}
              y1={top}
              x2={plotRight * ratio}
              y2={top + height}
              stroke={TERMINAL_COLORS.redGridSoft}
              strokeDasharray="2 6"
              opacity="0.48"
            />
          ))}
          <line x1={plotRight} y1="0" x2={plotRight} y2={bottom} stroke={TERMINAL_COLORS.axis} strokeWidth="1.1" />
          <line x1="0" y1={top + height} x2={plotRight} y2={top + height} stroke={TERMINAL_COLORS.axis} strokeWidth="1" />

          {visible.map((point, index) => {
            const x = index * step;
            const volumeHeight = (pickBarValue(point) / maxVolume) * height;
            const y = top + height - volumeHeight;
            const isUp = point.close >= point.open;
            const barColor = isUp ? TERMINAL_COLORS.cyan : TERMINAL_COLORS.orange;
            return (
              <g key={`volume-${point.date}-${index}`}>
                <rect
                  x={x - bodyWidth / 2}
                  y={y}
                  width={bodyWidth}
                  height={Math.max(volumeHeight, 1)}
                  fill={isUp ? barColor : TERMINAL_COLORS.bg}
                  stroke={barColor}
                  strokeWidth="1.1"
                  opacity={isUp ? 0.88 : 1}
                />
                {index % labelStep === 0 || index === visible.length - 1 ? (
                  <text x={x} y={bottom - 12} textAnchor="middle" fill={TERMINAL_COLORS.axisText} className="text-[10px]">
                    {formatAxisDate(point.date, period)}
                  </text>
                ) : null}
              </g>
            );
          })}

          <path d={volumeMa5Path} fill="none" stroke={LINE_COLORS.ma10} strokeWidth="2" />
          <path d={volumeMa10Path} fill="none" stroke={LINE_COLORS.ma5} strokeWidth="2" />

          {visible.map((point, index) => {
            const x = index * step;
            return (
              <rect
                key={`volume-hit-${point.date}-${index}`}
                data-testid={`indicator-volume-bar-${point.date}`}
                x={x - Math.max(step, bodyWidth + 8) / 2}
                y={top}
                width={Math.max(step, bodyWidth + 8)}
                height={height}
                fill="transparent"
                pointerEvents="all"
                tabIndex={0}
                role="graphics-symbol"
                aria-label={`${point.date} 成交量明细`}
                onMouseEnter={() => {
                  if (!isHoverPinned) {
                    setVisibleHoverIndex(index);
                  }
                }}
                onMouseLeave={(event) => {
                  if (!isHoverPinned && !isTooltipPanelTarget(event.relatedTarget)) {
                    onHoverIndexChange(null);
                  }
                }}
                onMouseDown={handleHitMouseDown}
                onClick={() => {
                  setVisibleHoverIndex(index);
                  onHoverPinnedChange(true);
                }}
                onFocus={() => {
                  if (!isHoverPinned) {
                    setVisibleHoverIndex(index);
                  }
                }}
                onBlur={() => {
                  if (!isHoverPinned) {
                    onHoverIndexChange(null);
                  }
                }}
              />
            );
          })}

          {hoveredPoint ? (
            <>
              <g pointerEvents="none">
                <line x1={hoveredX} y1={top} x2={hoveredX} y2={top + height} stroke={TERMINAL_COLORS.white} strokeDasharray="4 5" opacity="0.45" />
              </g>
              <g
                data-testid="indicator-volume-tooltip"
                data-indicator-tooltip-panel="true"
                pointerEvents="all"
                transform={`translate(${activeTooltipPosition.x}, ${activeTooltipPosition.y})`}
                onPointerDown={handleTooltipPointerDown}
                onMouseLeave={(event) => {
                  if (!isHoverPinned && event.buttons !== 1) {
                    onHoverIndexChange(null);
                  }
                }}
                style={{ cursor: 'move' }}
              >
                <rect width={tooltipWidth} height={tooltipHeight} rx="4" fill={TERMINAL_COLORS.tooltipBg} stroke={TERMINAL_COLORS.tooltipBorder} opacity="0.96" />
                <text x="12" y="22" fill={TERMINAL_COLORS.yellow} className="text-[12px] font-semibold">{hoveredPoint.date}</text>
                {[
                  ['成交量', formatCompactNumber(hoveredPoint.volume)],
                  ['成交额', formatCompactNumber(hoveredPoint.amount)],
                  ['MAVOL5', formatCompactNumber(hoveredPoint.volumeMa5)],
                  ['MAVOL10', formatCompactNumber(hoveredPoint.volumeMa10)],
                ].map(([label, value], rowIndex) => (
                  <g key={label} transform={`translate(12, ${44 + rowIndex * 18})`}>
                    <text x="0" y="0" fill={TERMINAL_COLORS.tooltipMuted} className="text-[10px]">{label}</text>
                    <text x="186" y="0" textAnchor="end" fill={TERMINAL_COLORS.tooltipText} className="text-[11px] font-medium tabular-nums">{value}</text>
                  </g>
                ))}
              </g>
            </>
          ) : null}
        </svg>
      </div>
      <TerminalTabs
        labels={tabs.map((tab) => tab.label)}
        activeIndex={Math.max(tabs.findIndex((tab) => tab.mode === activeMode), 0)}
        onSelect={(index) => {
          const nextMode = tabs[index]?.mode ?? 'volume';
          if (nextMode !== activeMode) {
            onTooltipPositionReset();
          }
          setMode(nextMode);
        }}
      />
    </div>
  );
};

const MacdSignalChart: React.FC<{
  points: ChartPoint[];
  visible: ChartPoint[];
  visibleStartIndex: number;
  hoveredIndex: number | null;
  isHoverPinned: boolean;
  onHoverIndexChange: (index: number | null) => void;
  onHoverPinnedChange: (value: boolean) => void;
  onStepHoverIndex: (direction: HoverStepDirection) => void;
  onOpenChartMenu: (event: React.MouseEvent) => void;
  tooltipPosition?: TooltipPosition;
  onTooltipPositionChange: (position: TooltipPosition) => void;
  onTooltipPositionReset: () => void;
  period: KLinePeriod;
}> = ({
  points,
  visible,
  visibleStartIndex,
  hoveredIndex,
  isHoverPinned,
  onHoverIndexChange,
  onHoverPinnedChange,
  onStepHoverIndex,
  onOpenChartMenu,
  tooltipPosition,
  onTooltipPositionChange,
  onTooltipPositionReset,
  period,
}) => {
  const [mode, setMode] = useState<MomentumIndicatorMode>('macd');
  const width = 1420;
  const axisWidth = 64;
  const plotRight = width - axisWidth;
  const top = 18;
  const height = 96;
  const bottom = 132;
  const hasRsi = points.some((point) => typeof point.rsi6 === 'number' || typeof point.rsi12 === 'number');
  const tabs = [
    { label: 'MACD', mode: 'macd' as const },
    ...(hasRsi ? [{ label: 'RSI', mode: 'rsi' as const }] : []),
  ];
  const activeMode = mode === 'rsi' && !hasRsi ? 'macd' : mode;
  const values = activeMode === 'rsi'
    ? visible.flatMap((point) => [point.rsi6, point.rsi12]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    : visible.flatMap((point) => [point.dif, point.dea, point.macd])
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const maxAbs = activeMode === 'rsi' ? 100 : Math.max(...values.map((value) => Math.abs(value)), 0.01);
  const minValue = activeMode === 'rsi' ? 0 : -maxAbs;
  const maxValue = activeMode === 'rsi' ? 100 : maxAbs;
  const step = plotRight / Math.max(visible.length - 1, 1);
  const bodyWidth = Math.max(3, Math.min(10, step * 0.58));
  const zeroY = top + height / 2;
  const yForValue = (value?: number) => (
    typeof value === 'number'
      ? top + ((maxValue - value) / Math.max(maxValue - minValue, 0.01)) * height
      : zeroY
  );
  const difPath = buildTrendPath(visible, plotRight, top, height, -maxAbs, maxAbs, (point) => point.dif);
  const deaPath = buildTrendPath(visible, plotRight, top, height, -maxAbs, maxAbs, (point) => point.dea);
  const rsi6Path = buildTrendPath(visible, plotRight, top, height, 0, 100, (point) => point.rsi6);
  const rsi12Path = buildTrendPath(visible, plotRight, top, height, 0, 100, (point) => point.rsi12);
  const labelStep = Math.max(Math.floor(visible.length / 7), 1);
  const latest = visible.at(-1);
  const hoveredVisibleIndex = hoveredIndex !== null && hoveredIndex >= visibleStartIndex && hoveredIndex < visibleStartIndex + visible.length
    ? hoveredIndex - visibleStartIndex
    : null;
  const hoveredX = hoveredVisibleIndex === null ? 0 : hoveredVisibleIndex * step;
  const hoveredPoint = hoveredVisibleIndex === null ? null : visible[hoveredVisibleIndex];
  const tooltipWidth = 192;
  const tooltipHeight = 92;
  const tooltipX = hoveredX > plotRight - tooltipWidth - 26 ? hoveredX - tooltipWidth - 12 : hoveredX + 12;
  const activeTooltipPosition = clampTooltipPosition(
    tooltipPosition ?? { x: tooltipX, y: top + 8 },
    tooltipWidth,
    tooltipHeight,
    width,
    bottom,
  );
  const setVisibleHoverIndex = (index: number) => {
    onHoverIndexChange(visibleStartIndex + index);
  };
  const handleHitMouseDown = (event: React.MouseEvent<SVGRectElement>) => {
    if (!isHoverPinned || (event.button !== 3 && event.button !== 4)) {
      return;
    }
    event.preventDefault();
    onStepHoverIndex(event.button === 3 ? -1 : 1);
  };
  const handleTooltipPointerDown = (event: React.PointerEvent<SVGGElement>) => {
    onHoverPinnedChange(true);
    startTooltipDrag(event, activeTooltipPosition, (position) => {
      onTooltipPositionChange(clampTooltipPosition(position, tooltipWidth, tooltipHeight, width, bottom));
    });
  };

  return (
    <div
      className="overflow-hidden"
      style={{ backgroundColor: TERMINAL_COLORS.bg }}
      onContextMenu={onOpenChartMenu}
    >
      <div className="flex flex-wrap items-center gap-3 border-b px-2 py-1 font-mono text-[11px]" style={{ borderColor: TERMINAL_COLORS.redGrid, backgroundColor: TERMINAL_COLORS.panel }}>
        <span style={{ color: TERMINAL_COLORS.text }}>{activeMode === 'rsi' ? 'RSI指标' : 'MACD等指标'}</span>
        {activeMode === 'rsi' ? (
          <>
            <span style={{ color: TERMINAL_COLORS.green }}>RSI6:{formatNumber(latest?.rsi6)}</span>
            <span style={{ color: TERMINAL_COLORS.blue }}>RSI12:{formatNumber(latest?.rsi12)}</span>
          </>
        ) : (
          <>
            <span style={{ color: TERMINAL_COLORS.yellow }}>DIF:{formatNumber(latest?.dif)}</span>
            <span style={{ color: TERMINAL_COLORS.purple }}>DEA:{formatNumber(latest?.dea)}</span>
            <span style={{ color: (latest?.macd ?? 0) >= 0 ? TERMINAL_COLORS.green : TERMINAL_COLORS.blue }}>MACD:{formatNumber(latest?.macd)}</span>
          </>
        )}
      </div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${bottom}`}
          role="img"
          aria-label="MACD指标图"
          className="h-[5rem] min-w-[46rem] w-full"
          style={{ backgroundColor: TERMINAL_COLORS.bg }}
        >
          <rect x="0" y="0" width={width} height={bottom} fill={TERMINAL_COLORS.bg} />
          <rect x="0.5" y="0.5" width={plotRight - 1} height={bottom - 1} fill="none" stroke={TERMINAL_COLORS.redGrid} strokeWidth="1" />
          {(activeMode === 'rsi' ? [100, 70, 50, 30, 0] : [maxAbs, maxAbs / 2, 0, -maxAbs / 2, -maxAbs]).map((value) => {
            const y = yForValue(value);
            return (
              <g key={`macd-grid-${value}`}>
                <line x1="0" y1={y} x2={plotRight} y2={y} stroke={value === 0 ? TERMINAL_COLORS.axis : TERMINAL_COLORS.redGrid} strokeDasharray={value === 0 ? undefined : '2 5'} opacity={value === 0 ? 0.92 : 0.72} />
                <text x={plotRight + 8} y={y + 4} fill={TERMINAL_COLORS.axisText} className="text-[10px] font-semibold tabular-nums">{formatSignedNumber(value)}</text>
              </g>
            );
          })}
          {[0.2, 0.4, 0.6, 0.8].map((ratio) => (
            <line
              key={`macd-vertical-grid-${ratio}`}
              x1={plotRight * ratio}
              y1={top}
              x2={plotRight * ratio}
              y2={top + height}
              stroke={TERMINAL_COLORS.redGridSoft}
              strokeDasharray="2 6"
              opacity="0.48"
            />
          ))}
          <line x1={plotRight} y1="0" x2={plotRight} y2={bottom} stroke={TERMINAL_COLORS.axis} strokeWidth="1.1" />

          {activeMode === 'macd' ? visible.map((point, index) => {
            const x = index * step;
            const y = yForValue(point.macd);
            const barHeight = Math.abs(y - zeroY);
            const isPositive = (point.macd ?? 0) >= 0;
            return (
              <g key={`macd-${point.date}-${index}`}>
                <rect
                  x={x - bodyWidth / 2}
                  y={isPositive ? y : zeroY}
                  width={bodyWidth}
                  height={Math.max(barHeight, 1)}
                  fill={isPositive ? TERMINAL_COLORS.green : TERMINAL_COLORS.blue}
                  stroke={isPositive ? TERMINAL_COLORS.positiveStroke : TERMINAL_COLORS.negativeStroke}
                  strokeWidth="0.8"
                  opacity="0.86"
                />
                {index % labelStep === 0 || index === visible.length - 1 ? (
                  <text x={x} y={bottom - 12} textAnchor="middle" fill={TERMINAL_COLORS.axisText} className="text-[10px]">
                    {formatAxisDate(point.date, period)}
                  </text>
                ) : null}
              </g>
            );
          }) : null}
          {visible.map((point, index) => {
            const x = index * step;
            return (
              <rect
                key={`momentum-hit-${point.date}-${index}`}
                x={x - Math.max(step, bodyWidth + 8) / 2}
                y={top}
                width={Math.max(step, bodyWidth + 8)}
                height={height}
                fill="transparent"
                pointerEvents="all"
                tabIndex={0}
                role="graphics-symbol"
                aria-label={`${point.date} 动能指标明细`}
                onMouseEnter={() => {
                  if (!isHoverPinned) {
                    setVisibleHoverIndex(index);
                  }
                }}
                onMouseLeave={(event) => {
                  if (!isHoverPinned && !isTooltipPanelTarget(event.relatedTarget)) {
                    onHoverIndexChange(null);
                  }
                }}
                onMouseDown={handleHitMouseDown}
                onClick={() => {
                  setVisibleHoverIndex(index);
                  onHoverPinnedChange(true);
                }}
                onFocus={() => {
                  if (!isHoverPinned) {
                    setVisibleHoverIndex(index);
                  }
                }}
                onBlur={() => {
                  if (!isHoverPinned) {
                    onHoverIndexChange(null);
                  }
                }}
              />
            );
          })}
          {activeMode === 'macd' ? (
            <>
              <path d={difPath} fill="none" stroke={TERMINAL_COLORS.yellow} strokeWidth="2" pointerEvents="none" />
              <path d={deaPath} fill="none" stroke={TERMINAL_COLORS.purple} strokeWidth="2" pointerEvents="none" />
              <path d={rsi6Path} fill="none" stroke={TERMINAL_COLORS.green} strokeWidth="1.5" opacity="0.9" pointerEvents="none" />
              <path d={rsi12Path} fill="none" stroke={TERMINAL_COLORS.blue} strokeWidth="1.5" opacity="0.9" pointerEvents="none" />
            </>
          ) : (
            <>
              <path d={rsi6Path} fill="none" stroke={TERMINAL_COLORS.green} strokeWidth="2" pointerEvents="none" />
              <path d={rsi12Path} fill="none" stroke={TERMINAL_COLORS.blue} strokeWidth="2" pointerEvents="none" />
            </>
          )}
          {hoveredVisibleIndex !== null ? (
            <line x1={hoveredX} y1={top} x2={hoveredX} y2={top + height} stroke={TERMINAL_COLORS.white} strokeDasharray="4 5" opacity="0.45" pointerEvents="none" />
          ) : null}
          {hoveredPoint ? (
            <g
              data-testid="indicator-momentum-tooltip"
              data-indicator-tooltip-panel="true"
              pointerEvents="all"
              transform={`translate(${activeTooltipPosition.x}, ${activeTooltipPosition.y})`}
              onPointerDown={handleTooltipPointerDown}
              onMouseLeave={(event) => {
                if (!isHoverPinned && event.buttons !== 1) {
                  onHoverIndexChange(null);
                }
              }}
              style={{ cursor: 'move' }}
            >
              <rect width={tooltipWidth} height={tooltipHeight} rx="4" fill={TERMINAL_COLORS.tooltipBg} stroke={TERMINAL_COLORS.tooltipBorder} opacity="0.96" />
              <text x="12" y="21" fill={TERMINAL_COLORS.yellow} className="text-[12px] font-semibold">{hoveredPoint.date}</text>
              {(activeMode === 'rsi'
                ? [
                  ['RSI6', formatNumber(hoveredPoint.rsi6)],
                  ['RSI12', formatNumber(hoveredPoint.rsi12)],
                ]
                : [
                  ['DIF', formatNumber(hoveredPoint.dif)],
                  ['DEA', formatNumber(hoveredPoint.dea)],
                  ['MACD', formatNumber(hoveredPoint.macd)],
                ]
              ).map(([label, value], rowIndex) => (
                <g key={label} transform={`translate(12, ${43 + rowIndex * 18})`}>
                  <text x="0" y="0" fill={TERMINAL_COLORS.tooltipMuted} className="text-[10px]">{label}</text>
                  <text x="166" y="0" textAnchor="end" fill={TERMINAL_COLORS.tooltipText} className="text-[11px] font-medium tabular-nums">{value}</text>
                </g>
              ))}
            </g>
          ) : null}
        </svg>
      </div>
      <TerminalTabs
        labels={tabs.map((tab) => tab.label)}
        activeIndex={Math.max(tabs.findIndex((tab) => tab.mode === activeMode), 0)}
        onSelect={(index) => {
          const nextMode = tabs[index]?.mode ?? 'macd';
          if (nextMode !== activeMode) {
            onTooltipPositionReset();
          }
          setMode(nextMode);
        }}
      />
    </div>
  );
};

const ChipPeakPanel: React.FC<{
  points: ChartPoint[];
  currentPoint?: ChartPoint | null;
  chip: ChipDistributionMetrics | null;
  mainChip: ChipDistributionMetrics | null;
  requiresRealChipData: boolean;
}> = ({ points, currentPoint, chip, mainChip, requiresRealChipData }) => {
  const [activeScope, setActiveScope] = useState<ChipPanelScope>('all');
  const [activeRange, setActiveRange] = useState<ChipRangeLevel>('90');
  const activeChip = activeScope === 'main' ? mainChip : chip;
  const latestClose = currentPoint?.close ?? points.at(-1)?.close;
  const rows = useMemo(
    () => (activeChip ? buildChipPeakRows(activeChip, latestClose) : []),
    [activeChip, latestClose],
  );
  const svgWidth = 555;
  const svgHeight = 300;
  const chartTop = 20;
  const chartHeight = 250;
  const chartBottom = chartTop + chartHeight;
  const axisX = 74;
  const barMaxWidth = 455;
  const axisPrices = [
    ...rows.map((row) => row.price),
    activeChip?.cost90Low,
    activeChip?.cost90High,
    activeChip?.cost70Low,
    activeChip?.cost70High,
    activeChip?.avgCost,
    latestClose,
  ].filter((value): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0);
  const priceMax = axisPrices.length > 0 ? Math.max(...axisPrices) : 1;
  const priceMin = axisPrices.length > 0 ? Math.min(...axisPrices) : 0;
  const priceRange = Math.max(priceMax - priceMin, 0.01);
  const yForPrice = (price: number) => chartTop + ((priceMax - price) / priceRange) * chartHeight;
  const currentY = typeof latestClose === 'number' ? yForPrice(latestClose) : null;
  const avgCostY = typeof activeChip?.avgCost === 'number' ? yForPrice(activeChip.avgCost) : null;
  const profitRatio = activeChip?.profitRatio;
  const trappedRatio = typeof profitRatio === 'number' ? 1 - profitRatio : undefined;
  const concentration = getChipConcentration(activeChip, activeRange);

  return (
    <aside
      data-testid="chip-peak-panel"
      className="flex min-h-0 flex-col overflow-hidden rounded-md border"
      style={{ borderColor: TERMINAL_COLORS.redGrid, backgroundColor: TERMINAL_COLORS.bg, boxShadow: TERMINAL_COLORS.shadow }}
      aria-label="筹码峰"
    >
      <div className="flex items-start justify-between gap-2 border-b px-3 py-2" style={{ borderColor: TERMINAL_COLORS.redGrid, backgroundColor: TERMINAL_COLORS.panel }}>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Layers3 className="h-4 w-4" style={{ color: TERMINAL_COLORS.cyan }} />
            <h3 className="text-sm font-semibold" style={{ color: TERMINAL_COLORS.text }}>筹码峰</h3>
          </div>
        </div>
        {activeChip?.date ? <span className="font-mono text-[11px]" style={{ color: TERMINAL_COLORS.muted }}>{activeChip.date}</span> : null}
      </div>

      <div className="px-2 py-2">
        {rows.length === 0 ? (
          <div className="border border-dashed px-3 py-6 text-center text-xs" style={{ borderColor: TERMINAL_COLORS.redGrid, color: TERMINAL_COLORS.muted }}>
            {activeScope === 'main'
              ? '暂无同源主力筹码峰明细'
              : requiresRealChipData
                ? '真实筹码明细与本地模型均不可用，无法与同花顺对齐'
                : '暂无真实筹码峰明细'}
          </div>
        ) : (
          <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} role="img" aria-label="筹码峰分布图" className="h-[10rem] w-full">
            <rect x="0" y="0" width={svgWidth} height={svgHeight} fill={TERMINAL_COLORS.bg} />
            <rect x="0.5" y="0.5" width={svgWidth - 1} height={svgHeight - 1} fill="none" stroke={TERMINAL_COLORS.redGrid} />
            {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
              const y = chartTop + chartHeight * ratio;
              const price = priceMax - priceRange * ratio;
              return (
                <g key={`chip-grid-${ratio}`}>
                  <line x1={axisX} y1={y} x2={svgWidth - 10} y2={y} stroke={TERMINAL_COLORS.redGridSoft} strokeDasharray="2 5" opacity="0.74" />
                  <text x="8" y={y + 4} fill={TERMINAL_COLORS.axisText} className="text-[10px] font-semibold tabular-nums">{formatNumber(price)}</text>
                </g>
              );
            })}
            <line x1={axisX} y1={chartTop - 4} x2={axisX} y2={chartBottom + 4} stroke={TERMINAL_COLORS.axis} strokeWidth="1.1" />
            {rows.map((row) => {
              const y = yForPrice(row.price);
              const widthValue = Math.max(row.ratio * barMaxWidth, 5);
              const isAbove = typeof latestClose === 'number' && row.price > latestClose;
              const barColor = row.isCurrent
                ? TERMINAL_COLORS.cyan
                : row.isAvgCost
                  ? TERMINAL_COLORS.yellow
                  : isAbove
                    ? TERMINAL_COLORS.chipBlue
                    : TERMINAL_COLORS.orange;
              return (
                <g key={`chip-row-${row.price.toFixed(4)}`}>
                  <rect
                    x={axisX}
                    y={y - 2.5}
                    width={widthValue}
                    height="5"
                    fill={barColor}
                    opacity={row.isCurrent || row.isAvgCost ? 0.96 : 0.82}
                  />
                  <rect
                    x={axisX + widthValue}
                    y={y - 1.2}
                    width="9"
                    height="2.4"
                    fill={barColor}
                    opacity="0.72"
                  />
                </g>
              );
            })}
            {currentY !== null ? (
              <g>
                <line x1={axisX} y1={currentY} x2={svgWidth - 8} y2={currentY} stroke={TERMINAL_COLORS.white} strokeWidth="1.1" />
                <rect x={axisX - 8} y={currentY - 8} width="58" height="16" fill={TERMINAL_COLORS.priceTagBg} rx="1" />
                <text x={axisX + 21} y={currentY + 4} textAnchor="middle" fill={TERMINAL_COLORS.priceTagText} className="text-[10px] font-semibold tabular-nums">
                  {formatNumber(latestClose)}
                </text>
              </g>
            ) : null}
            {avgCostY !== null ? (
              <g>
                <line x1={axisX} y1={avgCostY} x2={svgWidth - 8} y2={avgCostY} stroke={TERMINAL_COLORS.yellow} strokeWidth="1" strokeDasharray="4 3" />
                <text x={svgWidth - 10} y={avgCostY - 4} textAnchor="end" fill={TERMINAL_COLORS.yellow} className="text-[10px] font-semibold">平均成本</text>
              </g>
            ) : null}
            <text x={axisX + 8} y={chartTop + 12} fill={TERMINAL_COLORS.cyan} className="text-[10px] font-semibold">套牢</text>
            <text x={axisX + 8} y={chartBottom - 8} fill={TERMINAL_COLORS.orange} className="text-[10px] font-semibold">获利</text>
          </svg>
        )}
      </div>

      <div className="border-t px-3 py-2" style={{ borderColor: TERMINAL_COLORS.redGrid }}>
        <div
          role="tablist"
          aria-label="筹码明细范围"
          className="inline-flex overflow-hidden rounded border font-mono text-[11px]"
          style={{ borderColor: TERMINAL_COLORS.redGrid }}
        >
          {([
            { value: 'all', label: '全部筹码' },
            { value: 'main', label: '主力筹码' },
          ] as Array<{ value: ChipPanelScope; label: string }>).map((option) => {
            const selected = activeScope === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setActiveScope(option.value)}
                className="border-r px-2.5 py-1 last:border-r-0"
                style={{
                  borderColor: TERMINAL_COLORS.redGrid,
                  backgroundColor: selected ? TERMINAL_COLORS.activeTabBg : TERMINAL_COLORS.panel,
                  color: selected ? TERMINAL_COLORS.text : TERMINAL_COLORS.muted,
                }}
              >
                {option.label}
              </button>
            );
          })}
        </div>

        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 font-mono text-[12px]">
          <div className="flex items-center justify-between gap-2">
            <dt style={{ color: TERMINAL_COLORS.orange }}>收盘获利</dt>
            <dd className="font-semibold tabular-nums" style={{ color: TERMINAL_COLORS.orange }}>{formatRatioPct(profitRatio)}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt style={{ color: TERMINAL_COLORS.chipBlue }}>套牢盘</dt>
            <dd className="font-semibold tabular-nums" style={{ color: TERMINAL_COLORS.chipBlue }}>{formatRatioPct(trappedRatio)}</dd>
          </div>
          <div className="col-span-2 flex items-center justify-between gap-2">
            <dt style={{ color: TERMINAL_COLORS.yellow }}>平均成本</dt>
            <dd className="font-semibold tabular-nums" style={{ color: TERMINAL_COLORS.yellow }}>{formatNumber(activeChip?.avgCost)}</dd>
          </div>
        </dl>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div
            role="tablist"
            aria-label="筹码区间"
            className="inline-flex overflow-hidden rounded border font-mono text-[11px]"
            style={{ borderColor: TERMINAL_COLORS.redGrid }}
          >
            {([
              { value: '90', label: '90%筹码' },
              { value: '70', label: '70%筹码' },
            ] as Array<{ value: ChipRangeLevel; label: string }>).map((option) => {
              const selected = activeRange === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  onClick={() => setActiveRange(option.value)}
                  className="border-r px-2.5 py-1 last:border-r-0"
                  style={{
                    borderColor: TERMINAL_COLORS.redGrid,
                    backgroundColor: selected ? TERMINAL_COLORS.text : TERMINAL_COLORS.panel,
                    color: selected ? TERMINAL_COLORS.selectedText : TERMINAL_COLORS.muted,
                  }}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>

        <dl className="mt-2 space-y-1.5 font-mono text-[12px]">
          <div className="flex items-center justify-between gap-3">
            <dt style={{ color: TERMINAL_COLORS.muted }}>价格区间</dt>
            <dd className="font-semibold tabular-nums" style={{ color: TERMINAL_COLORS.text }}>{formatChipRange(activeChip, activeRange)}</dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt style={{ color: TERMINAL_COLORS.muted }}>集中度</dt>
            <dd className="font-semibold tabular-nums" style={{ color: TERMINAL_COLORS.text }}>{formatRatioPct(concentration)}</dd>
          </div>
        </dl>
      </div>
    </aside>
  );
};

const OrderFlowMonitor: React.FC<{
  points: ChartPoint[];
  quote: StockQuote | null;
}> = ({ points, quote }) => {
  const flow = useMemo(() => buildOrderFlowMetrics(points, quote), [points, quote]);
  const netTone = getMetricTone(flow.netTotal);

  return (
    <aside
      data-testid="order-flow-monitor"
      className="rounded-xl border border-subtle bg-surface/75 p-3"
      aria-label="实时分单监控"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Radio className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">实时监控</h3>
          </div>
          <p className="mt-1 text-[11px] text-muted-text">{flow.sourceLabel}</p>
        </div>
        <Badge variant={netTone === 'success' ? 'success' : netTone === 'danger' ? 'danger' : 'warning'} size="sm">
          {netTone === 'success' ? '流入' : netTone === 'danger' ? '流出' : '均衡'}
        </Badge>
      </div>

      <div className={`mb-3 rounded-lg border border-subtle bg-background/50 px-3 py-2 ${getValueToneClass(netTone)}`}>
        <div className="text-[11px] text-muted-text">主力净额</div>
        <div className="mt-1 text-lg font-semibold tabular-nums">{formatCompactNumber(flow.netTotal)}</div>
      </div>

      <div className="space-y-3">
        {flow.rows.map((row) => (
          <div key={row.label}>
            <div className="mb-1 flex items-center justify-between gap-2 text-xs">
              <span className="text-secondary-text">{row.label}</span>
              <span className={`font-semibold tabular-nums ${getValueToneClass(row.tone)}`}>
                {formatCompactNumber(row.value)}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-background/70">
              <div
                className={`h-full rounded-full ${row.tone === 'success' ? 'bg-success' : row.tone === 'danger' ? 'bg-danger' : 'bg-warning'}`}
                style={{ width: `${Math.max(row.ratio * 100, 6)}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 border-t border-subtle pt-3 text-[11px] text-muted-text">
        更新时间 {formatDateTime(flow.updatedAt)}。美股或分单源缺失时使用价量估算，保证页面连续展示。
      </div>
    </aside>
  );
};

const IndicatorSidePanel: React.FC<{
  points: ChartPoint[];
  chipPoints: ChartPoint[];
  chipPoint?: ChartPoint | null;
  chip: ChipDistributionMetrics | null;
  mainChip: ChipDistributionMetrics | null;
  requiresRealChipData: boolean;
  quote: StockQuote | null;
}> = ({
  points,
  chipPoints,
  chipPoint,
  chip,
  mainChip,
  requiresRealChipData,
  quote,
}) => {
  const [activeTab, setActiveTab] = useState<SidePanelTab>('chip');

  return (
    <div className="flex min-h-0 flex-col gap-2 xl:flex-1" data-testid="indicator-side-panel">
      <div
        role="tablist"
        aria-label="筹码与监控切换"
        className="grid grid-cols-2 overflow-hidden rounded-md border font-mono text-xs"
        style={{ borderColor: TERMINAL_COLORS.redGrid, backgroundColor: TERMINAL_COLORS.panel }}
      >
        {([
          { value: 'chip', label: '筹码峰', icon: Layers3 },
          { value: 'flow', label: '实时监控', icon: Radio },
        ] as Array<{ value: SidePanelTab; label: string; icon: React.ElementType<{ className?: string }> }>).map((option) => {
          const selected = activeTab === option.value;
          const Icon = option.icon;
          return (
            <button
              key={option.value}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setActiveTab(option.value)}
              className="flex items-center justify-center gap-2 border-r px-3 py-2 last:border-r-0"
              style={{
                borderColor: TERMINAL_COLORS.redGrid,
                backgroundColor: selected ? TERMINAL_COLORS.activeTabBg : TERMINAL_COLORS.panel,
                color: selected ? TERMINAL_COLORS.text : TERMINAL_COLORS.muted,
              }}
            >
              <Icon className="h-4 w-4" />
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {activeTab === 'chip' ? (
          <ChipPeakPanel
            points={chipPoints}
            currentPoint={chipPoint}
            chip={chip}
            mainChip={mainChip}
            requiresRealChipData={requiresRealChipData}
          />
        ) : (
          <OrderFlowMonitor points={points} quote={quote} />
        )}
      </div>
    </div>
  );
};

const MarketStructureTrendChart: React.FC<{
  trend: StructureTrendPoint[];
  signal: MainForceSignal;
  holderOptions: HolderOption[];
  selectedHolderKey: string;
  onSelectedHolderKeyChange: (value: string) => void;
}> = ({
  trend,
  signal,
  holderOptions,
  selectedHolderKey,
  onSelectedHolderKeyChange,
}) => {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const width = 760;
  const priceTop = 34;
  const priceHeight = 168;
  const forceTop = 236;
  const forceHeight = 70;
  const chartBottom = 340;
  const priceValues = trend
    .flatMap((point) => [point.close, point.avgCost])
    .filter((value): value is number => typeof value === 'number' && !Number.isNaN(value));
  const maxPrice = Math.max(...priceValues, 1);
  const minPrice = Math.min(...priceValues, maxPrice);
  const pricePadding = Math.max((maxPrice - minPrice) * 0.1, maxPrice * 0.01, 0.01);
  const chartMax = maxPrice + pricePadding;
  const chartMin = Math.max(0, minPrice - pricePadding);
  const closePath = buildTrendPath(trend, width, priceTop, priceHeight, chartMin, chartMax, (point) => point.close);
  const avgCostPath = buildTrendPath(trend, width, priceTop, priceHeight, chartMin, chartMax, (point) => point.avgCost);
  const forcePath = buildTrendPath(trend, width, forceTop, forceHeight, 0, 100, (point) => point.forceScore);
  const normalizeRatioForChart = (value?: number): number | undefined => (
    typeof value === 'number' && !Number.isNaN(value)
      ? (Math.abs(value) <= 1 ? value * 100 : value)
      : undefined
  );
  const profitPath = buildTrendPath(trend, width, forceTop, forceHeight, 0, 100, (point) => (
    normalizeRatioForChart(point.profitRatio)
  ));
  const labelStep = Math.max(Math.floor(trend.length / 5), 1);
  const latest = trend.at(-1);
  const hoveredPoint = hoveredIndex === null ? null : trend[hoveredIndex];
  const step = width / Math.max(trend.length - 1, 1);
  const hoveredX = hoveredIndex === null ? 0 : hoveredIndex * step;
  const tooltipWidth = 248;
  const tooltipHeight = 156;
  const tooltipX = hoveredX > width - tooltipWidth - 18 ? hoveredX - tooltipWidth - 14 : hoveredX + 14;
  const tooltipY = priceTop + 12;
  const latestForceTone = latest ? getSignedTone(latest.force5) : 'neutral';
  const latestForceClass = latestForceTone === 'success'
    ? 'fill-success'
    : latestForceTone === 'danger'
      ? 'fill-danger'
      : latestForceTone === 'warning'
        ? 'fill-warning'
        : 'fill-foreground';

  return (
    <div className="rounded-xl border border-subtle bg-surface/75 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <LineChart className="h-4 w-4 shrink-0 text-primary" />
          <h4 className="text-sm font-semibold text-foreground">主力筹码趋势</h4>
          <Badge variant={signal.variant} size="sm">{signal.label}</Badge>
        </div>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
          <span className="text-[11px] text-muted-text">主力名称</span>
          <select
            data-testid="major-holder-select"
            aria-label="选择主力名称"
            value={selectedHolderKey}
            onChange={(event) => onSelectedHolderKeyChange(event.target.value)}
            className="h-8 max-w-[18rem] rounded-lg border border-subtle bg-background/70 px-3 pr-8 text-xs font-medium text-foreground outline-none transition-colors focus:border-primary/60 focus:ring-2 focus:ring-primary/20"
          >
            {holderOptions.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
          <span className="text-[11px] text-muted-text">最近 {trend.length} 个交易日</span>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-3 text-[11px] text-muted-text">
        <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5" style={{ backgroundColor: LINE_COLORS.close }} />收盘价</span>
        <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5" style={{ backgroundColor: LINE_COLORS.avgCost }} />筹码成本</span>
        <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5" style={{ backgroundColor: LINE_COLORS.force }} />主力动能</span>
        <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5" style={{ backgroundColor: LINE_COLORS.profit }} />获利盘</span>
      </div>

      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${width} ${chartBottom}`}
          role="img"
          aria-label="主力筹码趋势图"
          className="h-[18rem] min-w-[42rem] w-full rounded-lg bg-background/60"
        >
          {[0, 1, 2, 3].map((line) => {
            const y = priceTop + (priceHeight / 3) * line;
            const value = chartMax - ((chartMax - chartMin) / 3) * line;
            return (
              <g key={`structure-price-grid-${line}`}>
                <line x1="0" y1={y} x2={width} y2={y} stroke="currentColor" className="text-border/60" strokeWidth="1" />
                <text x="6" y={y - 5} className="fill-muted-text text-[10px]">{formatNumber(value, 2)}</text>
              </g>
            );
          })}

          {[0, 50, 100].map((value) => {
            const y = forceTop + ((100 - value) / 100) * forceHeight;
            return (
              <g key={`structure-force-grid-${value}`}>
                <line x1="0" y1={y} x2={width} y2={y} stroke="currentColor" className="text-border/45" strokeWidth="1" />
                <text x="6" y={y - 5} className="fill-muted-text text-[10px]">{value}</text>
              </g>
            );
          })}

          <path d={closePath} fill="none" stroke={LINE_COLORS.close} strokeWidth="2" />
          <path d={avgCostPath} fill="none" stroke={LINE_COLORS.avgCost} strokeWidth="2" />
          <path d={forcePath} fill="none" stroke={LINE_COLORS.force} strokeWidth="2.2" />
          <path d={profitPath} fill="none" stroke={LINE_COLORS.profit} strokeWidth="1.8" strokeDasharray="5 5" />
          <line x1="0" y1={forceTop - 18} x2={width} y2={forceTop - 18} stroke="currentColor" className="text-border/60" />
          <text x="6" y={forceTop - 24} className="fill-muted-text text-[10px]">动能 / 获利盘</text>

          {trend.map((point, index) => {
            const x = step * index;
            const shouldLabel = index % labelStep === 0 || index === trend.length - 1;
            return shouldLabel ? (
              <text key={`structure-date-${point.date}`} x={x} y={chartBottom - 10} textAnchor="middle" className="fill-muted-text text-[10px]">
                {point.date.slice(5)}
              </text>
            ) : null;
          })}

          {trend.map((point, index) => {
            const x = step * index;
            const hitWidth = Math.max(step, 12);
            return (
              <rect
                key={`structure-hit-${point.date}-${index}`}
                data-testid={`market-structure-trend-point-${point.date}`}
                x={x - hitWidth / 2}
                y={priceTop}
                width={hitWidth}
                height={chartBottom - priceTop - 18}
                fill="transparent"
                pointerEvents="all"
                tabIndex={0}
                role="graphics-symbol"
                aria-label={`${point.date} 主力筹码明细`}
                onMouseEnter={() => setHoveredIndex(index)}
                onMouseLeave={() => setHoveredIndex((current) => (current === index ? null : current))}
                onFocus={() => setHoveredIndex(index)}
                onBlur={() => setHoveredIndex((current) => (current === index ? null : current))}
              />
            );
          })}

          {latest ? (
            <g transform={`translate(${width - 176}, 18)`}>
              <rect width="166" height="58" rx="9" fill="#101522" stroke="#334155" opacity="0.94" />
              <text x="12" y="21" className="fill-muted-text text-[10px]">最新成本</text>
              <text x="154" y="21" textAnchor="end" className="fill-foreground text-[12px] font-semibold tabular-nums">{formatNumber(latest.avgCost)}</text>
              <text x="12" y="43" className="fill-muted-text text-[10px]">5日动能</text>
              <text x="154" y="43" textAnchor="end" className={`text-[12px] font-semibold tabular-nums ${latestForceClass}`}>
                {formatSignedRatioPct(latest.force5)}
              </text>
            </g>
          ) : null}

          {hoveredPoint ? (
            <g data-testid="market-structure-trend-tooltip" pointerEvents="none">
              <line
                x1={hoveredX}
                y1={priceTop}
                x2={hoveredX}
                y2={chartBottom - 24}
                stroke="#e5e7eb"
                strokeDasharray="4 5"
                strokeWidth="1"
                opacity="0.45"
              />
              <rect
                x={hoveredX - Math.max(step, 8) / 2}
                y={priceTop}
                width={Math.max(step, 8)}
                height={chartBottom - priceTop - 24}
                fill={LINE_COLORS.force}
                opacity="0.08"
                rx="3"
              />
              <g transform={`translate(${tooltipX}, ${tooltipY})`}>
                <rect
                  width={tooltipWidth}
                  height={tooltipHeight}
                  rx="10"
                  fill="#101522"
                  stroke="#334155"
                  strokeWidth="1"
                  opacity="0.98"
                />
                <text x="14" y="22" className="fill-foreground text-[12px] font-semibold">
                  {hoveredPoint.date}
                </text>
                <text x={tooltipWidth - 14} y="22" textAnchor="end" className="fill-cyan text-[11px]">
                  主力筹码
                </text>
                {[
                  ['收盘价', formatNumber(hoveredPoint.close), '筹码成本', formatNumber(hoveredPoint.avgCost)],
                  ['主力动能', formatNumber(hoveredPoint.forceScore, 1), '获利盘', formatRatioPct(hoveredPoint.profitRatio)],
                  ['5日动能', formatSignedRatioPct(hoveredPoint.force5), '20日动能', formatSignedRatioPct(hoveredPoint.force20)],
                  ['90%集中度', formatRatioPct(hoveredPoint.concentration90), '样本点', `${(hoveredIndex ?? 0) + 1}/${trend.length}`],
                ].map(([leftLabel, leftValue, rightLabel, rightValue], rowIndex) => (
                  <g key={`${leftLabel}-${rightLabel}`} transform={`translate(14, ${46 + rowIndex * 24})`}>
                    <text x="0" y="0" className="fill-muted-text text-[10px]">{leftLabel}</text>
                    <text x="66" y="0" className="fill-foreground text-[11px] font-medium tabular-nums">{leftValue}</text>
                    <text x="134" y="0" className="fill-muted-text text-[10px]">{rightLabel}</text>
                    <text x="220" y="0" textAnchor="end" className="fill-foreground text-[11px] font-medium tabular-nums">{rightValue}</text>
                  </g>
                ))}
              </g>
            </g>
          ) : null}
        </svg>
      </div>
    </div>
  );
};

const MarketStructureStrip: React.FC<{
  points: ChartPoint[];
  quote: StockQuote | null;
  metrics: StockIndicatorMetrics | null;
  metricsError: string | null;
  estimatedChip: ChipDistributionMetrics | null;
  derivedVolumeRatio?: number;
}> = ({
  points,
  quote,
  metrics,
  metricsError,
  estimatedChip,
  derivedVolumeRatio,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [selectedHolderKey, setSelectedHolderKey] = useState(ALL_HOLDERS_KEY);
  const chip = metrics?.chipDistribution ?? estimatedChip;
  const isEstimatedChip = !metrics?.chipDistribution && chip?.source === 'history_estimate';
  const holders = metrics?.majorHolders ?? EMPTY_HOLDERS;
  const volumeRatio = quote?.volumeRatio ?? derivedVolumeRatio;
  const holderOptions = useMemo(() => buildHolderOptions(holders), [holders]);
  const effectiveSelectedHolderKey = holderOptions.some((option) => option.key === selectedHolderKey)
    ? selectedHolderKey
    : ALL_HOLDERS_KEY;
  const selectedHolder = holderOptions.find((option) => option.key === effectiveSelectedHolderKey) ?? holderOptions[0];
  const activeHolders = selectedHolder?.holders ?? EMPTY_HOLDERS;
  const holderProfile = useMemo(
    () => buildHolderScopeProfile(activeHolders, holders),
    [activeHolders, holders],
  );
  const scopedChip = useMemo(() => adjustChipForHolderScope(chip ?? null, holderProfile), [chip, holderProfile]);
  const trend = useMemo(() => buildStructureTrend(points, chip, holderProfile), [points, chip, holderProfile]);
  const signal = useMemo(() => buildMainForceSignal(points, activeHolders, holderProfile), [points, activeHolders, holderProfile]);

  const mainMetricRows: Array<{ label: string; value: React.ReactNode; tone?: MainForceVariant | 'neutral' }> = [
    {
      label: '当前主力',
      value: <span data-testid="selected-holder-label">{selectedHolder?.label ?? '所有主力合计'}</span>,
    },
    { label: '覆盖比例', value: formatPlainPct(holderProfile.ratio) },
    { label: '主力动向', value: signal.label, tone: signal.variant },
    { label: '判断依据', value: signal.summary },
    { label: '5日动能', value: formatSignedRatioPct(signal.force5), tone: getSignedTone(signal.force5) },
    { label: '20日动能', value: formatSignedRatioPct(signal.force20), tone: getSignedTone(signal.force20) },
    { label: '主力获利盘', value: formatRatioPct(scopedChip?.profitRatio) },
    { label: '主力成本', value: formatNumber(scopedChip?.avgCost) },
    { label: '主力90%区间', value: formatCostRange(scopedChip) },
  ];
  const baseMetricRows: Array<{ label: string; value: React.ReactNode; tone?: MainForceVariant | 'neutral' }> = [
    { label: '换手率', value: formatPlainPct(quote?.turnoverRate) },
    { label: '量比', value: formatNumber(volumeRatio, 2) },
    { label: '筹码获利盘', value: formatRatioPct(chip?.profitRatio) },
    { label: '平均成本', value: formatNumber(chip?.avgCost) },
    { label: '90%成本区间', value: formatCostRange(chip) },
    { label: '90%集中度', value: formatRatioPct(chip?.concentration90) },
    { label: '70%集中度', value: formatRatioPct(chip?.concentration70) },
  ];

  return (
    <section className="rounded-xl border border-primary/25 bg-primary/5 px-4 py-3 shadow-[0_0_26px_rgba(34,211,238,0.06)]" aria-label="主力持仓与筹码分布">
      <button
        type="button"
        data-testid="market-structure-toggle"
        aria-expanded={isExpanded}
        aria-label={`${isExpanded ? '收起' : '展开'}主力持仓与筹码分布`}
        onClick={() => setIsExpanded((value) => !value)}
        className={`flex w-full items-center justify-between gap-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary/45 ${isExpanded ? 'mb-3' : ''}`}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Users className="h-4 w-4 shrink-0 text-primary" />
          <h3 className="text-sm font-semibold text-foreground">主力持仓与筹码分布</h3>
          {chip?.date ? <span className="font-mono text-[11px] text-muted-text">{chip.date}</span> : null}
          {isEstimatedChip ? <Badge variant="warning" size="sm">历史估算</Badge> : null}
          {holders.length > 0 ? <Badge variant="info" size="sm">{holders.length} 名</Badge> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {metricsError ? <span className="text-[11px] text-warning">主力数据暂不可用</span> : null}
          <ChevronDown className={`h-4 w-4 text-muted-text transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {isExpanded ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(18rem,0.8fr)]">
          <MarketStructureTrendChart
            trend={trend}
            signal={signal}
            holderOptions={holderOptions}
            selectedHolderKey={effectiveSelectedHolderKey}
            onSelectedHolderKeyChange={setSelectedHolderKey}
          />

          <div className="rounded-xl border border-subtle bg-surface/75 p-4" data-testid="main-force-panel">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <Activity className="h-4 w-4 shrink-0 text-primary" />
                <h4 className="text-sm font-semibold text-foreground">主力判断</h4>
              </div>
              <Badge variant={signal.variant} size="sm">{signal.source}</Badge>
            </div>

            <div className="mb-2 text-xs font-semibold text-secondary-text">主力指标</div>
            <div className="space-y-2 text-sm">
              {mainMetricRows.map((row) => (
                <div key={row.label} className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-start gap-3">
                  <span className="text-muted-text">{row.label}</span>
                  <span className={`min-w-0 break-words text-right font-semibold tabular-nums ${getValueToneClass(row.tone)}`}>
                    {row.value}
                  </span>
                </div>
              ))}
            </div>

            <div className="mb-2 mt-4 border-t border-subtle pt-3 text-xs font-semibold text-secondary-text">基础指标</div>
            <div className="space-y-2 text-sm">
              {baseMetricRows.map((row) => (
                <div key={row.label} className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-start gap-3">
                  <span className="text-muted-text">{row.label}</span>
                  <span className={`min-w-0 break-words text-right font-semibold tabular-nums ${getValueToneClass(row.tone)}`}>
                    {row.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
};

export const IndicatorAnalysisView: React.FC<IndicatorAnalysisViewProps> = ({
  stockCode,
  stockName,
  onClose,
  variant = 'page',
}) => {
  const [selectedPeriod, setSelectedPeriod] = useState<KLinePeriod>('daily');
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [isHoverPinned, setIsHoverPinned] = useState(false);
  const [timelineZoom, setTimelineZoom] = useState(0);
  const [windowStart, setWindowStart] = useState(0);
  const [chartMenu, setChartMenu] = useState<ChartMenuState | null>(null);
  const [tooltipPositions, setTooltipPositions] = useState<TooltipPositions>({});
  const [historyCache, setHistoryCache] = useState<HistoryCache>(() => ({
    daily: createHistoryState(stockCode, 'daily'),
  }));
  const oneMinuteRefreshInFlightRef = useRef(false);
  const dailyCutoffDateRef = useRef<string | null>(null);
  const selectedRefreshTokenRef = useRef<string | null>(null);
  const marketKind = useMemo(() => getMarketKind(stockCode), [stockCode]);

  const resetTooltipPositions = useCallback(() => {
    setTooltipPositions((current) => (Object.keys(current).length === 0 ? current : {}));
  }, []);

  const setTooltipPosition = useCallback((key: TooltipPlacementKey, position: TooltipPosition) => {
    setTooltipPositions((current) => ({
      ...current,
      [key]: position,
    }));
  }, []);

  const handlePeriodChange = useCallback((period: KLinePeriod) => {
    resetTooltipPositions();
    selectedRefreshTokenRef.current = null;
    setSelectedPeriod(period);
  }, [resetTooltipPositions]);

  useEffect(() => {
    setSelectedPeriod('daily');
    setHoveredIndex(null);
    setIsHoverPinned(false);
    setTimelineZoom(0);
    setWindowStart(0);
    setChartMenu(null);
    resetTooltipPositions();
  }, [resetTooltipPositions, stockCode]);

  useEffect(() => {
    setHoveredIndex(null);
    setIsHoverPinned(false);
    setChartMenu(null);
    resetTooltipPositions();
  }, [resetTooltipPositions, selectedPeriod]);

  useEffect(() => {
    if (!chartMenu) {
      return undefined;
    }
    const closeMenu = () => setChartMenu(null);
    window.addEventListener('click', closeMenu);
    window.addEventListener('keydown', closeMenu);
    return () => {
      window.removeEventListener('click', closeMenu);
      window.removeEventListener('keydown', closeMenu);
    };
  }, [chartMenu]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return;
      }
      if (isHoverPinned) {
        event.preventDefault();
        resetTooltipPositions();
        setIsHoverPinned(false);
        setHoveredIndex(null);
        return;
      }
      if (onClose) {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isHoverPinned, onClose, resetTooltipPositions]);

  useEffect(() => {
    let ignore = false;
    dailyCutoffDateRef.current = null;
    selectedRefreshTokenRef.current = null;
    setHistoryCache({ daily: createHistoryState(stockCode, 'daily') });

    const loadInitialData = async () => {
      const dailyMeta = getPeriodMeta('daily');
      const [historyResult, quoteResult, metricsResult] = await Promise.allSettled([
        stocksApi.getHistory(stockCode, dailyMeta.days, 'daily'),
        stocksApi.getQuote(stockCode),
        stocksApi.getIndicatorMetrics(stockCode),
      ]);

      if (ignore) {
        return;
      }

      const historyResponse = historyResult.status === 'fulfilled' ? historyResult.value : null;
      const quoteResponse = quoteResult.status === 'fulfilled' ? quoteResult.value : null;
      const metricsResponse = metricsResult.status === 'fulfilled' ? metricsResult.value : null;
      const dailyHistory = historyResponse?.data ?? [];
      const dailyCutoffDate = getLatestHistoryDate(dailyHistory);
      dailyCutoffDateRef.current = dailyCutoffDate;
      const metricsError = metricsResult.status === 'rejected'
        ? metricsResult.reason instanceof Error
          ? metricsResult.reason.message
          : '主力筹码数据加载失败'
        : null;

      const dailyState = createHistoryState(stockCode, 'daily', {
        history: dailyHistory,
        quote: quoteResponse,
        metrics: metricsResponse,
        metricsError,
        isLoading: false,
        error: historyResult.status === 'rejected'
          ? historyResult.reason instanceof Error
            ? historyResult.reason.message
            : '指标数据加载失败'
          : null,
      });

      setHistoryCache({ daily: dailyState });

      INTRADAY_PERIOD_OPTIONS.forEach((option) => {
        setHistoryCache((current) => ({
          ...current,
          [option.value]: createHistoryState(stockCode, option.value, {
            quote: current.daily?.quote ?? quoteResponse,
            metrics: current.daily?.metrics ?? metricsResponse,
            metricsError: current.daily?.metricsError ?? metricsError,
          }),
        }));

        void stocksApi.getHistory(stockCode, option.days, option.value)
          .then((periodResponse) => {
            if (ignore) {
              return;
            }
            setHistoryCache((current) => {
              const currentDaily = current.daily;
              if (currentDaily?.stockCode !== stockCode) {
                return current;
              }
              const cutoffDate = getLatestHistoryDate(currentDaily.history) ?? dailyCutoffDate;
              const normalizedHistory = normalizeHistoryForPeriod(
                periodResponse.data,
                option.value,
                marketKind,
                cutoffDate,
              );
              return {
                ...current,
                [option.value]: createHistoryState(stockCode, option.value, {
                  history: normalizedHistory,
                  quote: currentDaily.quote,
                  metrics: currentDaily.metrics,
                  metricsError: currentDaily.metricsError,
                  isLoading: false,
                  error: null,
                }),
              };
            });
          })
          .catch((err: unknown) => {
            if (ignore) {
              return;
            }
            setHistoryCache((current) => {
              const currentState = current[option.value];
              if (currentState?.stockCode !== stockCode) {
                return current;
              }
              return {
                ...current,
                [option.value]: {
                  ...currentState,
                  isLoading: false,
                  error: err instanceof Error ? err.message : '指标数据加载失败',
                },
              };
            });
          });
      });
    };

    void loadInitialData().catch((err: unknown) => {
      if (!ignore) {
        setHistoryCache({
          daily: createHistoryState(stockCode, 'daily', {
            metricsError: err instanceof Error ? err.message : '主力筹码数据加载失败',
            isLoading: false,
            error: err instanceof Error ? err.message : '指标数据加载失败',
          }),
        });
      }
    });

    return () => {
      ignore = true;
    };
  }, [marketKind, stockCode]);

  const selectedCachedState = historyCache[selectedPeriod];
  const dailyCachedState = historyCache.daily;
  const displayState = selectedCachedState?.stockCode === stockCode && selectedCachedState.history.length > 0
    ? selectedCachedState
    : dailyCachedState?.stockCode === stockCode && dailyCachedState.history.length > 0
      ? dailyCachedState
      : selectedCachedState?.stockCode === stockCode
        ? selectedCachedState
        : dailyCachedState?.stockCode === stockCode
          ? dailyCachedState
          : Object.values(historyCache).find((state) => state?.stockCode === stockCode);
  const effectivePeriod = displayState?.period ?? selectedPeriod;
  const periodMeta = getPeriodMeta(effectivePeriod);
  const isLoading = !displayState || (displayState.isLoading && displayState.history.length === 0);
  const error = displayState?.period === selectedPeriod ? displayState.error : null;
  const history = displayState?.history ?? EMPTY_HISTORY;
  const quote = displayState?.quote ?? dailyCachedState?.quote ?? null;
  const metrics = displayState?.metrics ?? dailyCachedState?.metrics ?? null;
  const metricsError = displayState?.metricsError ?? dailyCachedState?.metricsError ?? null;

  useEffect(() => {
    if (selectedPeriod === 'daily' || !selectedCachedState || selectedCachedState.isLoading) {
      return undefined;
    }

    const refreshToken = `${stockCode}:${selectedPeriod}`;
    if (selectedRefreshTokenRef.current === refreshToken) {
      return undefined;
    }
    selectedRefreshTokenRef.current = refreshToken;

    let ignore = false;
    const selectedMeta = getPeriodMeta(selectedPeriod);
    stocksApi.getHistory(stockCode, selectedMeta.days, selectedPeriod)
      .then((periodResponse) => {
        if (ignore) {
          return;
        }
        setHistoryCache((current) => {
          const currentState = current[selectedPeriod];
          if (currentState?.stockCode !== stockCode) {
            return current;
          }
          const cutoffDate = getLatestHistoryDate(current.daily?.history ?? []) ?? dailyCutoffDateRef.current;
          const normalizedHistory = normalizeHistoryForPeriod(
            periodResponse.data,
            selectedPeriod,
            marketKind,
            cutoffDate,
          );
          return {
            ...current,
            [selectedPeriod]: {
              ...currentState,
              history: normalizedHistory,
              isLoading: false,
              error: null,
            },
          };
        });
      })
      .catch((err: unknown) => {
        if (ignore) {
          return;
        }
        setHistoryCache((current) => {
          const currentState = current[selectedPeriod];
          if (currentState?.stockCode !== stockCode) {
            return current;
          }
          return {
            ...current,
            [selectedPeriod]: {
              ...currentState,
              isLoading: false,
              error: err instanceof Error ? err.message : '指标数据刷新失败',
            },
          };
        });
      });

    return () => {
      ignore = true;
    };
  }, [marketKind, selectedCachedState, selectedPeriod, stockCode]);

  useEffect(() => {
    if (selectedPeriod !== '1m' || !selectedCachedState || selectedCachedState.isLoading) {
      return undefined;
    }

    let ignore = false;
    const oneMinuteMeta = getPeriodMeta('1m');

    const refreshOneMinuteData = async () => {
      if (oneMinuteRefreshInFlightRef.current) {
        return;
      }

      oneMinuteRefreshInFlightRef.current = true;
      try {
        const [historyResult, quoteResult] = await Promise.allSettled([
          stocksApi.getHistory(stockCode, oneMinuteMeta.days, '1m'),
          stocksApi.getQuote(stockCode),
        ]);

        if (ignore) {
          return;
        }

        setHistoryCache((current) => {
          const currentState = current['1m'];
          if (currentState?.stockCode !== stockCode) {
            return current;
          }

          const cutoffDate = getLatestHistoryDate(current.daily?.history ?? []) ?? dailyCutoffDateRef.current;
          const nextHistory = historyResult.status === 'fulfilled'
            ? normalizeHistoryForPeriod(historyResult.value.data, '1m', marketKind, cutoffDate)
            : currentState.history;
          const nextQuote = quoteResult.status === 'fulfilled'
            ? quoteResult.value
            : currentState.quote;
          const historyError = historyResult.status === 'rejected'
            ? historyResult.reason instanceof Error
              ? historyResult.reason.message
              : '指标数据刷新失败'
            : null;

          return {
            ...current,
            '1m': {
              ...currentState,
              history: nextHistory,
              quote: nextQuote,
              error: nextHistory.length > 0 ? null : historyError,
            },
          };
        });
      } finally {
        oneMinuteRefreshInFlightRef.current = false;
      }
    };

    const intervalId = window.setInterval(() => {
      void refreshOneMinuteData();
    }, ONE_MINUTE_REFRESH_MS);

    return () => {
      ignore = true;
      oneMinuteRefreshInFlightRef.current = false;
      window.clearInterval(intervalId);
    };
  }, [marketKind, selectedCachedState, selectedPeriod, stockCode]);

  useEffect(() => {
    if (isLoading || selectedPeriod === '1m') {
      return undefined;
    }

    let ignore = false;
    const refreshQuote = async () => {
      try {
        const nextQuote = await stocksApi.getQuote(stockCode);
        if (ignore) {
          return;
        }
        setHistoryCache((current) => {
          const next: HistoryCache = { ...current };
          KLINE_PERIOD_OPTIONS.forEach((option) => {
            const state = next[option.value];
            if (state?.stockCode === stockCode) {
              next[option.value] = {
                ...state,
                quote: nextQuote,
              };
            }
          });
          return next;
        });
      } catch {
        // Quote refresh is a soft realtime enhancement; keep the last usable quote.
      }
    };

    const intervalId = window.setInterval(() => {
      void refreshQuote();
    }, ONE_MINUTE_REFRESH_MS);

    return () => {
      ignore = true;
      window.clearInterval(intervalId);
    };
  }, [isLoading, selectedPeriod, stockCode]);

  const timeMeta = useMemo(() => getTimeMeta(stockCode), [stockCode]);
  const points = useMemo(() => buildChartPoints(history), [history]);
  const visibleCount = useMemo(() => getVisiblePointCount(points.length, timelineZoom), [points.length, timelineZoom]);
  const maxWindowStart = Math.max(points.length - visibleCount, 0);
  const safeWindowStart = clamp(windowStart, 0, maxWindowStart);
  const visiblePoints = points.slice(safeWindowStart, safeWindowStart + visibleCount);
  const handleHoverIndexChange = useCallback((index: number | null) => {
    if (hoveredIndex !== index) {
      resetTooltipPositions();
    }
    setHoveredIndex(index);
  }, [hoveredIndex, resetTooltipPositions]);
  const handleWindowStartChange = useCallback((value: number) => {
    resetTooltipPositions();
    setWindowStart(value);
  }, [resetTooltipPositions]);
  const stepHoverIndex = useCallback((direction: HoverStepDirection) => {
    resetTooltipPositions();
    if (points.length === 0) {
      setHoveredIndex(null);
      setIsHoverPinned(false);
      return;
    }
    setHoveredIndex((current) => {
      const baseIndex = current !== null && current >= 0 && current < points.length
        ? current
        : points.length - 1;
      return clamp(baseIndex + direction, 0, points.length - 1);
    });
    setIsHoverPinned(true);
    setChartMenu(null);
  }, [points.length, resetTooltipPositions]);
  const setPriceTooltipPosition = useCallback((position: TooltipPosition) => {
    setTooltipPosition('price', position);
  }, [setTooltipPosition]);
  const setVolumeTooltipPosition = useCallback((position: TooltipPosition) => {
    setTooltipPosition('volume', position);
  }, [setTooltipPosition]);
  const setMomentumTooltipPosition = useCallback((position: TooltipPosition) => {
    setTooltipPosition('momentum', position);
  }, [setTooltipPosition]);
  const safeHoveredIndex = hoveredIndex !== null && hoveredIndex >= 0 && hoveredIndex < points.length ? hoveredIndex : null;
  const latest = points.at(-1);
  const displayVolume = quote?.volume ?? latest?.volume;
  const volumeRatio = displayVolume && latest?.volumeMa5 ? displayVolume / latest.volumeMa5 : undefined;
  const chipPoints = points;
  const requiresRealChipData = marketKind === 'cn';
  const estimatedChip: ChipDistributionMetrics | null = null;
  const visibleAnchorPoint = visiblePoints.at(-1) ?? latest;
  const selectedPoint = safeHoveredIndex !== null ? points[safeHoveredIndex] : visibleAnchorPoint;
  const baseChip = metrics?.chipDistribution ?? null;
  const chip = useMemo(() => pickChipSnapshot(baseChip, selectedPoint?.date), [baseChip, selectedPoint?.date]);
  const chipPoint = useMemo(
    () => (isChipForDate(chip, selectedPoint?.date) ? selectedPoint : findPointByDate(points, chip?.date) ?? selectedPoint ?? latest),
    [chip, latest, points, selectedPoint],
  );
  const mainChip: ChipDistributionMetrics | null = null;
  const modal = variant === 'modal';

  useEffect(() => {
    resetTooltipPositions();
    setWindowStart(maxWindowStart);
  }, [effectivePeriod, maxWindowStart, points.length, resetTooltipPositions, stockCode, visibleCount]);

  useEffect(() => {
    if (hoveredIndex === null) {
      setIsHoverPinned(false);
    }
  }, [hoveredIndex]);

  useEffect(() => {
    if (!isHoverPinned || points.length === 0) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
        return;
      }
      event.preventDefault();
      stepHoverIndex(event.key === 'ArrowLeft' ? -1 : 1);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isHoverPinned, points.length, stepHoverIndex]);

  const openChartMenu = (event: React.MouseEvent) => {
    event.preventDefault();
    setChartMenu({ x: event.clientX, y: event.clientY });
  };

  const adjustTimelineZoom = (delta: number) => {
    resetTooltipPositions();
    setTimelineZoom((current) => clamp(current + delta, -1, 4));
    setIsHoverPinned(false);
    setHoveredIndex(null);
    setChartMenu(null);
  };

  return (
    <div
      data-testid={modal ? 'indicator-analysis-modal' : 'indicator-analysis-page'}
      className={modal
        ? 'fixed inset-0 z-[70] flex items-center justify-center bg-background/75 p-2 backdrop-blur-sm md:p-5'
        : 'flex h-[calc(100vh-5rem)] min-h-0 w-full flex-col overflow-hidden px-3 pb-4 md:h-[calc(100vh-2rem)] md:px-4'}
      role={modal ? 'dialog' : 'region'}
      aria-modal={modal ? 'true' : undefined}
      aria-label="指标分析"
    >
      <div className={`glass-card flex min-h-0 w-full flex-col overflow-hidden shadow-2xl ${modal ? 'max-h-full max-w-7xl' : 'h-full'}`}>
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-subtle px-4 py-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-foreground">指标分析</h2>
              <Badge variant="info" size="sm">{stockCode}</Badge>
              <span className="truncate text-sm text-secondary-text">{stockName}</span>
            </div>
          </div>
          {onClose ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              aria-label={modal ? '关闭指标分析浮窗' : '返回自选'}
            >
              {modal ? <X className="h-4 w-4" /> : <ArrowLeft className="h-4 w-4" />}
            </Button>
          ) : null}
        </div>

        <div className={modal ? 'min-h-0 flex-1 overflow-y-auto p-4 md:p-5' : 'min-h-0 flex-1 overflow-hidden p-3 md:p-4'}>
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
            <div className="flex h-full min-h-0 flex-col gap-3">
              <section className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[minmax(0,1fr)_20rem] xl:items-start">
                <div className="grid min-w-0 content-start gap-2 xl:h-full xl:min-h-0">
                  <section
                    className="min-h-0 overflow-hidden"
                    aria-label="K线图区域"
                  >
                    <CandlestickChart
                      points={points}
                      visible={visiblePoints}
                      visibleStartIndex={safeWindowStart}
                      visibleCount={visibleCount}
                      safeWindowStart={safeWindowStart}
                      maxWindowStart={maxWindowStart}
                      hoveredIndex={safeHoveredIndex}
                      isHoverPinned={isHoverPinned}
                      onHoverIndexChange={handleHoverIndexChange}
                      onHoverPinnedChange={setIsHoverPinned}
                      onStepHoverIndex={stepHoverIndex}
                      onWindowStartChange={handleWindowStartChange}
                      onOpenChartMenu={openChartMenu}
                      tooltipPosition={tooltipPositions.price}
                      onTooltipPositionChange={setPriceTooltipPosition}
                      period={effectivePeriod}
                      periodLabel={periodMeta.label}
                      recentLabel={periodMeta.recentLabel}
                      sampleUnit={periodMeta.sampleUnit}
                      timeZoneLabel={timeMeta.klineLabel}
                      selectedPeriod={selectedPeriod}
                      onPeriodChange={handlePeriodChange}
                    />
                  </section>

                  <section
                    className="min-h-0 overflow-hidden"
                    aria-label="成交量图区域"
                  >
                    <VolumeActivityChart
                      points={points}
                      visible={visiblePoints}
                      visibleStartIndex={safeWindowStart}
                      hoveredIndex={safeHoveredIndex}
                      isHoverPinned={isHoverPinned}
                      onHoverIndexChange={handleHoverIndexChange}
                      onHoverPinnedChange={setIsHoverPinned}
                      onStepHoverIndex={stepHoverIndex}
                      onOpenChartMenu={openChartMenu}
                      tooltipPosition={tooltipPositions.volume}
                      onTooltipPositionChange={setVolumeTooltipPosition}
                      onTooltipPositionReset={resetTooltipPositions}
                      period={effectivePeriod}
                    />
                  </section>

                  <section
                    className="min-h-0 overflow-hidden"
                    aria-label="MACD图区域"
                  >
                    <MacdSignalChart
                      points={points}
                      visible={visiblePoints}
                      visibleStartIndex={safeWindowStart}
                      hoveredIndex={safeHoveredIndex}
                      isHoverPinned={isHoverPinned}
                      onHoverIndexChange={handleHoverIndexChange}
                      onHoverPinnedChange={setIsHoverPinned}
                      onStepHoverIndex={stepHoverIndex}
                      onOpenChartMenu={openChartMenu}
                      tooltipPosition={tooltipPositions.momentum}
                      onTooltipPositionChange={setMomentumTooltipPosition}
                      onTooltipPositionReset={resetTooltipPositions}
                      period={effectivePeriod}
                    />
                  </section>
                </div>

                <div className="flex min-h-0 flex-col gap-3 xl:h-full">
                  <IndicatorSidePanel
                    points={points}
                    chipPoints={chipPoints}
                    chipPoint={chipPoint}
                    chip={chip}
                    mainChip={mainChip}
                    requiresRealChipData={requiresRealChipData}
                    quote={quote}
                  />
                  {modal ? (
                    <MarketStructureStrip
                      points={points}
                      quote={quote}
                      metrics={metrics}
                      metricsError={metricsError}
                      estimatedChip={estimatedChip}
                      derivedVolumeRatio={volumeRatio}
                    />
                  ) : null}
                </div>
              </section>
            </div>
          )}
        </div>
      </div>
      {chartMenu ? (
        <div
          className="fixed z-[90] min-w-28 overflow-hidden rounded-md border py-1 text-sm shadow-2xl"
          style={{
            left: chartMenu.x,
            top: chartMenu.y,
            borderColor: TERMINAL_COLORS.redGrid,
            backgroundColor: TERMINAL_COLORS.panel,
            color: TERMINAL_COLORS.text,
          }}
          role="menu"
          aria-label="图表缩放菜单"
        >
          <button
            type="button"
            className="block w-full px-3 py-1.5 text-left hover:bg-white/10"
            onClick={() => adjustTimelineZoom(1)}
          >
            放大
          </button>
          <button
            type="button"
            className="block w-full px-3 py-1.5 text-left hover:bg-white/10"
            onClick={() => adjustTimelineZoom(-1)}
          >
            缩小
          </button>
          <button
            type="button"
            className="block w-full px-3 py-1.5 text-left hover:bg-white/10"
            onClick={() => {
              resetTooltipPositions();
              setTimelineZoom(0);
              setIsHoverPinned(false);
              setHoveredIndex(null);
              setChartMenu(null);
            }}
          >
            重置
          </button>
        </div>
      ) : null}
    </div>
  );
};

export const IndicatorAnalysisModal: React.FC<IndicatorAnalysisModalProps> = (props) => (
  <IndicatorAnalysisView {...props} variant="modal" />
);
