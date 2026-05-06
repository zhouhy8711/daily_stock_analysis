import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { stocksApi, type StockQuote } from '../api/stocks';
import { systemConfigApi } from '../api/systemConfig';
import { getWatchlistLookupKeys, normalizeWatchlistCode } from '../utils/watchlist';

const DEFAULT_REFRESH_SECONDS = 60;
const MIN_REFRESH_SECONDS = 10;
const MAX_REFRESH_SECONDS = 3600;

type LiveMarketDataContextValue = {
  quotesByCode: Record<string, StockQuote>;
  lastUpdatedAt: string | null;
  errorsByCode: Record<string, string>;
  refreshIntervalSeconds: number;
  subscribeQuotes: (stockCodes: string[]) => () => void;
  upsertQuotes: (quotes: StockQuote[]) => void;
  refreshQuotes: (stockCodes?: string[]) => Promise<void>;
};

const defaultContext: LiveMarketDataContextValue = {
  quotesByCode: {},
  lastUpdatedAt: null,
  errorsByCode: {},
  refreshIntervalSeconds: DEFAULT_REFRESH_SECONDS,
  subscribeQuotes: () => () => undefined,
  upsertQuotes: () => undefined,
  refreshQuotes: async () => undefined,
};

const LiveMarketDataContext = createContext<LiveMarketDataContextValue>(defaultContext);

function clampRefreshSeconds(value: unknown): number {
  const parsed = typeof value === 'number'
    ? value
    : typeof value === 'string' && value.trim()
      ? Number(value)
      : DEFAULT_REFRESH_SECONDS;
  if (!Number.isFinite(parsed)) {
    return DEFAULT_REFRESH_SECONDS;
  }
  return Math.max(MIN_REFRESH_SECONDS, Math.min(Math.round(parsed), MAX_REFRESH_SECONDS));
}

