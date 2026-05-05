import { generateUUID } from './uuid';
import type {
  RuleAggregateMethod,
  RuleCompareOperator,
  RuleOperator,
  RuleValueExpression,
} from '../types/rules';

export const RULE_METRIC_DRAFT_STORAGE_KEY = 'dsa.ruleMetricDraft.v1';

export type RuleMetricDraftItem = {
  id: string;
  key: string;
  label: string;
  value?: number | null;
  unit?: string | null;
  date?: string | null;
  operator?: RuleOperator;
  offset?: number;
  right?: RuleValueExpression;
  compare?: RuleCompareOperator;
  lookback?: number;
  minCount?: number;
  addedAt: string;
};

export type RuleMetricDraft = {
  version: 1;
  stockCode?: string;
  stockName?: string;
  items: RuleMetricDraftItem[];
};

export type RuleMetricDraftInput = {
  key: string;
  label: string;
  value?: number | null;
  unit?: string | null;
  date?: string | null;
  stockCode?: string;
  stockName?: string;
};

export type RuleMetricDraftItemPatch = Partial<Omit<RuleMetricDraftItem, 'id' | 'addedAt'>>;

function canUseLocalStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function emptyDraft(stockCode?: string, stockName?: string): RuleMetricDraft {
  return {
    version: 1,
    stockCode,
    stockName,
    items: [],
  };
}

