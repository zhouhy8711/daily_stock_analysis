import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CalendarDays,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Filter,
  ListFilter,
  Maximize2,
  Minimize2,
  Play,
  Search,
  Table2,
  Trash2,
  X,
} from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { historyApi } from '../api/history';
import { rulesApi } from '../api/rules';
import { systemConfigApi } from '../api/systemConfig';
import { ApiErrorAlert, Badge, Button, ConfirmDialog, EmptyState, InlineAlert } from '../components/common';
import { IndicatorAnalysisModal } from '../components/report';
import { useStockIndex } from '../hooks/useStockIndex';
import type { RuleItem, RuleMatchItem, RuleMetricItem, RuleRunHistoryItem, RuleTargetScope } from '../types/rules';
import type { StockIndexItem } from '../types/stockIndex';
import { getOneYearAgoInShanghai, getRecentStartDate, getTodayInShanghai } from '../utils/format';
import { matchesIndustryQuery, normalizeIndustryQuery, UNCLASSIFIED_INDUSTRY_LABEL } from '../utils/industryFilter';
import {
  buildCurrentWatchlistItems,
  compareStockIndexById,
  isAllShareStock,
  parseWatchlistValue,
} from '../utils/watchlist';

const INPUT_CLASS =
  'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';
const TEXTAREA_CLASS =
  'input-surface input-focus-glow min-h-[112px] w-full resize-y rounded-xl border bg-transparent px-3 py-2 text-sm text-foreground transition-all placeholder:text-muted-text focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';
const WATCHLIST_HISTORY_LIMIT = 20;
const UNCLASSIFIED_INDUSTRY = UNCLASSIFIED_INDUSTRY_LABEL;
const DEFAULT_INDUSTRY = '半导体';

type StockListDisplayItem = {
  code: string;
  name?: string;
  industry?: string;
};

type StockListLineItem = StockListDisplayItem & {
  sourceCode: string;
  line: string;
  industryLabel: string;
};

type StockListIndustryOption = {
  name: string;
  count: number;
};

type BacktestTargetScope = RuleTargetScope | 'industry';
type ResultTab = 'logs' | 'results';
type LogLevel = 'info' | 'success' | 'warning' | 'error';

type ResultValueColumn = {
  key: string;
  ruleId: number;
  groupId: string;
  conditionId: string;
  metricKey: string;
  header: string;
  side: 'left' | 'right';
};

type RuleRunEventRow = {
  id: string;
  runId?: number;
  ruleId: number;
  ruleName?: string | null;
  stockCode: string;
  stockName?: string | null;
  eventDate: string;
  event: Record<string, unknown>;
  explanation?: string | null;
};

type RuleResultGroup = {
  key: string;
  ruleId: number;
  ruleName: string;
  rows: RuleRunEventRow[];
  columns: ResultValueColumn[];
  tableMinWidth: number;
};

type RunProgressState = {
  progress: number;
  stage: string;
};

type ExecutionLogEntry = {
  id: string;
  time: string;
  level: LogLevel;
  message: string;
};

type IndicatorAnalysisSelection = {
  stockCode: string;
  stockName: string;
  eventDate: string;
};

type BacktestRuntimeState = {
  runHistory: RuleRunHistoryItem[];
  selectedRun: RuleRunHistoryItem | null;
  displayRows: RuleRunEventRow[];
  activeResultTab: ResultTab;
  executionLogs: ExecutionLogEntry[];
  runProgressById: Record<number, RunProgressState>;
  isRunning: boolean;
  runError: ParsedApiError | null;
  runWarning: string | null;
};

type BacktestRuntimeListener = (state: BacktestRuntimeState) => void;

function createEmptyBacktestRuntimeState(): BacktestRuntimeState {
  return {
    runHistory: [],
    selectedRun: null,
    displayRows: [],
    activeResultTab: 'results',
    executionLogs: [],
    runProgressById: {},
    isRunning: false,
    runError: null,
    runWarning: null,
  };
}

let backtestRuntimeState = createEmptyBacktestRuntimeState();
const backtestRuntimeListeners = new Set<BacktestRuntimeListener>();

function cloneBacktestRuntimeState(state: BacktestRuntimeState = backtestRuntimeState): BacktestRuntimeState {
  return {
    ...state,
    runHistory: [...state.runHistory],
    displayRows: [...state.displayRows],
    executionLogs: [...state.executionLogs],
    runProgressById: { ...state.runProgressById },
  };
}

function hasBacktestRuntimeSession(state: BacktestRuntimeState = backtestRuntimeState): boolean {
  return (
    state.isRunning
    || state.runHistory.length > 0
    || state.displayRows.length > 0
    || state.executionLogs.length > 0
    || Object.keys(state.runProgressById).length > 0
    || state.runError !== null
    || state.runWarning !== null
  );
}

function updateBacktestRuntime(
  updater: (current: BacktestRuntimeState) => BacktestRuntimeState,
): void {
  backtestRuntimeState = updater(backtestRuntimeState);
  const snapshot = cloneBacktestRuntimeState();
  backtestRuntimeListeners.forEach((listener) => listener(snapshot));
}

function resolveStateAction<T>(current: T, action: React.SetStateAction<T>): T {
  return typeof action === 'function' ? (action as (previous: T) => T)(current) : action;
}

function normalizeStockCode(value: string): string {
  const normalizedValue = value.trim().toUpperCase();
  const strongCode = normalizedValue.match(
    /(?:SH|SZ|BJ)\d{6}|\d{6}\.(?:SH|SZ|SS|BJ)|HK\d{1,5}|\d{1,5}\.HK|\d{6}|\d{5}/,
  );
  const usCode = normalizedValue
    .split(/[\s,，;；]+/)
    .find((part) => /^[A-Z]{1,5}(?:\.US)?$/.test(part));
  const code = strongCode?.[0] ?? usCode ?? '';
  const hkPrefix = code.match(/^HK(\d{1,5})$/);
  if (hkPrefix) {
    return `HK${hkPrefix[1].padStart(5, '0')}`;
  }
  const hkSuffix = code.match(/^(\d{1,5})\.HK$/);
  if (hkSuffix) {
    return `${hkSuffix[1].padStart(5, '0')}.HK`;
  }
  return code;
}

function parseStockCodes(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(/[\n\r,，;；]+/)
    .map(normalizeStockCode)
    .filter((code) => {
      if (!code || seen.has(code)) {
        return false;
      }
      seen.add(code);
      return true;
    });
}

function getStockLookupKeys(code: string): string[] {
  const upperCode = code.trim().toUpperCase();
  const normalized = normalizeStockCode(upperCode);
  const keys = new Set<string>([upperCode, normalized]);
  const [base] = upperCode.split('.');

  if (base) keys.add(normalizeStockCode(base));

  const prefixedAShare = upperCode.match(/^(SH|SZ|BJ)(\d{6})$/);
  if (prefixedAShare) {
    keys.add(prefixedAShare[2]);
    keys.add(`${prefixedAShare[2]}.${prefixedAShare[1]}`);
  }

  const suffixedAShare = upperCode.match(/^(\d{6})\.(SH|SZ|SS|BJ)$/);
  if (suffixedAShare) {
    keys.add(suffixedAShare[1]);
    keys.add(`${suffixedAShare[2] === 'SS' ? 'SH' : suffixedAShare[2]}${suffixedAShare[1]}`);
  }

  if (/^\d{6}$/.test(upperCode)) {
    keys.add(`SH${upperCode}`);
    keys.add(`SZ${upperCode}`);
    keys.add(`BJ${upperCode}`);
    keys.add(`${upperCode}.SH`);
    keys.add(`${upperCode}.SZ`);
    keys.add(`${upperCode}.SS`);
    keys.add(`${upperCode}.BJ`);
  }

  const hkSuffix = upperCode.match(/^(\d{1,5})\.HK$/);
  if (hkSuffix) {
    const padded = hkSuffix[1].padStart(5, '0');
    keys.add(padded);
    keys.add(`HK${padded}`);
  }

  const usSuffix = upperCode.match(/^([A-Z]{1,5})\.US$/);
  if (usSuffix) keys.add(usSuffix[1]);

  return Array.from(keys).filter(Boolean);
}

function getStockDisplayCode(item: StockIndexItem): string {
  return normalizeStockCode(item.displayCode || item.canonicalCode);
}

function buildStockLookup(
  stockIndex: StockIndexItem[],
  preferredItems: StockListDisplayItem[] = [],
): Map<string, StockListDisplayItem> {
  const lookup = new Map<string, StockListDisplayItem>();

  for (const item of stockIndex) {
    const displayItem = {
      code: getStockDisplayCode(item),
      name: item.nameZh || item.nameEn,
      industry: item.industry,
    };
    for (const key of getStockLookupKeys(item.canonicalCode).concat(getStockLookupKeys(item.displayCode))) {
      lookup.set(key, displayItem);
    }
  }

  for (const item of preferredItems) {
    if (!item.code) continue;
    const existing = getStockLookupKeys(item.code)
      .map((key) => lookup.get(key))
      .find((displayItem) => displayItem !== undefined);
    const displayItem = {
      code: item.code,
      name: item.name || existing?.name,
      industry: item.industry || existing?.industry,
    };
    for (const key of getStockLookupKeys(item.code)) {
      lookup.set(key, displayItem);
    }
  }

  return lookup;
}

function getDisplayItemForCode(code: string, lookup: Map<string, StockListDisplayItem>): StockListDisplayItem {
  for (const key of getStockLookupKeys(code)) {
    const item = lookup.get(key);
    if (item) return item;
  }
  return { code: normalizeStockCode(code) };
}

function getStockIndustryLabel(item: StockListDisplayItem): string {
  return item.industry?.trim() || UNCLASSIFIED_INDUSTRY;
}

function formatStockListLine(item: StockListDisplayItem): string {
  const industry = getStockIndustryLabel(item);
  return item.name ? `${industry} ${item.code} ${item.name}` : `${industry} ${item.code}`;
}

function formatStockListText(codes: string[], lookup: Map<string, StockListDisplayItem>): string {
  return codes
    .map((code) => getDisplayItemForCode(code, lookup))
    .map(formatStockListLine)
    .join('\n');
}

function buildStockListLineItems(
  codes: string[],
  lookup: Map<string, StockListDisplayItem>,
): StockListLineItem[] {
  return codes.map((sourceCode) => {
    const item = getDisplayItemForCode(sourceCode, lookup);
    return {
      ...item,
      sourceCode,
      line: formatStockListLine(item),
      industryLabel: getStockIndustryLabel(item),
    };
  });
}

function buildStockListIndustryOptions(items: StockListLineItem[]): StockListIndustryOption[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item.industryLabel, (counts.get(item.industryLabel) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => {
      if (left.name === UNCLASSIFIED_INDUSTRY) return 1;
      if (right.name === UNCLASSIFIED_INDUSTRY) return -1;
      return left.name.localeCompare(right.name, 'zh-Hans-CN');
    });
}

function stockIndexToDisplayItem(item: StockIndexItem): StockListDisplayItem {
  return {
    code: getStockDisplayCode(item),
    name: item.nameZh || item.nameEn,
    industry: item.industry,
  };
}