function normalizeCodes(stockCodes: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const code of stockCodes) {
    const normalized = normalizeWatchlistCode(code || '');
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function quoteLookupKeys(stockCode: string): string[] {
  const keys = getWatchlistLookupKeys(stockCode);
  const normalized = normalizeWatchlistCode(stockCode);
  return normalized ? Array.from(new Set([normalized, ...keys])) : keys;
}

// eslint-disable-next-line react-refresh/only-export-components -- helper is shared by Home and indicator views
export function findLiveQuote(
  quotesByCode: Record<string, StockQuote>,
  stockCode?: string | null,
): StockQuote | null {
  if (!stockCode) {
    return null;
  }
  for (const key of quoteLookupKeys(stockCode)) {
    const quote = quotesByCode[key];
    if (quote) {
      return quote;
    }
  }
  return null;
}

export function LiveMarketDataProvider({ children }: { children: React.ReactNode }) {
  const [quotesByCode, setQuotesByCode] = useState<Record<string, StockQuote>>({});
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [errorsByCode, setErrorsByCode] = useState<Record<string, string>>({});
  const [refreshIntervalSeconds, setRefreshIntervalSeconds] = useState(DEFAULT_REFRESH_SECONDS);
  const subscriptionsRef = useRef(new Map<string, number>());
  const [subscribedCodes, setSubscribedCodes] = useState<string[]>([]);
  const inFlightRef = useRef(false);

  useEffect(() => {
    let ignore = false;
    systemConfigApi.getConfig(false)
      .then((config) => {
        if (ignore) {
          return;
        }
        const item = config.items.find((candidate) => candidate.key === 'INDICATOR_INTRADAY_REFRESH_SECONDS');
        setRefreshIntervalSeconds(clampRefreshSeconds(item?.value));
      })
      .catch(() => {
        if (!ignore) {
          setRefreshIntervalSeconds(DEFAULT_REFRESH_SECONDS);
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  const upsertQuotes = useCallback((quotes: StockQuote[]) => {
    if (quotes.length === 0) {
      return;
    }
    setQuotesByCode((current) => {
      const next = { ...current };
      for (const quote of quotes) {
        for (const key of quoteLookupKeys(quote.stockCode)) {
          next[key] = quote;
        }
      }
      return next;
    });
    setLastUpdatedAt(new Date().toISOString());
  }, []);

  const subscribeQuotes = useCallback((stockCodes: string[]) => {
    const normalizedCodes = normalizeCodes(stockCodes);
    if (normalizedCodes.length === 0) {
      return () => undefined;
    }

    normalizedCodes.forEach((code) => {
      subscriptionsRef.current.set(code, (subscriptionsRef.current.get(code) ?? 0) + 1);
    });
    setSubscribedCodes(Array.from(subscriptionsRef.current.keys()));

    return () => {
      normalizedCodes.forEach((code) => {
        const count = subscriptionsRef.current.get(code) ?? 0;
        if (count <= 1) {
          subscriptionsRef.current.delete(code);
        } else {
          subscriptionsRef.current.set(code, count - 1);
        }
      });
      setSubscribedCodes(Array.from(subscriptionsRef.current.keys()));
    };
  }, []);

  const refreshQuotes = useCallback(async (stockCodes?: string[]) => {
    const targetCodes = normalizeCodes(stockCodes ?? Array.from(subscriptionsRef.current.keys()));
    if (targetCodes.length === 0 || inFlightRef.current) {
      return;
    }

    inFlightRef.current = true;
    try {
      const response = await stocksApi.getQuotes(targetCodes, refreshIntervalSeconds);
      upsertQuotes(response.items);
      setErrorsByCode((current) => {
        const next = { ...current };
        targetCodes.forEach((code) => {
          delete next[code];
        });
        response.failedCodes.forEach((code) => {
          for (const key of quoteLookupKeys(code)) {
            next[key] = '实时行情刷新失败';
          }
        });
        return next;
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : '实时行情刷新失败';
      setErrorsByCode((current) => {
        const next = { ...current };
        targetCodes.forEach((code) => {
          next[code] = message;
        });
        return next;
      });
    } finally {
      inFlightRef.current = false;
    }
  }, [refreshIntervalSeconds, upsertQuotes]);

  useEffect(() => {
    if (subscribedCodes.length === 0) {
      return undefined;
    }

    void refreshQuotes(subscribedCodes);
    const intervalId = window.setInterval(() => {
      void refreshQuotes(subscribedCodes);
    }, refreshIntervalSeconds * 1000);

    return () => window.clearInterval(intervalId);
  }, [refreshIntervalSeconds, refreshQuotes, subscribedCodes]);

  const value = useMemo<LiveMarketDataContextValue>(() => ({
    quotesByCode,
    lastUpdatedAt,
    errorsByCode,
    refreshIntervalSeconds,
    subscribeQuotes,
    upsertQuotes,
    refreshQuotes,
  }), [
    errorsByCode,
    lastUpdatedAt,
    quotesByCode,
    refreshIntervalSeconds,
    refreshQuotes,
    subscribeQuotes,
    upsertQuotes,
  ]);

  return (
    <LiveMarketDataContext.Provider value={value}>
      {children}
    </LiveMarketDataContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- hook is co-located for context access
export function useLiveMarketData() {
  return useContext(LiveMarketDataContext);
}

// eslint-disable-next-line react-refresh/only-export-components -- hook is co-located for context access
export function useLiveQuotes(stockCodes: string[]) {
  const {
    quotesByCode,
    lastUpdatedAt,
    errorsByCode,
    refreshIntervalSeconds,
    refreshQuotes,
    subscribeQuotes,
  } = useLiveMarketData();
  const normalizedKey = useMemo(() => normalizeCodes(stockCodes).join('|'), [stockCodes]);
  const subscribed = useMemo(
    () => (normalizedKey ? normalizedKey.split('|').filter(Boolean) : []),
    [normalizedKey],
  );

  useEffect(() => {
    if (subscribed.length === 0) {
      return undefined;
    }
    return subscribeQuotes(subscribed);
  }, [subscribeQuotes, subscribed]);

  return {
    quotesByCode,
    lastUpdatedAt,
    errorsByCode,
    refreshIntervalSeconds,
    refreshQuotes,
  };
}

// eslint-disable-next-line react-refresh/only-export-components -- hook is co-located for context access
export function useLiveQuote(stockCode?: string | null) {
  const codes = useMemo(() => (stockCode ? [stockCode] : []), [stockCode]);
  const { quotesByCode, ...rest } = useLiveQuotes(codes);
  return {
    quote: findLiveQuote(quotesByCode, stockCode),
    quotesByCode,
    ...rest,
  };
}
