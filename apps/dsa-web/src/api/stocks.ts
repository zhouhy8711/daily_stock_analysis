import apiClient from './index';
import type { NewsIntelItem, NewsIntelResponse } from '../types/analysis';

const STOCK_HISTORY_TIMEOUT_MS = 75_000;
const STOCK_INDICATOR_METRICS_TIMEOUT_MS = 75_000;

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  codes: string[];
  items?: ExtractItem[];
  rawText?: string;
};

export type KLineData = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  afterHoursVolume?: number | null;
  amount?: number | null;
  changePercent?: number | null;
  volumeRatio?: number | null;
  turnoverRate?: number | null;
  peRatio?: number | null;
  totalMv?: number | null;
  circMv?: number | null;
  totalShares?: number | null;
  floatShares?: number | null;
  dataSource?: string | null;
  snapshotId?: string | null;
  snapshotTime?: string | null;
};

export type KLinePeriod = 'daily' | '1m' | '5m' | '15m' | '30m' | '60m';
export type StockDataPolicy = 'default' | 'cache_only' | 'snapshot_only' | 'db_only';

export type StockHistoryResponse = {
  stockCode: string;
  stockName?: string | null;
  period: KLinePeriod | string;
  data: KLineData[];
  dataSource?: string | null;
  snapshotId?: string | null;
  snapshotTime?: string | null;
  snapshotAgeSeconds?: number | null;
};

export type StockQuote = {
  stockCode: string;
  stockName?: string | null;
  currentPrice: number;
  change?: number | null;
  changePercent?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  prevClose?: number | null;
  volume?: number | null;
  amount?: number | null;
  afterHoursVolume?: number | null;
  afterHoursAmount?: number | null;
  volumeRatio?: number | null;
  turnoverRate?: number | null;
  amplitude?: number | null;
  peRatio?: number | null;
  totalMv?: number | null;
  circMv?: number | null;
  totalShares?: number | null;
  floatShares?: number | null;
  limitUpPrice?: number | null;
  limitDownPrice?: number | null;
  priceSpeed?: number | null;
  entrustRatio?: number | null;
  source?: string | null;
  updateTime?: string | null;
  snapshotId?: string | null;
  snapshotTime?: string | null;
  quoteTime?: string | null;
};

export type StockQuotesResponse = {
  items: StockQuote[];
  failedCodes: string[];
  updateTime?: string | null;
  snapshotId?: string | null;
  snapshotTime?: string | null;
  snapshotAgeSeconds?: number | null;
};

export type ChipDistributionPoint = {
  price: number;
  percent: number;
};

export type ChipDistributionMetrics = {
  code: string;
  date?: string | null;
  source?: string | null;
  profitRatio?: number | null;
  avgCost?: number | null;
  cost90Low?: number | null;
  cost90High?: number | null;
  concentration90?: number | null;
  cost70Low?: number | null;
  cost70High?: number | null;
  concentration70?: number | null;
  distribution: ChipDistributionPoint[];
  snapshots?: ChipDistributionMetrics[];
  chipStatus?: string | null;
};

export type CapitalFlowMetrics = {
  status: string;
  mainNetInflow?: number | null;
  mainNetInflowRatio?: number | null;
  inflow5d?: number | null;
  inflow10d?: number | null;
};

export type MajorHolder = {
  name: string;
  holderType?: string | null;
  shareType?: string | null;
  shares?: number | null;
  holdingRatio?: number | null;
  change?: string | null;
  changeRatio?: number | null;
  reportDate?: string | null;
  announceDate?: string | null;
  rank?: number | null;
  source?: string | null;
};

export type StockIndicatorMetrics = {
  stockCode: string;
  stockName?: string | null;
  chipDistribution?: ChipDistributionMetrics | null;
  capitalFlow?: CapitalFlowMetrics | null;
  majorHolders: MajorHolder[];
  majorHolderStatus: string;
  sourceChain: Array<Record<string, unknown>>;
  errors: string[];
  updateTime?: string | null;
};

export type StockIndicatorMetricsOptions = {
  dataPolicy?: StockDataPolicy;
  tradeDate?: string;
  days?: number;
};

function toNullableNumber(value: unknown): number | null {
  const numberValue = typeof value === 'number'
    ? value
    : typeof value === 'string' && value.trim()
      ? Number(value)
      : Number.NaN;
  return Number.isFinite(numberValue) ? numberValue : null;
}