function buildAllAshareIndustryOptions(stockIndex: StockIndexItem[]): StockListIndustryOption[] {
  return buildStockListIndustryOptions(
    stockIndex
      .filter(isAllShareStock)
      .map((item) => ({
        ...stockIndexToDisplayItem(item),
        sourceCode: getStockDisplayCode(item),
        line: formatStockListLine(stockIndexToDisplayItem(item)),
        industryLabel: getStockIndustryLabel(stockIndexToDisplayItem(item)),
      })),
  );
}

function getAshareCodesByIndustry(stockIndex: StockIndexItem[], industry: string): string[] {
  return Array.from(new Set(stockIndex
    .filter(isAllShareStock)
    .filter((item) => getStockIndustryLabel(stockIndexToDisplayItem(item)) === industry)
    .sort(compareStockIndexById)
    .map(getStockDisplayCode)));
}

function filterStockListItems(
  items: StockListLineItem[],
  query: string,
  selectedIndustries: string[],
): StockListLineItem[] {
  const normalizedQuery = query.trim().toUpperCase();
  const selectedIndustrySet = new Set(selectedIndustries);
  return items.filter((item) => {
    const matchesIndustry = selectedIndustries.length === 0 || selectedIndustrySet.has(item.industryLabel);
    if (!matchesIndustry) return false;
    if (!normalizedQuery) return true;
    return item.code.toUpperCase().includes(normalizedQuery) || (item.name ?? '').toUpperCase().includes(normalizedQuery);
  });
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function formatNumber(value: unknown): string {
  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) return '--';
  if (Math.abs(numberValue) >= 1000) {
    return numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  return numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}

function formatDateTime(value?: string | null): string {
  if (!value) return '--';
  return value.slice(0, 16).replace('T', ' ');
}

function formatLogTime(value: string): string {
  return value.slice(11, 19);
}

function getIndicatorHistoryDays(eventDate: string): number {
  const eventTime = Date.parse(eventDate.slice(0, 10));
  if (!Number.isFinite(eventTime)) {
    return 365;
  }
  const daysFromEvent = Math.ceil((Date.now() - eventTime) / 86_400_000);
  return Math.max(120, Math.min(365, daysFromEvent + 45));
}

function formatRunStatus(status: string): string {
  if (status === 'completed') return '完成';
  if (status === 'failed') return '失败';
  if (status === 'running') return '运行中';
  if (status === 'partial') return '部分完成';
  return status;
}

function formatLogLevel(level: LogLevel): string {
  if (level === 'success') return '成功';
  if (level === 'warning') return '警告';
  if (level === 'error') return '错误';
  return '信息';
}

function getLogLevelClass(level: LogLevel): string {
  if (level === 'success') return 'border-success/35 bg-success/10 text-success';
  if (level === 'warning') return 'border-warning/35 bg-warning/10 text-warning';
  if (level === 'error') return 'border-danger/35 bg-danger/10 text-danger';
  return 'border-primary/35 bg-primary/10 text-primary';
}

function metricLabel(metrics: RuleMetricItem[], metricKey: string): string {
  return metrics.find((metric) => metric.key === metricKey)?.label ?? metricKey;
}

function metricUnit(metrics: RuleMetricItem[], metricKey: string): string | null {
  return metrics.find((metric) => metric.key === metricKey)?.unit ?? null;
}

function getEventDate(event: Record<string, unknown>): string {
  return typeof event.date === 'string' && event.date ? event.date : '--';
}

function getMatchedGroups(event: Record<string, unknown>): Record<string, unknown>[] {
  const groups = event.matched_groups ?? event.matchedGroups;
  return Array.isArray(groups) ? groups.map(asRecord) : [];
}

function getEventSnapshot(event: Record<string, unknown>): Record<string, unknown> {
  return asRecord(event.snapshot);
}

function buildConditionRows(event: Record<string, unknown>, metrics: RuleMetricItem[]) {
  return getMatchedGroups(event).flatMap((group) => {
    const conditions = Array.isArray(group.conditions) ? group.conditions.map(asRecord) : [];
    return conditions.map((condition) => {
      const metricKey = String(condition.left_metric ?? condition.leftMetric ?? '');
      const values = asRecord(condition.values);
      return {
        id: String(condition.id ?? ''),
        groupId: String(group.id ?? ''),
        metricKey,
        metric: metricLabel(metrics, metricKey),
        operator: String(condition.operator ?? ''),
        left: values.left,
        right: getRightConditionValue(values),
        explanation: typeof condition.explanation === 'string' ? condition.explanation : '',
      };
    });
  });
}

function getRightConditionValue(values: Record<string, unknown>): unknown {
  if (values.right != null) return values.right;
  if (values.threshold != null) return values.threshold;
  if (values.min != null || values.max != null) return [values.min, values.max];
  if (values.matched_count != null) return values.matched_count;
  return undefined;
}

function findConditionValues(event: Record<string, unknown>, column: ResultValueColumn): Record<string, unknown> | null {
  for (const group of getMatchedGroups(event)) {
    const groupId = String(group.id ?? '');
    if (column.groupId && groupId !== column.groupId) {
      continue;
    }
    const conditions = Array.isArray(group.conditions) ? group.conditions.map(asRecord) : [];
    for (const condition of conditions) {
      const metricKey = String(condition.left_metric ?? condition.leftMetric ?? '');
      const conditionId = String(condition.id ?? '');
      const matchesId = column.conditionId ? conditionId === column.conditionId : false;
      if (matchesId || (!column.conditionId && metricKey === column.metricKey)) {
        return asRecord(condition.values);
      }
    }
  }
  return null;
}

function getConditionColumnValue(row: RuleRunEventRow, column: ResultValueColumn): unknown {
  if (row.ruleId !== column.ruleId) {
    return undefined;
  }
  const conditionValues = findConditionValues(row.event, column);
  if (conditionValues && column.side === 'left') {
    return conditionValues.left;
  }
  if (conditionValues && column.side === 'right') {
    return getRightConditionValue(conditionValues);
  }
  return undefined;
}

function formatCompactVolume(value: unknown): string {
  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) return '--';
  const absValue = Math.abs(numberValue);
  if (absValue >= 100000000) {
    return `${(numberValue / 100000000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}亿`;
  }
  if (absValue >= 10000) {
    return `${(numberValue / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}万`;
  }
  return numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function formatPercentMetric(value: unknown): string {
  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) return '--';
  const percent = numberValue > 0 && numberValue <= 1 ? numberValue * 100 : numberValue;
  return `${percent.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%`;
}

function formatMetricValue(value: unknown, column: ResultValueColumn, metrics: RuleMetricItem[]): string {
  if (Array.isArray(value)) {
    return value.map((item) => formatMetricValue(item, column, metrics)).join(' - ');
  }
  const unit = metricUnit(metrics, column.metricKey);
  if (unit === '%') {
    return formatPercentMetric(value);
  }
  if (unit === '股') {
    return formatCompactVolume(value);
  }
  return formatNumber(value);
}

function getRightMetricKey(expr: unknown, fallbackMetricKey: string): string {
  const record = asRecord(expr);
  const metricKey = typeof record.metric === 'string' && record.metric ? record.metric : null;
  if (metricKey) return metricKey;
  return fallbackMetricKey;
}

function describeRightExpression(expr: unknown, fallbackLabel: string, metrics: RuleMetricItem[]): string {
  const record = asRecord(expr);
  const valueType = String(record.type ?? 'literal');
  if (valueType === 'aggregate') {
    const window = Math.max(1, Number(record.window ?? 1));
    const offset = Math.max(0, Number(record.offset ?? 0));
    const metricKey = String(record.metric ?? '');
    const methodLabel = {
      max: '最大值',
      min: '最小值',
      avg: '均值',
      sum: '求和',
      median: '中位数',
      std: '标准差',
    }[String(record.method ?? 'avg')] ?? '均值';
    const multiplier = Number(record.multiplier ?? 1);
    const multiplierText = Number.isFinite(multiplier) && multiplier !== 1 ? `*${formatNumber(multiplier)}` : '';
    return `${offset > 0 ? '前' : '近'}${window}期${metricLabel(metrics, metricKey)}${methodLabel}${multiplierText}`;
  }
  if (valueType === 'metric') {
    return metricLabel(metrics, String(record.metric ?? ''));
  }
  if (valueType === 'range') {
    return `${fallbackLabel} 区间`;
  }
  return `${fallbackLabel} 阈值`;
}

function buildResultValueColumns(rules: RuleItem[], metrics: RuleMetricItem[]): ResultValueColumn[] {
  const multipleRules = rules.length > 1;
  return rules.flatMap((rule) => {
    let conditionIndex = 0;
    return rule.definition.groups.flatMap((group) => group.conditions.flatMap((condition) => {
      conditionIndex += 1;
      const metricKey = condition.left.metric;
      const label = metricLabel(metrics, metricKey);
      const prefix = multipleRules ? `#${rule.id} ` : '';
      const baseKey = `${rule.id}:${group.id}:${condition.id || conditionIndex}`;
      return [
        {
          key: `${baseKey}:left`,
          ruleId: rule.id,
          groupId: group.id,
          conditionId: condition.id,
          metricKey,
          header: `${prefix}${label} 当前值`,
          side: 'left' as const,
        },
        {
          key: `${baseKey}:right`,
          ruleId: rule.id,
          groupId: group.id,
          conditionId: condition.id,
          metricKey: getRightMetricKey(condition.right, metricKey),
          header: `${prefix}${describeRightExpression(condition.right, label, metrics)}`,
          side: 'right' as const,
        },
      ];
    }));
  });
}

function buildFallbackResultValueColumns(rows: RuleRunEventRow[], metrics: RuleMetricItem[]): ResultValueColumn[] {
  const columns: ResultValueColumn[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    for (const condition of buildConditionRows(row.event, metrics)) {
      const baseKey = `${row.ruleId}:${condition.groupId}:${condition.id || condition.metricKey}`;
      if (seen.has(baseKey)) continue;
      seen.add(baseKey);
      columns.push({
        key: `${baseKey}:left`,
        ruleId: row.ruleId,
        groupId: condition.groupId,
        conditionId: condition.id,
        metricKey: condition.metricKey,
        header: `${condition.metric} 当前值`,
        side: 'left',
      });
      columns.push({
        key: `${baseKey}:right`,
        ruleId: row.ruleId,
        groupId: condition.groupId,
        conditionId: condition.id,
        metricKey: condition.metricKey,
        header: `${condition.metric} 右值`,
        side: 'right',
      });
    }
  }
  return columns;
}

