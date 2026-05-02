import type { HistoryItem } from '../types/analysis';
import type { StockIndexItem } from '../types/stockIndex';
import { normalizeQuery } from './normalizeQuery';

export type WatchlistDisplayItem = {
  code: string;
  name?: string;
};

export const normalizeWatchlistCode = (stockCode: string) => stockCode.trim().toUpperCase();

export const getWatchlistLookupKeys = (stockCode: string): string[] => {
  const code = normalizeWatchlistCode(stockCode);
  const keys = new Set<string>([code]);
  const [base] = code.split('.');
  if (base) {
    keys.add(base);
  }
  if (code.startsWith('HK') && code.length > 2) {
    keys.add(code.slice(2));
  }
  if (/^\d{5}$/.test(code)) {
    keys.add(`HK${code}`);
    keys.add(`${code}.HK`);
  }
  return Array.from(keys).filter(Boolean);
};

export const parseWatchlistValue = (value: string): string[] => {
  const seen = new Set<string>();
  return value
    .split(/[,\n\r\t ]+/)
    .map(normalizeWatchlistCode)
    .filter((code) => {
      if (!code || seen.has(code)) {
        return false;
      }
      seen.add(code);
      return true;
    });
};

export const getStockIndexSortCode = (item: StockIndexItem): string => item.displayCode || item.canonicalCode;

export const compareStockIndexById = (left: StockIndexItem, right: StockIndexItem): number => (
  getStockIndexSortCode(left).localeCompare(getStockIndexSortCode(right), 'en', { numeric: true })
);

export const isAllShareStock = (item: StockIndexItem): boolean => (
  item.active && item.assetType === 'stock' && (item.market === 'CN' || item.market === 'BSE')
);

export const matchesStockIndexQuery = (item: StockIndexItem, query: string): boolean => {
  if (!query) {
    return true;
  }

  const fields = [
    item.canonicalCode,
    item.displayCode,
    item.nameZh,
    item.nameEn ?? '',
    item.pinyinFull ?? '',
    item.pinyinAbbr ?? '',
    ...(item.aliases ?? []),
  ];
  return fields.some((field) => normalizeQuery(field).includes(query));
};

export const hasEquivalentWatchlistCode = (codes: string[], candidateCode: string): boolean => {
  const candidateKeys = new Set(getWatchlistLookupKeys(candidateCode));
  return codes.some((code) => getWatchlistLookupKeys(code).some((key) => candidateKeys.has(key)));
};

export const appendWatchlistCode = (codes: string[], candidateCode: string): string[] => {
  const normalized = normalizeWatchlistCode(candidateCode);
  if (!normalized || hasEquivalentWatchlistCode(codes, normalized)) {
    return codes;
  }
  return [...codes, normalized];
};

export function buildLatestHistoryByCode(historyItems: HistoryItem[]): Map<string, HistoryItem> {
  const map = new Map<string, HistoryItem>();
  for (const item of historyItems) {
    for (const key of getWatchlistLookupKeys(item.stockCode)) {
      if (!key || map.has(key)) {
        continue;
      }
      map.set(key, item);
    }
  }
  return map;
}

export function buildCurrentWatchlistItems(
  watchlistCodes: string[],
  historyItems: HistoryItem[],
): WatchlistDisplayItem[] {
  const latestHistoryByCode = buildLatestHistoryByCode(historyItems);
  const sourceCodes = watchlistCodes.length > 0
    ? watchlistCodes
    : historyItems.reduce<string[]>((codes, item) => {
      const code = normalizeWatchlistCode(item.stockCode);
      if (code && !codes.includes(code)) {
        codes.push(code);
      }
      return codes;
    }, []);

  return sourceCodes.map((code) => {
    const history = getWatchlistLookupKeys(code)
      .map((key) => latestHistoryByCode.get(key))
      .find((item) => item !== undefined);
    return {
      code: normalizeWatchlistCode(history?.stockCode || code),
      name: history?.stockName,
    };
  });
}