function toFiniteNumber(value: unknown, fallback?: number): number | undefined {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeValueExpression(raw: unknown): RuleValueExpression | undefined {
  if (!raw || typeof raw !== 'object') {
    return undefined;
  }
  const item = raw as Record<string, unknown>;
  const type = String(item.type || 'literal');
  if (type === 'metric') {
    return {
      type: 'metric',
      metric: String(item.metric || 'close'),
      offset: toFiniteNumber(item.offset, 0),
      multiplier: item.multiplier == null ? undefined : toFiniteNumber(item.multiplier, 1),
    };
  }
  if (type === 'aggregate') {
    return {
      type: 'aggregate',
      metric: String(item.metric || 'close'),
      method: String(item.method || 'avg') as RuleAggregateMethod,
      window: Math.max(1, toFiniteNumber(item.window, 5) ?? 5),
      offset: toFiniteNumber(item.offset, 1),
      multiplier: item.multiplier == null ? undefined : toFiniteNumber(item.multiplier, 1),
    };
  }
  if (type === 'range') {
    return {
      type: 'range',
      min: normalizeValueExpression(item.min) ?? { type: 'literal', value: 0 },
      max: normalizeValueExpression(item.max) ?? { type: 'literal', value: 10 },
    };
  }
  return {
    type: 'literal',
    value: toFiniteNumber(item.value, 0) ?? 0,
    multiplier: item.multiplier == null ? undefined : toFiniteNumber(item.multiplier, 1),
  };
}

function normalizeDraft(raw: unknown): RuleMetricDraft | null {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const item = raw as Partial<RuleMetricDraft>;
  const items: RuleMetricDraftItem[] = [];
  if (Array.isArray(item.items)) {
    item.items.forEach((draftItem) => {
      if (!draftItem || typeof draftItem !== 'object') {
        return;
      }
      const candidate = draftItem as Partial<RuleMetricDraftItem>;
      if (!candidate.key || !candidate.label) {
        return;
      }
      items.push({
        id: String(candidate.id || `metric-${generateUUID()}`),
        key: String(candidate.key),
        label: String(candidate.label),
        value: typeof candidate.value === 'number' && Number.isFinite(candidate.value) ? candidate.value : null,
        unit: candidate.unit ?? null,
        date: candidate.date ?? null,
        operator: typeof candidate.operator === 'string' ? candidate.operator as RuleOperator : undefined,
        offset: toFiniteNumber(candidate.offset, 0),
        right: normalizeValueExpression(candidate.right),
        compare: typeof candidate.compare === 'string' ? candidate.compare as RuleCompareOperator : undefined,
        lookback: toFiniteNumber(candidate.lookback),
        minCount: toFiniteNumber(candidate.minCount ?? (candidate as Record<string, unknown>).min_count),
        addedAt: String(candidate.addedAt || new Date().toISOString()),
      });
    });
  }
  return {
    version: 1,
    stockCode: item.stockCode,
    stockName: item.stockName,
    items,
  };
}

export function readRuleMetricDraft(): RuleMetricDraft | null {
  if (!canUseLocalStorage()) {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(RULE_METRIC_DRAFT_STORAGE_KEY);
    return raw ? normalizeDraft(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

function writeRuleMetricDraft(draft: RuleMetricDraft): void {
  if (!canUseLocalStorage()) {
    return;
  }
  if (draft.items.length === 0) {
    window.localStorage.removeItem(RULE_METRIC_DRAFT_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(RULE_METRIC_DRAFT_STORAGE_KEY, JSON.stringify(draft));
}

export function addRuleMetricDraftItem(input: RuleMetricDraftInput): RuleMetricDraft {
  const existing = readRuleMetricDraft();
  const sameStock = !existing?.stockCode || !input.stockCode || existing.stockCode === input.stockCode;
  const draft = sameStock
    ? existing ?? emptyDraft(input.stockCode, input.stockName)
    : emptyDraft(input.stockCode, input.stockName);
  const nextItem: RuleMetricDraftItem = {
    id: `metric-${generateUUID()}`,
    key: input.key,
    label: input.label,
    value: typeof input.value === 'number' && Number.isFinite(input.value) ? input.value : null,
    unit: input.unit ?? null,
    date: input.date ?? null,
    addedAt: new Date().toISOString(),
  };
  const nextDraft: RuleMetricDraft = {
    version: 1,
    stockCode: input.stockCode ?? draft.stockCode,
    stockName: input.stockName ?? draft.stockName,
    items: [
      ...draft.items.filter((item) => item.key !== input.key),
      nextItem,
    ],
  };
  writeRuleMetricDraft(nextDraft);
  return nextDraft;
}

export function updateRuleMetricDraftItem(itemId: string, patch: RuleMetricDraftItemPatch): RuleMetricDraft | null {
  const existing = readRuleMetricDraft();
  if (!existing) {
    return null;
  }
  const nextDraft: RuleMetricDraft = {
    ...existing,
    items: existing.items.map((item) => {
      if (item.id !== itemId) {
        return item;
      }
      return {
        ...item,
        ...patch,
        value: patch.value === undefined
          ? item.value
          : typeof patch.value === 'number' && Number.isFinite(patch.value)
            ? patch.value
            : null,
        unit: patch.unit === undefined ? item.unit : patch.unit ?? null,
        date: patch.date === undefined ? item.date : patch.date ?? null,
      };
    }),
  };
  writeRuleMetricDraft(nextDraft);
  return nextDraft.items.length > 0 ? nextDraft : null;
}

export function removeRuleMetricDraftItem(metricKey: string, stockCode?: string): RuleMetricDraft | null {
  const existing = readRuleMetricDraft();
  if (!existing) {
    return null;
  }
  if (stockCode && existing.stockCode && existing.stockCode !== stockCode) {
    return existing;
  }
  const nextDraft: RuleMetricDraft = {
    ...existing,
    items: existing.items.filter((item) => item.key !== metricKey),
  };
  writeRuleMetricDraft(nextDraft);
  return nextDraft.items.length > 0 ? nextDraft : null;
}

export function getRuleMetricDraftCount(stockCode?: string): number {
  const draft = readRuleMetricDraft();
  if (!draft) {
    return 0;
  }
  if (stockCode && draft.stockCode && draft.stockCode !== stockCode) {
    return 0;
  }
  return draft.items.length;
}

export function consumeRuleMetricDraft(): RuleMetricDraft | null {
  const draft = readRuleMetricDraft();
  if (canUseLocalStorage()) {
    window.localStorage.removeItem(RULE_METRIC_DRAFT_STORAGE_KEY);
  }
  return draft;
}