function buildRuleResultGroups(
  rows: RuleRunEventRow[],
  resultRules: RuleItem[],
  allRules: RuleItem[],
  metrics: RuleMetricItem[],
): RuleResultGroup[] {
  const rowsByRule = new Map<number, RuleRunEventRow[]>();
  for (const row of rows) {
    rowsByRule.set(row.ruleId, [...(rowsByRule.get(row.ruleId) ?? []), row]);
  }

  const groups: RuleResultGroup[] = [];
  const includedRuleIds = new Set<number>();
  const addGroup = (ruleId: number, rule?: RuleItem) => {
    if (includedRuleIds.has(ruleId)) return;
    const groupRows = rowsByRule.get(ruleId) ?? [];
    const resolvedRule = rule ?? allRules.find((item) => item.id === ruleId);
    const columns = resolvedRule
      ? buildResultValueColumns([resolvedRule], metrics)
      : buildFallbackResultValueColumns(groupRows, metrics);
    const effectiveColumns = columns.length > 0 ? columns : buildFallbackResultValueColumns(groupRows, metrics);
    includedRuleIds.add(ruleId);
    groups.push({
      key: `rule-${ruleId}`,
      ruleId,
      ruleName: resolvedRule?.name ?? groupRows[0]?.ruleName ?? `规则 ${ruleId}`,
      rows: groupRows,
      columns: effectiveColumns,
      tableMinWidth: Math.max(520 + effectiveColumns.length * 140, 760),
    });
  };

  resultRules.forEach((rule) => addGroup(rule.id, rule));
  Array.from(rowsByRule.keys()).forEach((ruleId) => addGroup(ruleId));
  return groups;
}

function flattenMatches(
  matches: RuleMatchItem[],
  runMeta: { runId?: number; ruleId: number; ruleName?: string | null },
): RuleRunEventRow[] {
  return matches.flatMap((match, matchIndex) => {
    const rowRunMeta = {
      ...runMeta,
      runId: match.runId ?? runMeta.runId,
      ruleId: match.ruleId ?? runMeta.ruleId,
      ruleName: runMeta.ruleName,
    };
    if (match.matchedEvents.length > 0) {
      return match.matchedEvents.map((rawEvent, eventIndex) => {
        const event = asRecord(rawEvent);
        return {
          id: `${rowRunMeta.runId ?? 'pending'}-${rowRunMeta.ruleId}-${match.stockCode}-${getEventDate(event)}-${matchIndex}-${eventIndex}`,
          ...rowRunMeta,
          stockCode: match.stockCode,
          stockName: match.stockName,
          eventDate: getEventDate(event),
          event,
          explanation: typeof event.explanation === 'string' ? event.explanation : match.explanation,
        };
      });
    }

    return match.matchedDates.map((date, eventIndex) => ({
      id: `${rowRunMeta.runId ?? 'pending'}-${rowRunMeta.ruleId}-${match.stockCode}-${date}-${matchIndex}-${eventIndex}`,
      ...rowRunMeta,
      stockCode: match.stockCode,
      stockName: match.stockName,
      eventDate: date,
      event: {
        date,
        snapshot: match.snapshot,
        matched_groups: match.matchedGroups,
        explanation: match.explanation,
      },
      explanation: match.explanation,
    }));
  });
}

function getRunIds(run: RuleRunHistoryItem): number[] {
  if (Array.isArray(run.runIds) && run.runIds.length > 0) {
    return run.runIds;
  }
  return run.id > 0 ? [run.id] : [];
}

function getRuleIdsForRun(run: RuleRunHistoryItem | null): number[] {
  if (!run) return [];
  if (Array.isArray(run.ruleIds) && run.ruleIds.length > 0) {
    return run.ruleIds;
  }
  return run.ruleId > 0 ? [run.ruleId] : [];
}

function getRunHistoryEventCount(run: RuleRunHistoryItem, fallbackRows?: number): number {
  if (typeof run.eventCount === 'number' && Number.isFinite(run.eventCount)) {
    return Math.max(0, run.eventCount);
  }
  if (typeof fallbackRows === 'number' && Number.isFinite(fallbackRows)) {
    return Math.max(0, fallbackRows);
  }
  return run.matchCount;
}

function getRunHistoryLabel(run: RuleRunHistoryItem): string {
  const runIds = getRunIds(run);
  const ruleCount = getRuleIdsForRun(run).length;
  if (run.id < 0) {
    return ruleCount > 1 ? `回测中 ${ruleCount} 条规则` : `回测中 ${run.ruleName || `规则 ${run.ruleId}`}`;
  }
  if (ruleCount > 1) {
    const sortedIds = [...runIds].sort((left, right) => left - right);
    const idText = sortedIds.length > 1
      ? `#${sortedIds[0]}-${sortedIds[sortedIds.length - 1]}`
      : `#${run.id}`;
    return `${idText} 多规则回测`;
  }
  return `#${run.id} ${run.ruleName || `规则 ${run.ruleId}`}`;
}

function parseRunTime(value?: string | null): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

function canGroupLegacyRunBatch(group: RuleRunHistoryItem[], candidate: RuleRunHistoryItem): boolean {
  if (group.length === 0 || candidate.id <= 0 || candidate.status === 'running') {
    return false;
  }
  if (group.some((run) => run.status === 'running' || run.ruleId === candidate.ruleId)) {
    return false;
  }
  const targetCount = group[0].targetCount;
  if (candidate.targetCount !== targetCount) {
    return false;
  }
  const minGroupId = Math.min(...group.map((run) => run.id));
  if (minGroupId - candidate.id !== 1) {
    return false;
  }
  const newestStartedAt = parseRunTime(group[0].startedAt);
  const candidateStartedAt = parseRunTime(candidate.startedAt);
  if (newestStartedAt == null || candidateStartedAt == null) {
    return false;
  }
  return Math.abs(newestStartedAt - candidateStartedAt) <= 5 * 60 * 1000;
}

function mergeLegacyRunBatch(group: RuleRunHistoryItem[]): RuleRunHistoryItem {
  const ordered = [...group].sort((left, right) => left.id - right.id);
  const statuses = new Set(ordered.map((run) => run.status));
  const startedAt = ordered
    .map((run) => run.startedAt)
    .filter((value): value is string => Boolean(value))
    .sort()[0] ?? ordered[0].startedAt;
  const sortedFinishedAt = ordered
    .map((run) => run.finishedAt)
    .filter((value): value is string => Boolean(value))
    .sort();
  const finishedAt = sortedFinishedAt.length > 0
    ? sortedFinishedAt[sortedFinishedAt.length - 1]
    : ordered[ordered.length - 1].finishedAt;
  return {
    id: Math.max(...ordered.map((run) => run.id)),
    runIds: ordered.map((run) => run.id),
    ruleId: ordered[0].ruleId,
    ruleIds: ordered.map((run) => run.ruleId),
    ruleName: `多规则回测（${ordered.length} 条）`,
    ruleNames: ordered.map((run) => run.ruleName || `规则 ${run.ruleId}`),
    status: statuses.has('failed') ? 'failed' : statuses.has('partial') ? 'partial' : 'completed',
    targetCount: ordered[0].targetCount,
    matchCount: ordered.reduce((total, run) => total + run.matchCount, 0),
    eventCount: ordered.reduce((total, run) => total + getRunHistoryEventCount(run), 0),
    error: ordered.map((run) => run.error).filter(Boolean).join('；') || null,
    startedAt,
    finishedAt,
    durationMs: ordered.reduce((total, run) => total + (run.durationMs ?? 0), 0),
  };
}

function groupLegacyRunHistory(runs: RuleRunHistoryItem[]): RuleRunHistoryItem[] {
  const grouped: RuleRunHistoryItem[] = [];
  let currentGroup: RuleRunHistoryItem[] = [];

  const flushGroup = () => {
    if (currentGroup.length > 1) {
      grouped.push(mergeLegacyRunBatch(currentGroup));
    } else if (currentGroup.length === 1) {
      grouped.push(currentGroup[0]);
    }
    currentGroup = [];
  };

  for (const run of runs) {
    if (currentGroup.length === 0) {
      currentGroup = [run];
      continue;
    }
    if (canGroupLegacyRunBatch(currentGroup, run)) {
      currentGroup.push(run);
      continue;
    }
    flushGroup();
    currentGroup = [run];
  }
  flushGroup();
  return grouped;
}

function hydrateTarget(
  rule: RuleItem | undefined,
  watchlistCodes: string[],
  allAshareCodes: string[],
): { scope: BacktestTargetScope; stockCodes: string[] } {
  const target = rule?.definition.target;
  const scope = (target?.scope ?? 'watchlist') as RuleTargetScope;
  if (scope === 'watchlist') {
    return { scope, stockCodes: watchlistCodes };
  }
  if (scope === 'all_a_shares') {
    return { scope, stockCodes: allAshareCodes.length > 0 ? allAshareCodes : target?.stockCodes ?? [] };
  }
  return { scope, stockCodes: target?.stockCodes ?? [] };
}

const scopeOptions: Array<{ value: BacktestTargetScope; label: string }> = [
  { value: 'watchlist', label: '自选股 STOCK_LIST' },
  { value: 'all_a_shares', label: '所有 A 股' },
  { value: 'industry', label: '按行业' },
  { value: 'custom', label: '自定义股票列表' },
];