function toNumber(value: unknown, fallback = 0): number {
  return toNullableNumber(value) ?? fallback;
}

function toNullableString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function normalizeKLine(item: Record<string, unknown>): KLineData {
  return {
    date: String(item.date ?? ''),
    open: toNumber(item.open),
    high: toNumber(item.high),
    low: toNumber(item.low),
    close: toNumber(item.close),
    volume: toNullableNumber(item.volume),
    afterHoursVolume: toNullableNumber(
      item.after_hours_volume
        ?? item.afterHoursVolume
        ?? item.post_market_volume
        ?? item.postMarketVolume,
    ),
    amount: toNullableNumber(item.amount),
    changePercent: toNullableNumber(item.change_percent ?? item.changePercent),
    volumeRatio: toNullableNumber(item.volume_ratio ?? item.volumeRatio),
    turnoverRate: toNullableNumber(item.turnover_rate ?? item.turnoverRate),
    peRatio: toNullableNumber(item.pe_ratio ?? item.peRatio),
    totalMv: toNullableNumber(item.total_mv ?? item.totalMv),
    circMv: toNullableNumber(item.circ_mv ?? item.circMv),
    totalShares: toNullableNumber(item.total_shares ?? item.totalShares),
    floatShares: toNullableNumber(item.float_shares ?? item.floatShares),
    dataSource: toNullableString(item.data_source ?? item.dataSource),
    snapshotId: toNullableString(item.snapshot_id ?? item.snapshotId),
    snapshotTime: toNullableString(item.snapshot_time ?? item.snapshotTime),
  };
}

function normalizeQuote(item: Record<string, unknown>, stockCode: string): StockQuote {
  return {
    stockCode: String(item.stock_code ?? item.stockCode ?? stockCode),
    stockName: typeof (item.stock_name ?? item.stockName) === 'string'
      ? String(item.stock_name ?? item.stockName)
      : null,
    currentPrice: toNumber(item.current_price ?? item.currentPrice),
    change: toNullableNumber(item.change),
    changePercent: toNullableNumber(item.change_percent ?? item.changePercent),
    open: toNullableNumber(item.open),
    high: toNullableNumber(item.high),
    low: toNullableNumber(item.low),
    prevClose: toNullableNumber(item.prev_close ?? item.prevClose),
    volume: toNullableNumber(item.volume),
    amount: toNullableNumber(item.amount),
    afterHoursVolume: toNullableNumber(item.after_hours_volume ?? item.afterHoursVolume),
    afterHoursAmount: toNullableNumber(item.after_hours_amount ?? item.afterHoursAmount),
    volumeRatio: toNullableNumber(item.volume_ratio ?? item.volumeRatio),
    turnoverRate: toNullableNumber(item.turnover_rate ?? item.turnoverRate),
    amplitude: toNullableNumber(item.amplitude),
    peRatio: toNullableNumber(item.pe_ratio ?? item.peRatio),
    totalMv: toNullableNumber(item.total_mv ?? item.totalMv),
    circMv: toNullableNumber(item.circ_mv ?? item.circMv),
    totalShares: toNullableNumber(item.total_shares ?? item.totalShares),
    floatShares: toNullableNumber(item.float_shares ?? item.floatShares),
    limitUpPrice: toNullableNumber(item.limit_up_price ?? item.limitUpPrice),
    limitDownPrice: toNullableNumber(item.limit_down_price ?? item.limitDownPrice),
    priceSpeed: toNullableNumber(item.price_speed ?? item.priceSpeed),
    entrustRatio: toNullableNumber(item.entrust_ratio ?? item.entrustRatio),
    source: toNullableString(item.source),
    updateTime: typeof (item.update_time ?? item.updateTime) === 'string'
      ? String(item.update_time ?? item.updateTime)
      : null,
    snapshotId: toNullableString(item.snapshot_id ?? item.snapshotId),
    snapshotTime: toNullableString(item.snapshot_time ?? item.snapshotTime),
    quoteTime: toNullableString(item.quote_time ?? item.quoteTime),
  };
}

