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
    updateTime: typeof (item.update_time ?? item.updateTime) === 'string'
      ? String(item.update_time ?? item.updateTime)
      : null,
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