const BacktestPage: React.FC = () => {
  useEffect(() => {
    document.title = '规则回测 - DSA';
  }, []);

  const {
    index: stockIndex,
    loading: isLoadingStockIndex,
    error: stockIndexError,
  } = useStockIndex();
  const [rules, setRules] = useState<RuleItem[]>([]);
  const [metrics, setMetrics] = useState<RuleMetricItem[]>([]);
  const [selectedRuleIds, setSelectedRuleIds] = useState<number[]>([]);
  const [targetScope, setTargetScope] = useState<BacktestTargetScope>('watchlist');
  const [targetCodes, setTargetCodes] = useState<string[]>([]);
  const [selectedIndustry, setSelectedIndustry] = useState(DEFAULT_INDUSTRY);
  const [startDate, setStartDate] = useState(() => getOneYearAgoInShanghai());
  const [endDate, setEndDate] = useState(() => getTodayInShanghai());
  const [watchlistItems, setWatchlistItems] = useState<StockListDisplayItem[]>([]);
  const [runHistory, setRunHistoryState] = useState<RuleRunHistoryItem[]>(
    () => cloneBacktestRuntimeState().runHistory,
  );
  const [selectedRun, setSelectedRunState] = useState<RuleRunHistoryItem | null>(
    () => cloneBacktestRuntimeState().selectedRun,
  );
  const [displayRows, setDisplayRowsState] = useState<RuleRunEventRow[]>(
    () => cloneBacktestRuntimeState().displayRows,
  );
  const [activeResultTab, setActiveResultTabState] = useState<ResultTab>(
    () => cloneBacktestRuntimeState().activeResultTab,
  );
  const [executionLogs, setExecutionLogsState] = useState<ExecutionLogEntry[]>(
    () => cloneBacktestRuntimeState().executionLogs,
  );
  const [runProgressById, setRunProgressByIdState] = useState<Record<number, RunProgressState>>(
    () => cloneBacktestRuntimeState().runProgressById,
  );
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMatches, setIsLoadingMatches] = useState(false);
  const [isRunning, setIsRunningState] = useState(() => cloneBacktestRuntimeState().isRunning);
  const [pageError, setPageError] = useState<ParsedApiError | null>(null);
  const [runError, setRunErrorState] = useState<ParsedApiError | null>(
    () => cloneBacktestRuntimeState().runError,
  );
  const [runWarning, setRunWarningState] = useState<string | null>(
    () => cloneBacktestRuntimeState().runWarning,
  );
  const [selectedRow, setSelectedRow] = useState<RuleRunEventRow | null>(null);
  const [deleteRunCandidate, setDeleteRunCandidate] = useState<RuleRunHistoryItem | null>(null);
  const [deletingRunId, setDeletingRunId] = useState<number | null>(null);
  const [indicatorSelection, setIndicatorSelection] = useState<IndicatorAnalysisSelection | null>(null);
  const [isStockListExpanded, setIsStockListExpanded] = useState(false);
  const [stockListFilter, setStockListFilter] = useState('');
  const [stockListIndustryFilterOpen, setStockListIndustryFilterOpen] = useState(false);
  const [stockListIndustryQuery, setStockListIndustryQuery] = useState('');
  const [selectedStockListIndustries, setSelectedStockListIndustries] = useState<string[]>([]);
  const [expandedResultGroups, setExpandedResultGroups] = useState<Record<string, boolean>>({});
  const runHeartbeatRef = useRef<number | null>(null);
  const logScrollerRef = useRef<HTMLDivElement | null>(null);

  const setRunHistory = useCallback((action: React.SetStateAction<RuleRunHistoryItem[]>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      runHistory: resolveStateAction(current.runHistory, action),
    }));
  }, []);

  const setSelectedRun = useCallback((action: React.SetStateAction<RuleRunHistoryItem | null>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      selectedRun: resolveStateAction(current.selectedRun, action),
    }));
  }, []);

  const setDisplayRows = useCallback((action: React.SetStateAction<RuleRunEventRow[]>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      displayRows: resolveStateAction(current.displayRows, action),
    }));
  }, []);

  const setActiveResultTab = useCallback((action: React.SetStateAction<ResultTab>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      activeResultTab: resolveStateAction(current.activeResultTab, action),
    }));
  }, []);

  const setExecutionLogs = useCallback((action: React.SetStateAction<ExecutionLogEntry[]>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      executionLogs: resolveStateAction(current.executionLogs, action),
    }));
  }, []);

  const setRunProgressById = useCallback((action: React.SetStateAction<Record<number, RunProgressState>>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      runProgressById: resolveStateAction(current.runProgressById, action),
    }));
  }, []);

  const setIsRunning = useCallback((action: React.SetStateAction<boolean>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      isRunning: resolveStateAction(current.isRunning, action),
    }));
  }, []);

  const setRunError = useCallback((action: React.SetStateAction<ParsedApiError | null>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      runError: resolveStateAction(current.runError, action),
    }));
  }, []);

  const setRunWarning = useCallback((action: React.SetStateAction<string | null>) => {
    updateBacktestRuntime((current) => ({
      ...current,
      runWarning: resolveStateAction(current.runWarning, action),
    }));
  }, []);

  useEffect(() => {
    const syncRuntimeState = (state: BacktestRuntimeState) => {
      setRunHistoryState(state.runHistory);
      setSelectedRunState(state.selectedRun);
      setDisplayRowsState(state.displayRows);
      setActiveResultTabState(state.activeResultTab);
      setExecutionLogsState(state.executionLogs);
      setRunProgressByIdState(state.runProgressById);
      setIsRunningState(state.isRunning);
      setRunErrorState(state.runError);
      setRunWarningState(state.runWarning);
    };
    backtestRuntimeListeners.add(syncRuntimeState);
    syncRuntimeState(cloneBacktestRuntimeState());
    return () => {
      backtestRuntimeListeners.delete(syncRuntimeState);
    };
  }, []);

  useEffect(() => () => {
    if (import.meta.env.MODE === 'test' && !backtestRuntimeState.isRunning) {
      backtestRuntimeState = createEmptyBacktestRuntimeState();
    }
  }, []);

  const clearRunHeartbeat = useCallback(() => {
    if (runHeartbeatRef.current != null) {
      window.clearInterval(runHeartbeatRef.current);
      runHeartbeatRef.current = null;
    }
  }, []);

  useEffect(() => clearRunHeartbeat, [clearRunHeartbeat]);

  useEffect(() => {
    if (activeResultTab !== 'logs') return;
    const node = logScrollerRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [activeResultTab, executionLogs.length]);

  const watchlistCodes = useMemo(
    () => watchlistItems.map((item) => item.code),
    [watchlistItems],
  );
  const allAshareCodes = useMemo(
    () => Array.from(new Set(stockIndex
      .filter(isAllShareStock)
      .sort(compareStockIndexById)
      .map(getStockDisplayCode))),
    [stockIndex],
  );
  const allAshareIndustryOptions = useMemo(
    () => buildAllAshareIndustryOptions(stockIndex),
    [stockIndex],
  );
  const industryTargetCodes = useMemo(
    () => getAshareCodesByIndustry(stockIndex, selectedIndustry),
    [selectedIndustry, stockIndex],
  );
  const stockLookup = useMemo(() => buildStockLookup(stockIndex, watchlistItems), [stockIndex, watchlistItems]);
  const stockListItems = useMemo(
    () => buildStockListLineItems(targetCodes, stockLookup),
    [stockLookup, targetCodes],
  );
  const targetCodesText = useMemo(
    () => formatStockListText(targetCodes, stockLookup),
    [stockLookup, targetCodes],
  );
  const selectedRule = useMemo(
    () => rules.find((rule) => rule.id === selectedRuleIds[0]),
    [rules, selectedRuleIds],
  );
  const selectedRules = useMemo(
    () => selectedRuleIds
      .map((ruleId) => rules.find((rule) => rule.id === ruleId))
      .filter((rule): rule is RuleItem => rule !== undefined),
    [rules, selectedRuleIds],
  );
  const selectedRunRule = useMemo(
    () => rules.find((rule) => rule.id === selectedRun?.ruleId),
    [rules, selectedRun?.ruleId],
  );
  const selectedRunRuleIds = useMemo(() => getRuleIdsForRun(selectedRun), [selectedRun]);
  const selectedRunIdsForRows = useMemo(() => (
    selectedRun ? new Set(getRunIds(selectedRun)) : null
  ), [selectedRun]);
  const runRows = useMemo(() => {
    if (!selectedRunIdsForRows) return displayRows;
    if (selectedRunIdsForRows.size === 0) return [];
    return displayRows.filter((row) => row.runId != null && selectedRunIdsForRows.has(row.runId));
  }, [displayRows, selectedRunIdsForRows]);
  const resultRules = useMemo(() => {
    if (selectedRunRuleIds.length > 1) {
      return selectedRunRuleIds
        .map((ruleId) => rules.find((rule) => rule.id === ruleId))
        .filter((rule): rule is RuleItem => rule !== undefined);
    }
    if (selectedRunRule) {
      return [selectedRunRule];
    }
    return selectedRules;
  }, [rules, selectedRules, selectedRunRule, selectedRunRuleIds]);
  const resultRuleGroups = useMemo(
    () => buildRuleResultGroups(runRows, resultRules, rules, metrics),
    [metrics, resultRules, rules, runRows],
  );
  const hasCompletedResultContext = selectedRun !== null && selectedRun.status !== 'running';
  const hasResultContent = resultRuleGroups.length > 0 && (
    runRows.length > 0
    || hasCompletedResultContext
    || runWarning !== null
  );
  const headerControlGridClass = targetScope === 'industry'
    ? 'grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-[12rem_12rem_10rem_10rem]'
    : 'grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-[12rem_10rem_10rem]';
  const activeRunEventCount = runRows.length;
  const targetListReadOnly = targetScope !== 'custom';
  const stockListIndustryOptions = useMemo(
    () => buildStockListIndustryOptions(stockListItems),
    [stockListItems],
  );
  const normalizedStockListIndustryQuery = normalizeIndustryQuery(stockListIndustryQuery);
  const visibleStockListIndustryOptions = useMemo(
    () => stockListIndustryOptions.filter((option) => matchesIndustryQuery(option, normalizedStockListIndustryQuery)),
    [normalizedStockListIndustryQuery, stockListIndustryOptions],
  );
  const filteredStockListItems = useMemo(
    () => filterStockListItems(stockListItems, stockListFilter, selectedStockListIndustries),
    [selectedStockListIndustries, stockListFilter, stockListItems],
  );
  const hasStockListFilter = stockListFilter.trim().length > 0 || selectedStockListIndustries.length > 0;
  const stockListIndustryFilterLabel = selectedStockListIndustries.length === 0
    ? '行业 全部'
    : selectedStockListIndustries.length === 1
      ? selectedStockListIndustries[0]
      : `行业 ${selectedStockListIndustries.length}`;
  const isDateRangeInvalid = Boolean(startDate && endDate && startDate > endDate);
  const runDisabled = selectedRuleIds.length === 0 || targetCodes.length === 0 || isDateRangeInvalid || isRunning;
  const hasActiveRun = Object.values(runProgressById).some((item) => item.progress < 100);

  useEffect(() => {
    const availableIndustries = new Set(stockListIndustryOptions.map((option) => option.name));
    setSelectedStockListIndustries((current) => {
      const next = current.filter((industry) => availableIndustries.has(industry));
      return next.length === current.length ? current : next;
    });
  }, [stockListIndustryOptions]);

  useEffect(() => {
    if (allAshareIndustryOptions.length === 0) return;
    setSelectedIndustry((current) => {
      if (allAshareIndustryOptions.some((option) => option.name === current)) {
        return current;
      }
      return allAshareIndustryOptions.find((option) => option.name === DEFAULT_INDUSTRY)?.name
        ?? allAshareIndustryOptions[0].name;
    });
  }, [allAshareIndustryOptions]);

  useEffect(() => {
    if (targetScope === 'industry') {
      setTargetCodes(industryTargetCodes);
    }
  }, [industryTargetCodes, targetScope]);

  const applyTargetFromRule = useCallback((rule: RuleItem | undefined, nextWatchlist: string[], nextAllAShares: string[]) => {
    const hydrated = hydrateTarget(rule, nextWatchlist, nextAllAShares);
    setTargetScope(hydrated.scope);
    setTargetCodes(hydrated.stockCodes);
  }, []);

  const loadRunMatches = useCallback(async (run: RuleRunHistoryItem) => {
    if (run.id < 0 || run.status === 'running') {
      const now = new Date().toISOString();
      const runningRun = { ...run, eventCount: 0 };
      setSelectedRun(runningRun);
      setDisplayRows([]);
      setSelectedRow(null);
      setIndicatorSelection(null);
      setRunWarning(null);
      setRunError(null);
      setExecutionLogs([{
        id: `${now}-running-${run.id}`,
        time: now,
        level: 'info',
        message: `${getRunHistoryLabel(run)} 仍在执行，暂未生成命中结果`,
      }]);
      setActiveResultTab('logs');
      return;
    }
    setSelectedRun(run);
    setActiveResultTab('results');
    setIsLoadingMatches(true);
    setRunError(null);
    setRunWarning(null);
    setSelectedRow(null);
    setIndicatorSelection(null);
    try {
      const runIds = getRunIds(run);
      const matchGroups = await Promise.all(runIds.map((runId) => rulesApi.getRunMatches(runId)));
      const matches = matchGroups.flat();
      const nextRows = flattenMatches(matches, {
        runId: run.id,
        ruleId: run.ruleId,
        ruleName: run.ruleName,
      });
      const nextRun = { ...run, eventCount: nextRows.length };
      setSelectedRun(nextRun);
      setDisplayRows(nextRows);
      setRunHistory((current) => current.map((item) => (item.id === run.id ? nextRun : item)));
      setPageError(null);
    } catch (err) {
      setPageError(getParsedApiError(err));
    } finally {
      setIsLoadingMatches(false);
    }
  }, [
    setActiveResultTab,
    setDisplayRows,
    setExecutionLogs,
    setRunError,
    setRunHistory,
    setRunWarning,
    setSelectedRun,
  ]);

  const confirmDeleteRun = useCallback(async () => {
    const run = deleteRunCandidate;
    if (!run || run.id < 0 || run.status === 'running' || deletingRunId !== null) {
      return;
    }

    setDeletingRunId(run.id);
    try {
      const deleteRunIds = getRunIds(run);
      await Promise.all(deleteRunIds.map((runId) => rulesApi.deleteRun(runId)));
      setRunHistory((current) => current.filter((item) => item.id !== run.id));
      setRunProgressById((current) => {
        const next = { ...current };
        delete next[run.id];
        deleteRunIds.forEach((runId) => {
          delete next[runId];
        });
        return next;
      });
      if (selectedRun?.id === run.id) {
        setSelectedRun(null);
        setDisplayRows([]);
        setSelectedRow(null);
        setIndicatorSelection(null);
        setRunWarning(null);
        setRunError(null);
        setExecutionLogs([]);
        setExpandedResultGroups({});
        setActiveResultTab('results');
      }
      setPageError(null);
      void rulesApi.list().then(setRules).catch(() => undefined);
    } catch (err) {
      setPageError(getParsedApiError(err));
    } finally {
      setDeletingRunId(null);
      setDeleteRunCandidate(null);
    }
  }, [
    deleteRunCandidate,
    deletingRunId,
    selectedRun?.id,
    setActiveResultTab,
    setDisplayRows,
    setExecutionLogs,
    setRunError,
    setRunHistory,
    setRunProgressById,
    setRunWarning,
    setSelectedRun,
  ]);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setPageError(null);
    try {
      const [metricItems, ruleItems, config, persistedRuns, history] = await Promise.all([
        rulesApi.getMetrics(),
        rulesApi.list(),
        systemConfigApi.getConfig(false),
        rulesApi.listRuns(30),
        historyApi.getList({
          startDate: getRecentStartDate(30),
          endDate: getTodayInShanghai(),
          page: 1,
          limit: WATCHLIST_HISTORY_LIMIT,
        }),
      ]);
      const configuredWatchlistCodes = parseWatchlistValue(
        config.items.find((item) => item.key === 'STOCK_LIST')?.value ?? '',
      );
      const nextWatchlistItems = buildCurrentWatchlistItems(configuredWatchlistCodes, history.items);
      const nextWatchlistCodes = nextWatchlistItems.map((item) => item.code);
      const nextAllAshareCodes = Array.from(new Set(stockIndex
        .filter(isAllShareStock)
        .sort(compareStockIndexById)
        .map(getStockDisplayCode)));
      const groupedPersistedRuns = groupLegacyRunHistory(persistedRuns);
      const firstRule = ruleItems[0];
      const shouldHydratePersistedRuns = !hasBacktestRuntimeSession();
      let persistedSelectedRun = shouldHydratePersistedRuns
        ? groupedPersistedRuns.find((run) => run.status !== 'running') ?? groupedPersistedRuns[0] ?? null
        : null;
      let persistedRows: RuleRunEventRow[] = [];
      let persistedLogs: ExecutionLogEntry[] = [];
      if (persistedSelectedRun) {
        const now = new Date().toISOString();
        persistedLogs = [{
          id: `${now}-restored-${persistedSelectedRun.id}`,
          time: now,
          level: 'info',
          message: persistedSelectedRun.status === 'running'
            ? `已加载历史运行 #${persistedSelectedRun.id}，状态仍为运行中；可稍后刷新查看完成结果`
            : `已从持久化记录加载运行 #${persistedSelectedRun.id}`,
        }];
        if (persistedSelectedRun.id > 0 && persistedSelectedRun.status !== 'running') {
          try {
            const persistedRunIds = getRunIds(persistedSelectedRun);
            const persistedMatchGroups = await Promise.all(
              persistedRunIds.map((runId) => rulesApi.getRunMatches(runId)),
            );
            const persistedMatches = persistedMatchGroups.flat();
            persistedRows = flattenMatches(persistedMatches, {
              runId: persistedSelectedRun.id,
              ruleId: persistedSelectedRun.ruleId,
              ruleName: persistedSelectedRun.ruleName,
            });
            persistedSelectedRun = { ...persistedSelectedRun, eventCount: persistedRows.length };
          } catch (err) {
            setPageError(getParsedApiError(err));
          }
        }
      }
      const selectedHistoryRun = persistedSelectedRun;
      const nextRunHistory = selectedHistoryRun
        ? groupedPersistedRuns.map((run) => (run.id === selectedHistoryRun.id ? selectedHistoryRun : run))
        : groupedPersistedRuns;

      setMetrics(metricItems);
      setRules(ruleItems);
      setWatchlistItems(nextWatchlistItems);
      if (shouldHydratePersistedRuns) {
        setRunHistory(nextRunHistory);
        setSelectedRun(persistedSelectedRun);
        setDisplayRows(persistedRows);
        setActiveResultTab(persistedSelectedRun?.status === 'running' ? 'logs' : 'results');
        setExecutionLogs(persistedLogs);
        setRunProgressById({});
        setRunWarning(null);
        setRunError(null);
      }
      setSelectedRuleIds(firstRule ? [firstRule.id] : []);
      applyTargetFromRule(firstRule, nextWatchlistCodes, nextAllAshareCodes);
    } catch (err) {
      setPageError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [
    applyTargetFromRule,
    setActiveResultTab,
    setDisplayRows,
    setExecutionLogs,
    setRunError,
    setRunHistory,
    setRunProgressById,
    setRunWarning,
    setSelectedRun,
    stockIndex,
  ]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const toggleRule = (ruleId: number) => {
    const isSelected = selectedRuleIds.includes(ruleId);
    const next = isSelected
      ? selectedRuleIds.filter((item) => item !== ruleId)
      : [...selectedRuleIds, ruleId];
    setSelectedRuleIds(next);
    if (!isSelected && selectedRuleIds.length === 0) {
      applyTargetFromRule(rules.find((rule) => rule.id === ruleId), watchlistCodes, allAshareCodes);
    }
  };

  const selectAllRules = () => {
    setSelectedRuleIds(rules.map((rule) => rule.id));
    if (selectedRuleIds.length === 0) {
      applyTargetFromRule(rules[0], watchlistCodes, allAshareCodes);
    }
  };

  const clearSelectedRules = () => {
    setSelectedRuleIds([]);
  };

  const handleScopeChange = (scope: BacktestTargetScope) => {
    setTargetScope(scope);
    setStockListFilter('');
    setStockListIndustryFilterOpen(false);
    setStockListIndustryQuery('');
    setSelectedStockListIndustries([]);
    if (scope === 'watchlist') {
      setTargetCodes(watchlistCodes);
      return;
    }
    if (scope === 'all_a_shares') {
      setTargetCodes(allAshareCodes);
      return;
    }
    if (scope === 'industry') {
      setTargetCodes(industryTargetCodes);
      return;
    }
    setTargetCodes((current) => current);
  };

  const appendExecutionLog = useCallback((message: string, level: LogLevel = 'info') => {
    const now = new Date().toISOString();
    setExecutionLogs((current) => [
      ...current,
      {
        id: `${now}-${current.length}`,
        time: now,
        level,
        message,
      },
    ]);
  }, [setExecutionLogs]);

  const updateRunProgress = useCallback((runId: number, progress: number, stage: string) => {
    setRunProgressById((current) => ({
      ...current,
      [runId]: {
        progress: Math.max(0, Math.min(100, progress)),
      stage,
    },
  }));
  }, [setRunProgressById]);

  const handleRun = async () => {
    if (runDisabled) return;
    setIsRunning(true);
    setRunError(null);
    setRunWarning(null);
    setExecutionLogs([]);
    setRunProgressById({});
    setDisplayRows([]);
    setSelectedRun(null);
    setActiveResultTab('logs');
    const runStartedAt = new Date().toISOString();
    appendExecutionLog(`开始回测：${selectedRuleIds.length} 条规则，${targetCodes.length} 只股票，时间范围 ${startDate || '不限'} 至 ${endDate || '不限'}`);
    let currentTemporaryRunId: number | null = null;
    try {
      const temporaryRunId = -Date.now();
      currentTemporaryRunId = temporaryRunId;
      const runRuleNames = selectedRuleIds
        .map((ruleId) => rules.find((item) => item.id === ruleId)?.name ?? `规则 ${ruleId}`);
      const temporaryRun: RuleRunHistoryItem = {
        id: temporaryRunId,
        ruleId: selectedRuleIds[0],
        ruleIds: selectedRuleIds,
        ruleName: selectedRuleIds.length > 1 ? `多规则回测（${selectedRuleIds.length} 条）` : runRuleNames[0],
        ruleNames: runRuleNames,
        status: 'running',
        targetCount: targetCodes.length,
        matchCount: 0,
        eventCount: 0,
        startedAt: runStartedAt,
        finishedAt: null,
        durationMs: null,
      };
      setSelectedRun(temporaryRun);
      setRunHistory((current) => [temporaryRun, ...current]);
      updateRunProgress(temporaryRunId, 8, '已加入执行队列');
      appendExecutionLog(`已加入执行队列：${selectedRuleIds.length} 条规则`);
      updateRunProgress(temporaryRunId, 20, '请求后端执行');
      const backendModeText = selectedRuleIds.length > 1 ? '后端并行执行' : '后端执行';
      appendExecutionLog(`正在请求${backendModeText}，扫描 ${targetCodes.length} 只股票，${selectedRuleIds.length} 条规则`);
      clearRunHeartbeat();
      const heartbeatStartedAt = Date.now();
      let heartbeatTick = 0;
      runHeartbeatRef.current = window.setInterval(() => {
        heartbeatTick += 1;
        const elapsedSeconds = Math.max(1, Math.floor((Date.now() - heartbeatStartedAt) / 1000));
        const nextProgress = Math.min(80, 32 + heartbeatTick * 6);
        updateRunProgress(temporaryRunId, nextProgress, `${backendModeText}中，已等待 ${elapsedSeconds} 秒`);
        appendExecutionLog(`${backendModeText}中：已等待 ${elapsedSeconds} 秒，结果返回后会自动切换`);
      }, 5000);

      const result = await rulesApi.runBatch({
        ruleIds: selectedRuleIds,
        mode: 'history',
        target: {
          scope: targetScope === 'industry' ? 'custom' : targetScope,
          stockCodes: targetCodes,
        },
        startDate,
        endDate,
      });
      clearRunHeartbeat();
      updateRunProgress(temporaryRunId, 82, '后端已返回结果');
      appendExecutionLog(`后端返回：命中股票 ${result.matchCount} 只，命中记录 ${result.eventCount} 条`);
      if (result.errors.length > 0) {
        appendExecutionLog(`本次回测存在部分错误：${result.errors.join('，')}`, 'warning');
      }
      const now = new Date().toISOString();
      const resultRuleIds = result.ruleIds && result.ruleIds.length > 0 ? result.ruleIds : selectedRuleIds;
      const resultRuleNames = result.ruleNames && result.ruleNames.length > 0 ? result.ruleNames : runRuleNames;
      const resultRuleName = resultRuleIds.length > 1 ? `多规则回测（${resultRuleIds.length} 条）` : resultRuleNames[0] ?? null;
      const nextRows = flattenMatches(result.matches, {
        runId: result.runId,
        ruleId: result.ruleId,
        ruleName: resultRuleName,
      });
      const runMeta: RuleRunHistoryItem = {
        id: result.runId,
        runIds: [result.runId],
        ruleId: result.ruleId,
        ruleIds: resultRuleIds,
        ruleName: resultRuleName,
        ruleNames: resultRuleNames,
        status: result.status,
        targetCount: result.targetCount,
        matchCount: result.matchCount,
        eventCount: result.eventCount || nextRows.length,
        error: result.errors.length > 0 ? result.errors.join('；') : null,
        startedAt: now,
        finishedAt: now,
        durationMs: result.durationMs,
      };
      setSelectedRun(runMeta);
      setDisplayRows(nextRows);
      setRunHistory((current) => current.map((item) => (item.id === temporaryRunId ? runMeta : item)));
      setRunProgressById((current) => {
        const next = { ...current };
        delete next[temporaryRunId];
        next[result.runId] = { progress: 100, stage: '执行完成' };
        return next;
      });
      currentTemporaryRunId = null;
      setRules(await rulesApi.list());
      setRunWarning(result.errors.length > 0 ? result.errors.join('；') : null);
      appendExecutionLog(`本次回测完成：共 ${nextRows.length} 条命中记录`, result.errors.length > 0 ? 'warning' : 'success');
      setActiveResultTab('results');
      setPageError(null);
    } catch (err) {
      clearRunHeartbeat();
      const parsedError = getParsedApiError(err);
      appendExecutionLog(`回测失败：${parsedError.message}`, 'error');
      if (currentTemporaryRunId != null) {
        const finishedAt = new Date().toISOString();
        setRunHistory((current) => current.map((item) => (
          item.id === currentTemporaryRunId
            ? {
                ...item,
                status: 'failed',
                error: parsedError.message,
                finishedAt,
              }
            : item
        )));
        setSelectedRun((current) => (
          current?.id === currentTemporaryRunId
            ? {
                ...current,
                status: 'failed',
                error: parsedError.message,
                finishedAt,
              }
            : current
        ));
        updateRunProgress(currentTemporaryRunId, 100, '执行失败');
      }
      setActiveResultTab('logs');
      setRunError(parsedError);
    } finally {
      clearRunHeartbeat();
      setIsRunning(false);
    }
  };

  const removeTargetStockCode = useCallback((sourceCode: string) => {
    setTargetCodes((current) => current.filter((code) => code !== sourceCode));
  }, []);

  const toggleStockListIndustry = (industry: string) => {
    setSelectedStockListIndustries((current) => (
      current.includes(industry)
        ? current.filter((item) => item !== industry)
        : [...current, industry]
    ));
  };

  const clearStockListIndustryFilter = () => {
    setSelectedStockListIndustries([]);
    setStockListIndustryQuery('');
  };

  const closeStockListExpanded = useCallback(() => {
    setIsStockListExpanded(false);
    setStockListFilter('');
    setStockListIndustryFilterOpen(false);
    setStockListIndustryQuery('');
    setSelectedStockListIndustries([]);
  }, []);

  const toggleResultGroup = useCallback((groupKey: string) => {
    setExpandedResultGroups((current) => ({
      ...current,
      [groupKey]: !(current[groupKey] ?? true),
    }));
  }, []);

  const openIndicatorAnalysis = useCallback((row: RuleRunEventRow) => {
    setIndicatorSelection({
      stockCode: row.stockCode,
      stockName: row.stockName || row.stockCode,
      eventDate: row.eventDate,
    });
  }, []);

  const selectedRowSnapshot = selectedRow ? getEventSnapshot(selectedRow.event) : {};
  const selectedRowSnapshotEntries = Object.entries(selectedRowSnapshot)
    .filter(([, value]) => value != null)
    .sort(([left], [right]) => left.localeCompare(right));
  const selectedRowConditionRows = selectedRow ? buildConditionRows(selectedRow.event, metrics) : [];
  const selectedRuleSummary = selectedRules.length === 0
    ? '请选择规则'
    : selectedRules.length === 1
      ? `#${selectedRules[0].id} ${selectedRules[0].name}`
      : `已选择 ${selectedRules.length} 条规则`;
  const activeDateRangeText = `${startDate || '不限'} - ${endDate || '不限'}`;
  const resultSubtitle = hasActiveRun
    ? `回测运行中 · ${executionLogs.length} 条执行日志`
    : activeResultTab === 'logs' && executionLogs.length > 0
      ? `本次执行日志 · ${executionLogs.length} 条`
      : selectedRun
        ? `${getRunHistoryLabel(selectedRun)} · ${activeRunEventCount} 条命中记录`
        : runRows.length > 0
          ? `本次回测 · ${activeDateRangeText} · ${selectedRuleIds.length} 条规则 · ${activeRunEventCount} 条命中记录`
          : '运行一次规则回测后展示本次命中结果';
  const resultSetName = selectedRun && getRuleIdsForRun(selectedRun).length > 1
    ? selectedRun.ruleName || `多规则回测（${getRuleIdsForRun(selectedRun).length} 条）`
    : selectedRunRule?.name
    || selectedRun?.ruleName
    || (selectedRules.length > 1 ? `本次多规则回测（${selectedRules.length} 条）` : selectedRule?.name)
    || '规则回测';

  return (
    <div className="flex min-h-full flex-col rounded-[1.5rem] bg-transparent">
      <header className="flex-shrink-0 border-b border-white/5 px-3 py-3 sm:px-4">
        <div className="grid gap-3 xl:grid-cols-[18rem_minmax(0,1fr)_auto]">
          <div className="flex min-w-0 flex-col gap-1 text-xs text-muted-text">
            <div className="flex items-center justify-between gap-2">
              <span>规则</span>
              <span>{selectedRuleIds.length} / {rules.length}</span>
            </div>
            <details className="relative">
              <summary
                className={`${INPUT_CLASS} flex cursor-pointer list-none items-center justify-between gap-2 text-foreground marker:hidden`}
                aria-label="选择回测规则"
              >
                <span className="min-w-0 truncate">{selectedRuleSummary}</span>
                <ListFilter className="h-4 w-4 flex-shrink-0 text-primary" />
              </summary>
              <div className="absolute left-0 right-0 z-40 mt-2 rounded-2xl border border-border/70 bg-elevated/95 p-2 shadow-2xl backdrop-blur">
                <div className="mb-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={selectAllRules}
                    disabled={isLoading || isRunning || rules.length === 0}
                    className="rounded-lg border border-border/60 px-2 py-1 text-xs text-secondary-text transition-all hover:border-primary/45 hover:text-primary disabled:opacity-50"
                  >
                    全选
                  </button>
                  <button
                    type="button"
                    onClick={clearSelectedRules}
                    disabled={isLoading || isRunning || selectedRuleIds.length === 0}
                    className="rounded-lg border border-border/60 px-2 py-1 text-xs text-secondary-text transition-all hover:border-primary/45 hover:text-primary disabled:opacity-50"
                  >
                    取消
                  </button>
                </div>
                <div className="max-h-56 space-y-1 overflow-y-auto pr-1">
                  {rules.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-border/60 px-3 py-4 text-center text-xs text-muted-text">
                      暂无规则
                    </div>
                  ) : (
                    rules.map((rule) => (
                      <label
                        key={rule.id}
                        className="flex cursor-pointer items-center gap-2 rounded-xl px-2 py-2 text-xs text-secondary-text transition-all hover:bg-hover hover:text-foreground"
                      >
                        <input
                          type="checkbox"
                          checked={selectedRuleIds.includes(rule.id)}
                          onChange={() => toggleRule(rule.id)}
                          disabled={isRunning}
                          className="h-4 w-4 accent-cyan"
                        />
                        <span className="min-w-0 truncate">#{rule.id} {rule.name}</span>
                      </label>
                    ))
                  )}
                </div>
              </div>
            </details>
          </div>

          <div className="grid min-w-0 gap-3">
            <div className={headerControlGridClass}>
              <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-text">
                <span>股票范围</span>
                <select
                  value={targetScope}
                  onChange={(event) => handleScopeChange(event.target.value as BacktestTargetScope)}
                  disabled={isRunning}
                  className={INPUT_CLASS}
                >
                  {scopeOptions.map((option) => (
                    <option key={option.value} value={option.value} className="bg-elevated text-foreground">
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              {targetScope === 'industry' ? (
                <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-text">
                  <span>行业</span>
                  <select
                    value={selectedIndustry}
                    onChange={(event) => setSelectedIndustry(event.target.value)}
                    disabled={isRunning || allAshareIndustryOptions.length === 0}
                    className={INPUT_CLASS}
                  >
                    {allAshareIndustryOptions.map((option) => (
                      <option key={option.name} value={option.name} className="bg-elevated text-foreground">
                        {option.name}（{option.count}）
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-text">
                <span>开始日期</span>
                <input
                  type="date"
                  value={startDate}
                  max={endDate || undefined}
                  onChange={(event) => setStartDate(event.target.value)}
                  disabled={isRunning}
                  className={INPUT_CLASS}
                />
              </label>
              <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-text">
                <span>结束日期</span>
                <input
                  type="date"
                  value={endDate}
                  min={startDate || undefined}
                  onChange={(event) => setEndDate(event.target.value)}
                  disabled={isRunning}
                  className={INPUT_CLASS}
                />
              </label>
            </div>
            <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-text">
              <span>行业 / 股票代码 / 股票名称</span>
              <div className="relative">
                <textarea
                  value={targetCodesText}
                  onChange={(event) => setTargetCodes(parseStockCodes(event.target.value))}
                  readOnly={targetListReadOnly}
                  disabled={isRunning}
                  className={`${TEXTAREA_CLASS} pr-12 font-mono`}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
                  placeholder="白酒 600519 贵州茅台&#10;银行 000001 平安银行&#10;未分类 AAPL Apple"
                />
                <button
                  type="button"
                  aria-label="展开股票列表"
                  title="展开股票列表"
                  onClick={() => setIsStockListExpanded(true)}
                  disabled={targetCodes.length === 0}
                  className="absolute right-2 top-2 inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border/70 bg-card/90 text-secondary-text shadow-soft-card transition-all hover:border-primary/50 hover:bg-primary/10 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-cyan/15"
                >
                  <Maximize2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </label>
          </div>

          <div className="flex flex-col justify-end gap-2">
            <Button
              variant="primary"
              size="md"
              onClick={handleRun}
              disabled={runDisabled}
              isLoading={isRunning}
              loadingText="回测中..."
            >
              <Play className="h-4 w-4" />
              运行回测
            </Button>
            <div className="text-right text-[11px] text-muted-text">
              {targetCodes.length} 只股票
              {['all_a_shares', 'industry'].includes(targetScope) && isLoadingStockIndex ? '，正在加载 A 股列表...' : ''}
              {['all_a_shares', 'industry'].includes(targetScope) && stockIndexError ? '，A 股列表加载失败' : ''}
            </div>
          </div>
        </div>

        {isDateRangeInvalid ? (
          <InlineAlert variant="warning" message="开始日期不能晚于结束日期" className="mt-3" />
        ) : null}
        {runError ? <ApiErrorAlert error={runError} className="mt-3" /> : null}
      </header>

      <main className="grid min-h-0 flex-1 gap-3 overflow-hidden p-3 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col rounded-2xl border border-border/60 bg-card/70 shadow-soft-card">
          <div className="flex items-center justify-between border-b border-border/50 px-4 py-3">
            <div>
              <span className="label-uppercase">Run History</span>
              <h2 className="mt-1 text-base font-semibold text-foreground">回测执行历史</h2>
            </div>
            <ClipboardList className="h-4 w-4 text-primary" />
          </div>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
            {runHistory.length === 0 ? (
              <EmptyState
                title="暂无执行记录"
                description="运行一次规则回测后会出现在这里。"
                className="h-full min-h-[14rem] border-dashed bg-transparent shadow-none"
              />
            ) : (
              runHistory.map((run) => {
                const progressState = runProgressById[run.id];
                const canDeleteRun = run.id > 0 && run.status !== 'running';
                return (
                  <div key={run.id} className="group relative">
                    <button
                      type="button"
                      onClick={() => void loadRunMatches(run)}
                      className={`w-full rounded-xl border px-3 py-3 pr-12 text-left transition-all ${
                        selectedRun?.id === run.id
                          ? 'border-primary/60 bg-primary/10 shadow-glow-primary'
                          : 'border-border/60 bg-elevated/35 hover:border-primary/35 hover:bg-hover'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0 truncate text-sm font-semibold text-foreground">
                          {getRunHistoryLabel(run)}
                        </span>
                        <Badge variant={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'warning'}>
                          {formatRunStatus(run.status)}
                        </Badge>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-1 text-xs text-secondary-text">
                        <span>扫描 {run.targetCount}</span>
                        <span>命中记录 {getRunHistoryEventCount(run)}</span>
                        <span className="col-span-2 font-mono text-muted-text">{formatDateTime(run.startedAt)}</span>
                      </div>
                      {progressState ? (
                        <div className="mt-3">
                          <div className="flex items-center justify-between gap-2 text-[11px] text-muted-text">
                            <span className="min-w-0 truncate">{progressState.stage}</span>
                            <span className="shrink-0 font-mono">{progressState.progress}%</span>
                          </div>
                          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface/80">
                            <div
                              className="h-full rounded-full bg-primary transition-all duration-300"
                              style={{ width: `${progressState.progress}%` }}
                            />
                          </div>
                        </div>
                      ) : null}
                    </button>
                    {canDeleteRun ? (
                      <button
                        type="button"
                        aria-label={`删除回测记录 #${run.id}`}
                        title="删除这次回测结果"
                        onClick={() => setDeleteRunCandidate(run)}
                        disabled={deletingRunId === run.id}
                        className="absolute right-3 top-3 inline-flex h-8 w-8 items-center justify-center rounded-lg border border-danger/25 bg-danger/10 text-danger opacity-0 shadow-soft-card transition-all hover:bg-danger/15 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-danger/15 group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        </aside>

        <section className="min-h-0 overflow-hidden rounded-2xl border border-border/60 bg-card/70 shadow-soft-card">
          <div className="flex flex-col gap-3 border-b border-border/50 px-4 py-3 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-2">
              <Table2 className="h-5 w-5 text-primary" />
              <div>
                <h2 className="text-lg font-semibold text-foreground">回测结果</h2>
                <p className="text-xs text-secondary-text">{resultSubtitle}</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-secondary-text">
              <div
                role="tablist"
                aria-label="回测结果视图"
                className="inline-flex rounded-xl border border-border/60 bg-elevated/45 p-1"
              >
                {[
                  { key: 'logs' as const, label: '执行日志', count: executionLogs.length },
                  { key: 'results' as const, label: '运行结果', count: activeRunEventCount },
                ].map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    role="tab"
                    aria-selected={activeResultTab === tab.key}
                    onClick={() => setActiveResultTab(tab.key)}
                    className={`inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-all ${
                      activeResultTab === tab.key
                        ? 'bg-primary text-primary-foreground shadow-glow-primary'
                        : 'text-secondary-text hover:bg-hover hover:text-foreground'
                    }`}
                  >
                    <span>{tab.label}</span>
                    {tab.count > 0 ? (
                      <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                        activeResultTab === tab.key ? 'bg-background/20 text-primary-foreground' : 'bg-surface text-muted-text'
                      }`}
                      >
                        {tab.count}
                      </span>
                    ) : null}
                  </button>
                ))}
              </div>
              {selectedRun ? (
                <>
                  <span className="inline-flex items-center gap-1 rounded-lg border border-border/60 px-2 py-1">
                    <CalendarDays className="h-3.5 w-3.5" />
                    {formatDateTime(selectedRun.startedAt)}
                  </span>
                  <span className="rounded-lg border border-border/60 px-2 py-1">{selectedRun.durationMs ?? 0} ms</span>
                </>
              ) : null}
            </div>
          </div>

          <div className="min-h-0 overflow-y-auto p-4">
            {pageError ? <ApiErrorAlert error={pageError} className="mb-3" /> : null}
            {activeResultTab === 'logs' ? (
              <div role="tabpanel" aria-label="执行日志" className="space-y-3 animate-fade-in">
                {Object.entries(runProgressById).length > 0 ? (
                  <div className="rounded-xl border border-border/60 bg-elevated/35 p-3">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <div>
                        <span className="label-uppercase">Progress</span>
                        <h3 className="mt-1 text-sm font-semibold text-foreground">回测进度</h3>
                      </div>
                      {hasActiveRun ? (
                        <span className="rounded-full border border-primary/35 bg-primary/10 px-2 py-1 text-xs text-primary">运行中</span>
                      ) : null}
                    </div>
                    <div className="space-y-3">
                      {Object.entries(runProgressById).map(([runId, progressState]) => {
                        const historyItem = runHistory.find((item) => item.id === Number(runId));
                        return (
                          <div key={runId} className="rounded-lg border border-border/50 bg-card/55 p-3">
                            <div className="flex items-center justify-between gap-3 text-xs">
                              <span className="min-w-0 truncate font-medium text-foreground">
                                {historyItem ? getRunHistoryLabel(historyItem) : `运行 ${runId}`}
                              </span>
                              <span className="shrink-0 font-mono text-primary">{progressState.progress}%</span>
                            </div>
                            <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface/80">
                              <div
                                className="h-full rounded-full bg-primary transition-all duration-300"
                                style={{ width: `${progressState.progress}%` }}
                              />
                            </div>
                            <p className="mt-2 text-xs text-muted-text">{progressState.stage}</p>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
                {executionLogs.length === 0 ? (
                  <EmptyState
                    title="暂无执行日志"
                    description="点击运行回测后，这里会显示请求、进度和完成状态。"
                    className="backtest-empty-state border-dashed"
                    icon={<ClipboardList className="h-6 w-6" />}
                  />
                ) : (
                  <div className="overflow-hidden rounded-xl border border-border/60 bg-elevated/35">
                    <div className="flex items-center justify-between gap-2 border-b border-border/50 px-3 py-2">
                      <span className="label-uppercase">Execution Log</span>
                      <span className="text-xs text-muted-text">{executionLogs.length} 条</span>
                    </div>
                    <div ref={logScrollerRef} className="max-h-[56vh] overflow-y-auto">
                      {executionLogs.map((log) => (
                        <div
                          key={log.id}
                          className="grid gap-2 border-b border-border/40 px-3 py-2 text-xs last:border-b-0 sm:grid-cols-[4.5rem_4rem_minmax(0,1fr)]"
                        >
                          <span className="font-mono text-muted-text">{formatLogTime(log.time)}</span>
                          <span className={`inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-[11px] ${getLogLevelClass(log.level)}`}>
                            {formatLogLevel(log.level)}
                          </span>
                          <span className="min-w-0 break-words text-secondary-text">{log.message}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : isLoading || isLoadingMatches ? (
              <div className="flex h-64 items-center justify-center">
                <div className="backtest-spinner md" />
              </div>
            ) : !hasResultContent ? (
              <EmptyState
                title="暂无命中结果"
                description="历史回测命中后会按股票、日期以及当前规则每个条件的左值和右值展示。"
                className="backtest-empty-state border-dashed"
                icon={<ListFilter className="h-6 w-6" />}
              />
            ) : (
              <div role="tabpanel" aria-label="运行结果" className="animate-fade-in">
                {runWarning ? (
                  <InlineAlert variant="warning" message={runWarning} className="mb-3" />
                ) : null}
                {selectedRun?.error ? (
                  <InlineAlert variant="warning" message={selectedRun.error} className="mb-3" />
                ) : null}
                <div className="backtest-table-toolbar">
                  <div className="backtest-table-toolbar-meta">
                    <span className="label-uppercase">Rule Groups</span>
                    <span className="text-xs text-secondary-text">{resultSetName}</span>
                  </div>
                  <span className="backtest-table-scroll-hint">展开规则查看命中明细，点击股票查看命中日指标分析，点击行查看条件快照</span>
                </div>
                <div className="space-y-3">
                  {resultRuleGroups.map((group) => {
                    const expanded = expandedResultGroups[group.key] ?? true;
                    return (
                      <section
                        key={group.key}
                        data-testid={`backtest-rule-group-${group.ruleId}`}
                        className="overflow-hidden rounded-xl border border-border/60 bg-elevated/25"
                      >
                        <button
                          type="button"
                          aria-expanded={expanded}
                          aria-controls={`backtest-rule-group-panel-${group.ruleId}`}
                          aria-label={`${expanded ? '收起' : '展开'}规则 #${group.ruleId} ${group.ruleName}`}
                          onClick={() => toggleResultGroup(group.key)}
                          className="flex w-full items-center gap-3 border-b border-border/45 px-3 py-3 text-left transition-all hover:bg-hover/60"
                        >
                          <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-primary/35 bg-primary/10 text-primary">
                            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold text-foreground">#{group.ruleId} {group.ruleName}</span>
                            <span className="mt-0.5 block text-xs text-secondary-text">命中 {group.rows.length} 条</span>
                          </span>
                        </button>
                        {expanded ? (
                          <div id={`backtest-rule-group-panel-${group.ruleId}`}>
                            {group.rows.length === 0 ? (
                              <div className="px-4 py-6 text-sm text-muted-text">该规则暂无命中记录</div>
                            ) : (
                              <div className="backtest-table-wrapper rounded-none border-0">
                                <table className="backtest-table w-full text-sm" style={{ minWidth: `${group.tableMinWidth}px` }}>
                                  <thead className="backtest-table-head">
                                    <tr className="text-left">
                                      <th className="backtest-table-head-cell">股票</th>
                                      <th className="backtest-table-head-cell">日期</th>
                                      {group.columns.map((column) => (
                                        <th key={column.key} className="backtest-table-head-cell">
                                          <span className={column.side === 'left' ? 'text-danger' : undefined}>
                                            {column.header}
                                          </span>
                                        </th>
                                      ))}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {group.rows.map((row) => (
                                      <tr
                                        key={row.id}
                                        className="backtest-table-row cursor-pointer"
                                        onClick={() => setSelectedRow(row)}
                                      >
                                        <td className="backtest-table-cell backtest-table-code">
                                          <button
                                            type="button"
                                            aria-label={`查看 ${row.stockName || row.stockCode} ${row.eventDate} 指标分析`}
                                            title={`查看 ${row.eventDate} 指标分析`}
                                            onClick={(event) => {
                                              event.stopPropagation();
                                              openIndicatorAnalysis(row);
                                            }}
                                            className="flex flex-col rounded-lg px-2 py-1 text-left transition-all hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-cyan/20"
                                          >
                                            <span>{row.stockCode}</span>
                                            <span className="text-xs text-muted-text">{row.stockName || '--'}</span>
                                          </button>
                                        </td>
                                        <td className="backtest-table-cell font-mono text-secondary-text">{row.eventDate}</td>
                                        {group.columns.map((column) => (
                                          <td key={`${row.id}:${column.key}`} className="backtest-table-cell font-mono text-secondary-text">
                                            {formatMetricValue(getConditionColumnValue(row, column), column, metrics)}
                                          </td>
                                        ))}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        ) : null}
                      </section>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </section>
      </main>

      {stockListItems.length > 0 ? (
        <span className="sr-only">
          当前股票范围包含 {stockListItems.length} 只股票，已选择 {selectedStockListIndustries.length} 个行业筛选条件
        </span>
      ) : null}

      {isStockListExpanded ? (
        <div
          className="fixed inset-0 z-[70] flex items-stretch justify-center bg-background/75 p-2 backdrop-blur-sm md:p-5"
          role="dialog"
          aria-modal="true"
          aria-label="股票列表选择"
          onClick={closeStockListExpanded}
        >
          <div
            className="glass-card flex min-h-0 w-full max-w-7xl flex-col overflow-hidden shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex flex-col gap-3 border-b border-border/60 p-3 md:flex-row md:items-center md:justify-between md:p-4">
              <div>
                <h2 className="text-lg font-semibold text-foreground">行业 / 股票代码 / 股票名称</h2>
                <p className="mt-1 text-xs text-muted-text">
                  {hasStockListFilter ? `${filteredStockListItems.length} / ` : ''}
                  {targetCodes.length} 只股票
                </p>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <div className="relative sm:w-44">
                  <Button
                    variant="home-action-ai"
                    size="md"
                    disabled={stockListIndustryOptions.length === 0}
                    aria-haspopup="menu"
                    aria-expanded={stockListIndustryFilterOpen}
                    aria-label="筛选行业类别"
                    onClick={() => setStockListIndustryFilterOpen((current) => !current)}
                    className="w-full justify-start"
                  >
                    <Filter className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 truncate">{stockListIndustryFilterLabel}</span>
                  </Button>
                  {stockListIndustryFilterOpen ? (
                    <div
                      role="menu"
                      className="absolute left-0 z-[90] mt-2 w-72 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-subtle bg-elevated/95 shadow-2xl backdrop-blur sm:left-auto sm:right-0"
                    >
                      <div className="flex items-center justify-between gap-2 border-b border-subtle px-3 py-2">
                        <span className="text-xs font-semibold text-foreground">行业筛选</span>
                        <button
                          type="button"
                          aria-label="清空行业筛选"
                          disabled={selectedStockListIndustries.length === 0}
                          onClick={clearStockListIndustryFilter}
                          className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-secondary-text transition-colors hover:bg-hover hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                        >
                          <X className="h-3.5 w-3.5" />
                          清空
                        </button>
                      </div>
                      <div className="border-b border-subtle px-3 py-2">
                        <div className="flex h-9 items-center gap-2 rounded-lg border border-subtle bg-surface/80 px-2">
                          <Search className="h-4 w-4 shrink-0 text-muted-text" />
                          <input
                            type="search"
                            value={stockListIndustryQuery}
                            onChange={(event) => setStockListIndustryQuery(event.target.value)}
                            placeholder="中文 / 拼音首字母"
                            aria-label="搜索行业"
                            className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-text"
                          />
                        </div>
                      </div>
                      <div className="max-h-72 overflow-y-auto p-2">
                        {visibleStockListIndustryOptions.length === 0 ? (
                          <div className="px-3 py-6 text-center text-sm text-muted-text">
                            没有匹配的行业
                          </div>
                        ) : visibleStockListIndustryOptions.map((option) => {
                          const checked = selectedStockListIndustries.includes(option.name);
                          return (
                            <label
                              key={option.name}
                              className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleStockListIndustry(option.name)}
                                aria-label={`筛选行业 ${option.name}`}
                                className="h-3.5 w-3.5 rounded border-subtle-hover bg-transparent accent-primary focus:ring-primary/30"
                              />
                              <span className="min-w-0 flex-1 truncate">{option.name}</span>
                              <span className="shrink-0 text-xs text-muted-text">{option.count}</span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
                <label className="relative block sm:w-72">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-text" />
                  <input
                    value={stockListFilter}
                    onChange={(event) => setStockListFilter(event.target.value)}
                    className={`${INPUT_CLASS} pl-9`}
                    placeholder="筛选代码或名称"
                    aria-label="筛选股票列表"
                  />
                </label>
                <Button variant="secondary" size="sm" onClick={closeStockListExpanded} aria-label="收起股票列表">
                  <Minimize2 className="h-4 w-4" />
                  收起
                </Button>
              </div>
            </div>
            <div className="min-h-0 flex-1 p-3 md:p-4">
              <div
                className={`${TEXTAREA_CLASS} h-full min-h-[65vh] overflow-y-auto px-0 py-0`}
                role="list"
                aria-label="股票列表内容"
              >
                {filteredStockListItems.length === 0 ? (
                  <div className="flex h-full min-h-[12rem] items-center justify-center px-4 text-sm text-muted-text">
                    没有匹配的股票
                  </div>
                ) : (
                  filteredStockListItems.map((item) => (
                    <div
                      key={item.sourceCode}
                      role="listitem"
                      className="grid grid-cols-[2.25rem_minmax(0,1fr)] items-center border-b border-border/35 px-3 py-1.5 last:border-b-0 hover:bg-hover/70"
                    >
                      <button
                        type="button"
                        aria-label={`移除 ${item.line}`}
                        title={`移除 ${item.line}`}
                        onClick={() => removeTargetStockCode(item.sourceCode)}
                        className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-text transition-all hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-danger/15"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                      <span
                        className="min-w-0 whitespace-pre-wrap break-words font-mono text-sm leading-6 text-foreground"
                        style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
                      >
                        {item.line}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {indicatorSelection ? (
        <IndicatorAnalysisModal
          stockCode={indicatorSelection.stockCode}
          stockName={indicatorSelection.stockName}
          initialDate={indicatorSelection.eventDate}
          initialHistoryDays={getIndicatorHistoryDays(indicatorSelection.eventDate)}
          onClose={() => setIndicatorSelection(null)}
        />
      ) : null}

      {selectedRow ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-background/75 p-3 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="回测指标明细"
          onClick={() => setSelectedRow(null)}
        >
          <div
            className="glass-card flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-border/60 p-4">
              <div>
                <span className="label-uppercase">Indicator Snapshot</span>
                <h2 className="mt-1 text-lg font-semibold text-foreground">
                  {selectedRow.stockName || selectedRow.stockCode}
                </h2>
                <p className="mt-1 text-xs text-secondary-text">
                  {selectedRow.stockCode} · {selectedRow.eventDate} · 规则 #{selectedRow.ruleId}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedRow(null)}
                aria-label="关闭指标明细"
                className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-border/60 bg-elevated/60 text-secondary-text transition-all hover:border-primary/45 hover:text-primary"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
                <div>
                  <div className="mb-2 flex items-center gap-2">
                    <Search className="h-4 w-4 text-primary" />
                    <h3 className="text-sm font-semibold text-foreground">全部指标</h3>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {selectedRowSnapshotEntries.length === 0 ? (
                      <div className="rounded-xl border border-dashed border-border/60 p-4 text-sm text-muted-text">无指标快照</div>
                    ) : (
                      selectedRowSnapshotEntries.map(([key, value]) => (
                        <div key={key} className="rounded-xl border border-border/50 bg-elevated/35 px-3 py-2">
                          <div className="truncate text-[11px] text-muted-text">{metricLabel(metrics, key)}</div>
                          <div className="mt-1 font-mono text-sm text-foreground">{formatNumber(value)}</div>
                          <div className="mt-1 truncate font-mono text-[10px] text-muted-text">{key}</div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
                <div>
                  <h3 className="mb-2 text-sm font-semibold text-foreground">命中条件值</h3>
                  <div className="space-y-2">
                    {selectedRowConditionRows.length === 0 ? (
                      <div className="rounded-xl border border-dashed border-border/60 p-4 text-sm text-muted-text">无条件明细</div>
                    ) : (
                      selectedRowConditionRows.map((condition, index) => (
                        <div key={`${condition.id}-${index}`} className="rounded-xl border border-primary/20 bg-primary/5 p-3">
                          <div className="text-xs font-semibold text-foreground">{condition.metric}</div>
                          <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                            <span className="text-muted-text">左值</span>
                            <span className="font-mono text-secondary-text">{formatNumber(condition.left)}</span>
                            <span className="text-muted-text">右值</span>
                            <span className="font-mono text-secondary-text">{formatNumber(condition.right)}</span>
                          </div>
                          {condition.explanation ? (
                            <p className="mt-2 text-xs leading-5 text-secondary-text">{condition.explanation}</p>
                          ) : null}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <ConfirmDialog
        isOpen={deleteRunCandidate !== null}
        title="删除回测记录"
        message={`确认删除回测记录 #${deleteRunCandidate?.id ?? ''} 吗？删除后将同时移除这次回测的命中结果，刷新后也不会再显示。`}
        confirmText={deletingRunId === deleteRunCandidate?.id ? '删除中...' : '确认删除'}
        cancelText="取消"
        isDanger
        onConfirm={() => void confirmDeleteRun()}
        onCancel={() => {
          if (deletingRunId === null) {
            setDeleteRunCandidate(null);
          }
        }}
      />
    </div>
  );
};

export default BacktestPage;