function normalizeChipDistribution(item: unknown): ChipDistributionMetrics | null {
  if (!item || typeof item !== 'object') {
    return null;
  }
  const data = item as Record<string, unknown>;
  const rawDistribution = Array.isArray(data.distribution) ? data.distribution : [];
  const distribution = rawDistribution
    .map((rawPoint) => {
      if (!rawPoint || typeof rawPoint !== 'object') {
        return null;
      }
      const point = rawPoint as Record<string, unknown>;
      const price = toNullableNumber(point.price);
      const percent = toNullableNumber(point.percent ?? point.ratio);
      return price !== null && price > 0 && percent !== null && percent > 0
        ? { price, percent }
        : null;
    })
    .filter((point): point is ChipDistributionPoint => point !== null);
  const rawSnapshots = Array.isArray(data.snapshots) ? data.snapshots : [];
  const normalized: ChipDistributionMetrics = {
    code: String(data.code ?? ''),
    date: toNullableString(data.date),
    source: toNullableString(data.source),
    profitRatio: toNullableNumber(data.profit_ratio ?? data.profitRatio),
    avgCost: toNullableNumber(data.avg_cost ?? data.avgCost),
    cost90Low: toNullableNumber(data.cost_90_low ?? data.cost90Low),
    cost90High: toNullableNumber(data.cost_90_high ?? data.cost90High),
    concentration90: toNullableNumber(data.concentration_90 ?? data.concentration90),
    cost70Low: toNullableNumber(data.cost_70_low ?? data.cost70Low),
    cost70High: toNullableNumber(data.cost_70_high ?? data.cost70High),
    concentration70: toNullableNumber(data.concentration_70 ?? data.concentration70),
    distribution,
    chipStatus: toNullableString(data.chip_status ?? data.chipStatus),
  };
  normalized.snapshots = rawSnapshots
    .map(normalizeChipDistribution)
    .filter((snapshot): snapshot is ChipDistributionMetrics => snapshot !== null);
  return normalized;
}

function normalizeCapitalFlow(item: unknown): CapitalFlowMetrics | null {
  if (!item || typeof item !== 'object') {
    return null;
  }
  const data = item as Record<string, unknown>;
  return {
    status: String(data.status ?? 'not_supported'),
    mainNetInflow: toNullableNumber(data.main_net_inflow ?? data.mainNetInflow),
    mainNetInflowRatio: toNullableNumber(data.main_net_inflow_ratio ?? data.mainNetInflowRatio),
    inflow5d: toNullableNumber(data.inflow_5d ?? data.inflow5d),
    inflow10d: toNullableNumber(data.inflow_10d ?? data.inflow10d),
  };
}

function normalizeMajorHolder(item: Record<string, unknown>): MajorHolder | null {
  const name = toNullableString(item.name);
  if (!name) {
    return null;
  }
  return {
    name,
    holderType: toNullableString(item.holder_type ?? item.holderType),
    shareType: toNullableString(item.share_type ?? item.shareType),
    shares: toNullableNumber(item.shares),
    holdingRatio: toNullableNumber(item.holding_ratio ?? item.holdingRatio),
    change: toNullableString(item.change),
    changeRatio: toNullableNumber(item.change_ratio ?? item.changeRatio),
    reportDate: toNullableString(item.report_date ?? item.reportDate),
    announceDate: toNullableString(item.announce_date ?? item.announceDate),
    rank: toNullableNumber(item.rank),
    source: toNullableString(item.source),
  };
}

function normalizeIndicatorMetrics(item: Record<string, unknown>, stockCode: string): StockIndicatorMetrics {
  const rawHolders = Array.isArray(item.major_holders ?? item.majorHolders)
    ? item.major_holders ?? item.majorHolders
    : [];
  return {
    stockCode: String(item.stock_code ?? item.stockCode ?? stockCode),
    stockName: toNullableString(item.stock_name ?? item.stockName),
    chipDistribution: normalizeChipDistribution(item.chip_distribution ?? item.chipDistribution),
    capitalFlow: normalizeCapitalFlow(item.capital_flow ?? item.capitalFlow),
    majorHolders: (rawHolders as Array<Record<string, unknown>>)
      .map(normalizeMajorHolder)
      .filter((holder): holder is MajorHolder => holder !== null),
    majorHolderStatus: String(item.major_holder_status ?? item.majorHolderStatus ?? 'not_supported'),
    sourceChain: Array.isArray(item.source_chain ?? item.sourceChain)
      ? (item.source_chain ?? item.sourceChain) as Array<Record<string, unknown>>
      : [],
    errors: Array.isArray(item.errors) ? item.errors.map(String) : [],
    updateTime: toNullableString(item.update_time ?? item.updateTime),
  };
}

