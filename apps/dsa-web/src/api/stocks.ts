import apiClient from './index';

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
  amount?: number | null;
  changePercent?: number | null;
};

export type StockHistoryResponse = {
  stockCode: string;
  stockName?: string | null;
  period: string;
  data: KLineData[];
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
  volumeRatio?: number | null;
  turnoverRate?: number | null;
  amplitude?: number | null;
  source?: string | null;
  updateTime?: string | null;
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
  chipStatus?: string | null;
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
  majorHolders: MajorHolder[];
  majorHolderStatus: string;
  sourceChain: Array<Record<string, unknown>>;
  errors: string[];
  updateTime?: string | null;
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
    amount: toNullableNumber(item.amount),
    changePercent: toNullableNumber(item.change_percent ?? item.changePercent),
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
    volumeRatio: toNullableNumber(item.volume_ratio ?? item.volumeRatio),
    turnoverRate: toNullableNumber(item.turnover_rate ?? item.turnoverRate),
    amplitude: toNullableNumber(item.amplitude),
    source: toNullableString(item.source),
    updateTime: typeof (item.update_time ?? item.updateTime) === 'string'
      ? String(item.update_time ?? item.updateTime)
      : null,
  };
}

function normalizeChipDistribution(item: unknown): ChipDistributionMetrics | null {
  if (!item || typeof item !== 'object') {
    return null;
  }
  const data = item as Record<string, unknown>;
  return {
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
    chipStatus: toNullableString(data.chip_status ?? data.chipStatus),
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
  async getQuote(stockCode: string): Promise<StockQuote> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/quote`,
    );
    return normalizeQuote(response.data, stockCode);
  },

  async getHistory(stockCode: string, days = 120, period = 'daily'): Promise<StockHistoryResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/history`,
      {
        params: { days, period },
      },
    );
    const data = response.data as {
      stock_code?: string;
      stock_name?: string | null;
      period?: string;
      data?: Array<Record<string, unknown>>;
    };
    return {
      stockCode: data.stock_code ?? stockCode,
      stockName: data.stock_name,
      period: data.period ?? period,
      data: (data.data ?? []).map(normalizeKLine).filter((item) => item.date),
    };
  },

  async getIndicatorMetrics(stockCode: string): Promise<StockIndicatorMetrics> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/stocks/${encodeURIComponent(stockCode)}/indicator-metrics`,
    );
    return normalizeIndicatorMetrics(response.data, stockCode);
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
