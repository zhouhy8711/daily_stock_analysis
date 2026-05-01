import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, BarChart3, ChevronDown, ChevronLeft, ChevronRight, LineChart, Users, X } from 'lucide-react';
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

type ChartPoint = KLineData & {
  ma5?: number;
  ma10?: number;
  ma20?: number;
  volumeMa5?: number;
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
const MAX_VISIBLE_KLINE_POINTS = 80;
const ONE_MINUTE_REFRESH_MS = 10_000;
const LINE_COLORS = {
  close: '#e5e7eb',
  avgCost: '#f59e0b',
  force: '#22d3ee',
  profit: '#a855f7',
  ma5: '#f59e0b',
  ma10: '#22d3ee',
  ma20: '#a855f7',
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

function formatHolderLabel(holder: MajorHolder): string {
  const ratio = formatPlainPct(holder.holdingRatio);
  return ratio === '--' ? holder.name : `${holder.name} ${ratio}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
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

function weightedQuantile(
  items: Array<{ price: number; weight: number }>,
  percentile: number,
): number | undefined {
  if (items.length === 0) {
    return undefined;
  }
  const totalWeight = items.reduce((sum, item) => sum + item.weight, 0);
  if (totalWeight <= 0) {
    return undefined;
  }
  const target = totalWeight * percentile;
  let cumulative = 0;
  for (const item of items) {
    cumulative += item.weight;
    if (cumulative >= target) {
      return item.price;
    }
  }
  return items.at(-1)?.price;
}

function buildEstimatedChipDistribution(
  points: ChartPoint[],
  currentPrice?: number | null,
): ChipDistributionMetrics | null {
  if (typeof currentPrice !== 'number' || Number.isNaN(currentPrice) || currentPrice <= 0) {
    return null;
  }

  const weightedPrices = points
    .slice(-120)
    .map((point) => ({
      price: point.close,
      weight: point.volume ?? 0,
    }))
    .filter((item) => item.price > 0 && item.weight > 0)
    .sort((a, b) => a.price - b.price);

  const totalWeight = weightedPrices.reduce((sum, item) => sum + item.weight, 0);
  if (weightedPrices.length === 0 || totalWeight <= 0) {
    return null;
  }

  const avgCost = weightedPrices.reduce((sum, item) => sum + item.price * item.weight, 0) / totalWeight;
  const profitWeight = weightedPrices
    .filter((item) => item.price <= currentPrice)
    .reduce((sum, item) => sum + item.weight, 0);
  const cost90Low = weightedQuantile(weightedPrices, 0.05);
  const cost90High = weightedQuantile(weightedPrices, 0.95);
  const cost70Low = weightedQuantile(weightedPrices, 0.15);
  const cost70High = weightedQuantile(weightedPrices, 0.85);
  const concentration = (low?: number, high?: number) => (
    typeof low === 'number' && typeof high === 'number' && low + high > 0
      ? (high - low) / (high + low)
      : null
  );

  return {
    code: '',
    date: points.at(-1)?.date,
    source: 'history_estimate',
    profitRatio: profitWeight / totalWeight,
    avgCost,
    cost90Low,
    cost90High,
    concentration90: concentration(cost90Low, cost90High),
    cost70Low,
    cost70High,
    concentration70: concentration(cost70Low, cost70High),
    chipStatus: '历史成交量估算',
  };
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
    const estimatedChip = buildEstimatedChipDistribution(points.slice(0, originalIndex + 1), point.close);
    const chip = adjustChipForHolderScope(
      originalIndex === points.length - 1 && currentChip ? currentChip : estimatedChip,
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

const ChartLegend: React.FC = () => (
  <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-text">
    <span className="inline-flex items-center gap-1"><i className="h-2 w-4 rounded bg-success" />阳线</span>
    <span className="inline-flex items-center gap-1"><i className="h-2 w-4 rounded bg-danger" />阴线</span>
    <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5" style={{ backgroundColor: LINE_COLORS.ma5 }} />MA5</span>
    <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5" style={{ backgroundColor: LINE_COLORS.ma10 }} />MA10</span>
    <span className="inline-flex items-center gap-1"><i className="h-0.5 w-5" style={{ backgroundColor: LINE_COLORS.ma20 }} />MA20</span>
  </div>
);

const CandlestickChart: React.FC<{
  points: ChartPoint[];
  period: KLinePeriod;
  periodLabel: string;
  recentLabel: string;
  sampleUnit: string;
  timeZoneLabel: string;
}> = ({ points, period, periodLabel, recentLabel, sampleUnit, timeZoneLabel }) => {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const width = 960;
  const priceTop = 24;
  const priceHeight = 310;
  const volumeTop = 362;
  const volumeHeight = 110;
  const visibleCount = Math.min(points.length, MAX_VISIBLE_KLINE_POINTS);
  const maxWindowStart = Math.max(points.length - visibleCount, 0);
  const [windowStart, setWindowStart] = useState(maxWindowStart);
  const safeWindowStart = clamp(windowStart, 0, maxWindowStart);
  const visibleStartIndex = safeWindowStart;
  const visible = points.slice(safeWindowStart, safeWindowStart + visibleCount);
  const canPan = maxWindowStart > 0;
  const visibleFirst = visible[0];
  const visibleLast = visible.at(-1);
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
  const hoveredPoint = hoveredIndex === null ? null : visible[hoveredIndex];
  const hoveredPrevious = hoveredIndex === null ? undefined : points[visibleStartIndex + hoveredIndex - 1];
  const hoveredX = hoveredIndex === null ? 0 : hoveredIndex * step;
  const tooltipWidth = 250;
  const tooltipHeight = 222;
  const tooltipX = hoveredX > width - tooltipWidth - 18 ? hoveredX - tooltipWidth - 14 : hoveredX + 14;
  const tooltipY = priceTop + 12;

  const shiftWindow = (direction: -1 | 1) => {
    setWindowStart((current) => clamp(current + direction * visibleCount, 0, maxWindowStart));
    setHoveredIndex(null);
  };

  return (
    <div className="rounded-xl border border-subtle bg-surface/75 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <ChartLegend />
        <span className="text-[11px] text-muted-text">
          {recentLabel} {visible.length} {sampleUnit} · {periodLabel} · {timeZoneLabel}
        </span>
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
                    {formatAxisDate(point.date, period)}
                  </text>
                ) : null}
              </g>
            );
          })}

          <path d={ma5Path} fill="none" stroke={LINE_COLORS.ma5} strokeWidth="2" />
          <path d={ma10Path} fill="none" stroke={LINE_COLORS.ma10} strokeWidth="2" />
          <path d={ma20Path} fill="none" stroke={LINE_COLORS.ma20} strokeWidth="2" />
          <line x1="0" y1={volumeTop} x2={width} y2={volumeTop} stroke="currentColor" className="text-border/70" />
          <text x="6" y={volumeTop + 16} className="fill-muted-text text-[10px]">成交量</text>

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
                height={volumeTop + volumeHeight - priceTop}
                fill="transparent"
                pointerEvents="all"
                tabIndex={0}
                role="graphics-symbol"
                aria-label={`${point.date} 指标明细`}
                onMouseEnter={() => setHoveredIndex(index)}
                onMouseLeave={() => setHoveredIndex((current) => (current === index ? null : current))}
                onFocus={() => setHoveredIndex(index)}
                onBlur={() => setHoveredIndex((current) => (current === index ? null : current))}
              />
            );
          })}

          {hoveredPoint ? (
            <g data-testid="indicator-chart-tooltip" pointerEvents="none">
              <line
                x1={hoveredX}
                y1={priceTop}
                x2={hoveredX}
                y2={volumeTop + volumeHeight}
                stroke="#e5e7eb"
                strokeDasharray="4 5"
                strokeWidth="1"
                opacity="0.45"
              />
              <rect
                x={hoveredX - Math.max(bodyWidth, 8) / 2}
                y={priceTop}
                width={Math.max(bodyWidth, 8)}
                height={volumeTop + volumeHeight - priceTop}
                fill="#38bdf8"
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
                <text x={tooltipWidth - 14} y="22" textAnchor="end" className={hoveredPoint.close >= hoveredPoint.open ? 'fill-success text-[11px]' : 'fill-danger text-[11px]'}>
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
                  <g key={`${leftLabel}-${rightLabel}`} transform={`translate(14, ${42 + rowIndex * 24})`}>
                    <text x="0" y="0" className="fill-muted-text text-[10px]">{leftLabel}</text>
                    <text x="58" y="0" className="fill-foreground text-[11px] font-medium tabular-nums">{leftValue}</text>
                    <text x="132" y="0" className="fill-muted-text text-[10px]">{rightLabel}</text>
                    <text x="236" y="0" textAnchor="end" className="fill-foreground text-[11px] font-medium tabular-nums">{rightValue}</text>
                  </g>
                ))}
              </g>
            </g>
          ) : null}
        </svg>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-subtle/70 pt-3">
        <button
          type="button"
          aria-label="向前移动K线窗口"
          disabled={!canPan || safeWindowStart === 0}
          onClick={() => shiftWindow(-1)}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-subtle text-muted-text transition-colors hover:border-primary/60 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div className="min-w-[12rem] flex-1">
          <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-muted-text">
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
              setWindowStart(Number(event.currentTarget.value));
              setHoveredIndex(null);
            }}
            className="h-2 w-full cursor-grab accent-primary disabled:cursor-not-allowed disabled:opacity-40"
          />
        </div>
        <button
          type="button"
          aria-label="向后移动K线窗口"
          disabled={!canPan || safeWindowStart === maxWindowStart}
          onClick={() => shiftWindow(1)}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-subtle text-muted-text transition-colors hover:border-primary/60 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
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

export const IndicatorAnalysisModal: React.FC<IndicatorAnalysisModalProps> = ({
  stockCode,
  stockName,
  reportCurrentPrice,
  reportChangePct,
  onClose,
}) => {
  const [isKLineExpanded, setIsKLineExpanded] = useState(true);
  const [selectedPeriod, setSelectedPeriod] = useState<KLinePeriod>('daily');
  const [historyState, setHistoryState] = useState<HistoryState>({
    stockCode,
    period: 'daily',
    history: [],
    quote: null,
    metrics: null,
    metricsError: null,
    isLoading: true,
    error: null,
  });
  const oneMinuteRefreshInFlightRef = useRef(false);

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
    const periodMeta = getPeriodMeta(selectedPeriod);
    Promise.allSettled([
      stocksApi.getHistory(stockCode, periodMeta.days, selectedPeriod),
      stocksApi.getQuote(stockCode),
      stocksApi.getIndicatorMetrics(stockCode),
    ])
      .then(([historyResult, quoteResult, metricsResult]) => {
        if (!ignore) {
          const historyResponse = historyResult.status === 'fulfilled' ? historyResult.value : null;
          const quoteResponse = quoteResult.status === 'fulfilled' ? quoteResult.value : null;
          const metricsResponse = metricsResult.status === 'fulfilled' ? metricsResult.value : null;
          setHistoryState({
            stockCode,
            period: selectedPeriod,
            history: historyResponse?.data ?? [],
            quote: quoteResponse,
            metrics: metricsResponse,
            metricsError: metricsResult.status === 'rejected'
              ? metricsResult.reason instanceof Error
                ? metricsResult.reason.message
                : '主力筹码数据加载失败'
              : null,
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
            period: selectedPeriod,
            history: [],
            quote: null,
            metrics: null,
            metricsError: err instanceof Error ? err.message : '主力筹码数据加载失败',
            isLoading: false,
            error: err instanceof Error ? err.message : '指标数据加载失败',
          });
        }
      });
    return () => {
      ignore = true;
    };
  }, [stockCode, selectedPeriod]);

  const periodMeta = getPeriodMeta(selectedPeriod);
  const isCurrentState = historyState.stockCode === stockCode && historyState.period === selectedPeriod;
  const isLoading = !isCurrentState || historyState.isLoading;
  const error = isCurrentState ? historyState.error : null;
  const history = isCurrentState ? historyState.history : EMPTY_HISTORY;
  const quote = isCurrentState ? historyState.quote : null;
  const metrics = isCurrentState ? historyState.metrics : null;
  const metricsError = isCurrentState ? historyState.metricsError : null;

  useEffect(() => {
    if (selectedPeriod !== '1m' || !isCurrentState || isLoading) {
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

        setHistoryState((current) => {
          if (current.stockCode !== stockCode || current.period !== '1m') {
            return current;
          }

          const nextHistory = historyResult.status === 'fulfilled'
            ? historyResult.value.data
            : current.history;
          const nextQuote = quoteResult.status === 'fulfilled'
            ? quoteResult.value
            : current.quote;
          const historyError = historyResult.status === 'rejected'
            ? historyResult.reason instanceof Error
              ? historyResult.reason.message
              : '指标数据刷新失败'
            : null;

          return {
            ...current,
            history: nextHistory,
            quote: nextQuote,
            error: nextHistory.length > 0 ? null : historyError,
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
  }, [stockCode, selectedPeriod, isCurrentState, isLoading]);

  const timeMeta = useMemo(() => getTimeMeta(stockCode), [stockCode]);
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
  const estimatedChip = buildEstimatedChipDistribution(points, displayClose);
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
            <p className="mt-1 text-xs text-muted-text">日 K、分钟 K、成交量、均线、量能与关键价位</p>
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
              <div className="flex flex-wrap items-center gap-2 border-b border-subtle pb-3">
                <span className="text-xs font-medium text-muted-text">周期</span>
                <div className="inline-flex flex-wrap items-center gap-1 rounded-lg border border-subtle bg-surface/70 p-1" role="tablist" aria-label="K线周期">
                  {KLINE_PERIOD_OPTIONS.map((option) => {
                    const isSelected = selectedPeriod === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        role="tab"
                        aria-selected={isSelected}
                        onClick={() => setSelectedPeriod(option.value)}
                        className={`h-8 rounded-md px-3 text-xs font-medium transition-colors ${
                          isSelected
                            ? 'bg-primary text-primary-foreground shadow-sm'
                            : 'text-secondary-text hover:bg-surface-2 hover:text-foreground'
                        }`}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>

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
                <StatTile label={periodMeta.returnLabel} value={`${formatPct(return5)} / ${formatPct(return20)}`} tone={(return20 ?? 0) >= 0 ? 'success' : 'danger'} />
                <StatTile label={`行情更新时间 (${timeMeta.quoteLabel})`} value={formatDateTime(quote?.updateTime)} />
                <StatTile label="历史样本" value={`${points.length} ${periodMeta.sampleUnit}`} />
                <StatTile label="数据周期" value={`${periodMeta.label} · ${timeMeta.klineLabel}`} />
              </section>

              <MarketStructureStrip
                points={points}
                quote={quote}
                metrics={metrics}
                metricsError={metricsError}
                estimatedChip={estimatedChip}
                derivedVolumeRatio={volumeRatio}
              />

              <section className="rounded-xl border border-primary/25 bg-primary/5 px-4 py-3 shadow-[0_0_26px_rgba(34,211,238,0.06)]" aria-label="K线图">
                <button
                  type="button"
                  data-testid="kline-chart-toggle"
                  aria-expanded={isKLineExpanded}
                  aria-label={`${isKLineExpanded ? '收起' : '展开'}K线图`}
                  onClick={() => setIsKLineExpanded((value) => !value)}
                  className={`flex w-full items-center justify-between gap-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary/45 ${isKLineExpanded ? 'mb-3' : ''}`}
                >
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <BarChart3 className="h-4 w-4 shrink-0 text-primary" />
                    <h3 className="text-sm font-semibold text-foreground">K线图</h3>
                    {latest?.date ? <span className="font-mono text-[11px] text-muted-text">{latest.date}</span> : null}
                    <span className="text-[11px] text-muted-text">{timeMeta.klineLabel}</span>
                    <Badge variant="info" size="sm">{points.length} {periodMeta.sampleUnit}</Badge>
                  </div>
                  <ChevronDown className={`h-4 w-4 shrink-0 text-muted-text transition-transform ${isKLineExpanded ? 'rotate-180' : ''}`} />
                </button>

                {isKLineExpanded ? (
                  <section className="grid gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(18rem,0.8fr)]">
                    <CandlestickChart
                      key={`${selectedPeriod}-${points.length}-${points[0]?.date ?? ''}-${latest?.date ?? ''}`}
                      points={points}
                      period={selectedPeriod}
                      periodLabel={periodMeta.label}
                      recentLabel={periodMeta.recentLabel}
                      sampleUnit={periodMeta.sampleUnit}
                      timeZoneLabel={timeMeta.klineLabel}
                    />
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
                            <span className="text-muted-text">{periodMeta.supportLabel}</span>
                            <span className="font-semibold tabular-nums text-foreground">{formatNumber(support)}</span>
                          </div>
                          <div className="flex items-center justify-between gap-3">
                            <span className="text-muted-text">{periodMeta.resistanceLabel}</span>
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
                          {periodMeta.description}
                        </p>
                      </div>
                    </div>
                  </section>
                ) : null}
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