export const stocksApi = {
  async getQuote(stockCode: string, dataPolicy: StockDataPolicy = 'default'): Promise<StockQuote> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/quote`,
      { params: { data_policy: dataPolicy } },
    );
    return normalizeQuote(response.data, stockCode);
  },

  async getQuotes(stockCodes: string[], dataPolicy: StockDataPolicy = 'default'): Promise<StockQuotesResponse> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/stocks/quotes',
      { stock_codes: stockCodes },
      { params: { data_policy: dataPolicy } },
    );
    const rawItems = Array.isArray(response.data.items) ? response.data.items : [];
    const rawFailedCodes = response.data.failed_codes ?? response.data.failedCodes;
    return {
      items: (rawItems as Array<Record<string, unknown>>).map((item) => (
        normalizeQuote(item, String(item.stock_code ?? item.stockCode ?? ''))
      )),
      failedCodes: Array.isArray(rawFailedCodes)
        ? rawFailedCodes.map(String)
        : [],
      updateTime: toNullableString(response.data.update_time ?? response.data.updateTime),
      snapshotId: toNullableString(response.data.snapshot_id ?? response.data.snapshotId),
      snapshotTime: toNullableString(response.data.snapshot_time ?? response.data.snapshotTime),
      snapshotAgeSeconds: toNullableNumber(response.data.snapshot_age_seconds ?? response.data.snapshotAgeSeconds),
    };
  },

  async getHistory(
    stockCode: string,
    days = 120,
    period: KLinePeriod = 'daily',
    dataPolicy: StockDataPolicy = 'default',
  ): Promise<StockHistoryResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/history`,
      {
        params: { days, period, data_policy: dataPolicy },
        timeout: STOCK_HISTORY_TIMEOUT_MS,
      },
    );
    const data = response.data as {
      stock_code?: string;
      stock_name?: string | null;
      period?: string;
      data?: Array<Record<string, unknown>>;
      data_source?: string | null;
      snapshot_id?: string | null;
      snapshot_time?: string | null;
      snapshot_age_seconds?: number | null;
    };
    return {
      stockCode: data.stock_code ?? stockCode,
      stockName: data.stock_name,
      period: data.period ?? period,
      data: (data.data ?? []).map(normalizeKLine).filter((item) => item.date),
      dataSource: toNullableString(data.data_source ?? (data as Record<string, unknown>).dataSource),
      snapshotId: toNullableString(data.snapshot_id ?? (data as Record<string, unknown>).snapshotId),
      snapshotTime: toNullableString(data.snapshot_time ?? (data as Record<string, unknown>).snapshotTime),
      snapshotAgeSeconds: toNullableNumber(data.snapshot_age_seconds ?? (data as Record<string, unknown>).snapshotAgeSeconds),
    };
  },

  async getIndicatorMetrics(
    stockCode: string,
    options: StockIndicatorMetricsOptions = {},
  ): Promise<StockIndicatorMetrics> {
    const params: Record<string, string | number> = {};
    if (options.dataPolicy) {
      params.data_policy = options.dataPolicy;
    }
    if (options.tradeDate) {
      params.trade_date = options.tradeDate;
    }
    if (typeof options.days === 'number') {
      params.days = options.days;
    }
    const requestConfig = Object.keys(params).length > 0
      ? { params, timeout: STOCK_INDICATOR_METRICS_TIMEOUT_MS }
      : { timeout: STOCK_INDICATOR_METRICS_TIMEOUT_MS };
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/indicator-metrics`,
      requestConfig,
    );
    return normalizeIndicatorMetrics(response.data, stockCode);
  },

  async getRelatedNews(stockCode: string, limit = 8, refresh = false): Promise<NewsIntelResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/news`,
      {
        params: { limit, refresh },
      },
    );
    const rawItems = Array.isArray(response.data.items) ? response.data.items : [];
    return {
      total: toNumber(response.data.total),
      items: (rawItems as Array<Record<string, unknown>>).map((item): NewsIntelItem => ({
        title: String(item.title ?? ''),
        snippet: String(item.snippet ?? ''),
        url: String(item.url ?? ''),
      })).filter((item) => item.title && item.url),
    };
  },

  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { codes?: string[]; items?: ExtractItem[]; raw_text?: string };
    return {
      codes: data.codes ?? [],
      items: data.items,
      rawText: data.raw_text,
    };
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    throw new Error('请提供文件或粘贴文本');
  },
};
