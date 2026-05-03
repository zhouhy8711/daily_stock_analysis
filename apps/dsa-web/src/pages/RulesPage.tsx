import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  CopyPlus,
  HelpCircle,
  ListFilter,
  Maximize2,
  Minimize2,
  Play,
  Plus,
  Save,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { rulesApi } from '../api/rules';
import { getParsedApiError } from '../api/error';
import { historyApi } from '../api/history';
import { systemConfigApi } from '../api/systemConfig';
import { AppPage, Badge, Button, ConfirmDialog, EmptyState, InlineAlert, Input, PageHeader, Tooltip } from '../components/common';
import { useStockIndex } from '../hooks/useStockIndex';
import type { StockIndexItem } from '../types/stockIndex';
import type {
  RuleAggregateMethod,
  RuleCompareOperator,
  RuleCondition,
  RuleDefinition,
  RuleGroup,
  RuleItem,
  RuleMetricItem,
  RuleOperator,
  RuleRunResponse,
  RuleTargetScope,
  RuleValueExpression,
} from '../types/rules';
import { getRecentStartDate, getTodayInShanghai } from '../utils/format';
import { generateUUID } from '../utils/uuid';
import {
  buildCurrentWatchlistItems,
  compareStockIndexById,
  isAllShareStock,
  parseWatchlistValue,
} from '../utils/watchlist';

const PANEL_CLASS = 'rounded-2xl border border-border/60 bg-card/80 p-4 shadow-soft-card';
const INPUT_CLASS =
  'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';
const TEXTAREA_CLASS =
  'input-surface input-focus-glow min-h-[92px] w-full resize-y rounded-xl border bg-transparent px-3 py-2 text-sm text-foreground transition-all placeholder:text-muted-text focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';
const STOCK_CODES_TEXTAREA_CLASS = `${TEXTAREA_CLASS} font-mono`;
const WATCHLIST_HISTORY_LIMIT = 20;
const OFFSET_HELP_TEXT = (
  <span>
    偏移按交易日计算：0 表示当前判断日，也就是最新交易日；1 表示前 1 个交易日；2 表示前 2 个交易日。历史聚合里会先按偏移跳过对应交易日，再向前取窗口，例如窗口 5、偏移 1 就是跳过最新日，取前 5 个交易日。
  </span>
);

type StockListDisplayItem = {
  code: string;
  name?: string;
};

type StockListLineItem = StockListDisplayItem & {
  sourceCode: string;
  line: string;
};

const OPERATOR_OPTIONS: Array<{ value: RuleOperator; label: string }> = [
  { value: '>', label: '大于' },
  { value: '>=', label: '大于等于' },
  { value: '<', label: '小于' },
  { value: '<=', label: '小于等于' },
  { value: '=', label: '等于' },
  { value: '!=', label: '不等于' },
  { value: 'between', label: '介于' },
  { value: 'not_between', label: '不介于' },
  { value: 'consecutive', label: '连续满足' },
  { value: 'frequency', label: '频次满足' },
  { value: 'trend_up', label: '连续上升' },
  { value: 'trend_down', label: '连续下降' },
  { value: 'new_high', label: 'N 期新高' },
  { value: 'new_low', label: 'N 期新低' },
  { value: 'exists', label: '有值' },
  { value: 'not_exists', label: '无值' },
];
const COMPARE_OPTIONS: Array<{ value: RuleCompareOperator; label: string }> = [
  { value: '>', label: '>' },
  { value: '>=', label: '>=' },
  { value: '<', label: '<' },
  { value: '<=', label: '<=' },
  { value: '=', label: '=' },
  { value: '!=', label: '!=' },
];
const AGGREGATE_OPTIONS: Array<{ value: RuleAggregateMethod; label: string }> = [
  { value: 'max', label: '最大值' },
  { value: 'min', label: '最小值' },
  { value: 'avg', label: '平均值' },
  { value: 'sum', label: '求和' },
  { value: 'median', label: '中位数' },
  { value: 'std', label: '标准差' },
];

type MetricGroup = {
  category: string;
  items: RuleMetricItem[];
};

function createLiteral(value = 0): RuleValueExpression {
  return { type: 'literal', value };
}

function createMetricValue(metric = 'close'): RuleValueExpression {
  return { type: 'metric', metric, offset: 0 };
}

function createAggregate(metric = 'close'): RuleValueExpression {
  return { type: 'aggregate', metric, method: 'avg', window: 5, offset: 1 };
}

function createRange(): RuleValueExpression {
  return { type: 'range', min: createLiteral(0), max: createLiteral(10) };
}

function createCondition(metric = 'close'): RuleCondition {
  return {
    id: `cond-${generateUUID()}`,
    left: { metric, offset: 0 },
    operator: '>',
    right: createLiteral(0),
  };
}

function createGroup(metric = 'close'): RuleGroup {
  return {
    id: `group-${generateUUID()}`,
    conditions: [createCondition(metric)],
  };
}

function createDefinition(metric = 'close', stockCodes: string[] = []): RuleDefinition {
  return {
    period: 'daily',
    lookbackDays: 120,
    target: { scope: 'watchlist', stockCodes },
    groups: [createGroup(metric)],
  };
}

function normalizeRuleStockCode(value: string): string {
  const firstCode = value
    .trim()
    .toUpperCase()
    .match(/(?:SH|SZ|BJ)\d{6}|\d{6}\.(?:SH|SZ|SS|BJ)|HK\d{1,5}|\d{1,5}\.HK|\d{6}|\d{5}|[A-Z]{1,5}(?:\.US)?/);
  const code = firstCode?.[0] ?? '';
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
    .map(normalizeRuleStockCode)
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
  const normalized = normalizeRuleStockCode(upperCode);
  const keys = new Set<string>([upperCode, normalized]);

  if (upperCode) {
    keys.add(upperCode);
  }
  const [base] = upperCode.split('.');
  if (base) {
    keys.add(normalizeRuleStockCode(base));
  }
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
  if (upperCode.startsWith('HK') && upperCode.length > 2) {
    keys.add(normalizeRuleStockCode(upperCode.slice(2)));
  }
  const hkSuffix = upperCode.match(/^(\d{1,5})\.HK$/);
  if (hkSuffix) {
    const padded = hkSuffix[1].padStart(5, '0');
    keys.add(padded);
    keys.add(`HK${padded}`);
  }
  const usSuffix = upperCode.match(/^([A-Z]{1,5})\.US$/);
  if (usSuffix) {
    keys.add(usSuffix[1]);
  }
  return Array.from(keys).filter(Boolean);
}

function getStockDisplayCode(item: StockIndexItem): string {
  return normalizeRuleStockCode(item.displayCode || item.canonicalCode);
}

function compareStockListItems(left: StockIndexItem, right: StockIndexItem): number {
  return compareStockIndexById(left, right);
}

function isAllAshareItem(item: StockIndexItem): boolean {
  return isAllShareStock(item);
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
    };
    for (const key of getStockLookupKeys(item.canonicalCode).concat(getStockLookupKeys(item.displayCode))) {
      lookup.set(key, displayItem);
    }
  }
  for (const item of preferredItems) {
    if (!item.code) {
      continue;
    }
    const existing = getStockLookupKeys(item.code)
      .map((key) => lookup.get(key))
      .find((displayItem) => displayItem !== undefined);
    const displayItem = {
      code: item.code,
      name: item.name || existing?.name,
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
    if (item) {
      return item;
    }
  }
  return { code: normalizeRuleStockCode(code) };
}

function formatStockListText(codes: string[], lookup: Map<string, StockListDisplayItem>): string {
  return codes
    .map((code) => getDisplayItemForCode(code, lookup))
    .map((item) => (item.name ? `${item.code} ${item.name}` : item.code))
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
      line: item.name ? `${item.code} ${item.name}` : item.code,
    };
  });
}

function filterStockListItems(items: StockListLineItem[], query: string): StockListLineItem[] {
  const normalizedQuery = query.trim().toUpperCase();
  if (!normalizedQuery) {
    return items;
  }
  return items.filter((item) => item.line.toUpperCase().includes(normalizedQuery));
}

function hydrateDefinitionTarget(
  definition: RuleDefinition,
  watchlistCodes: string[],
  allAshareCodes: string[],
): RuleDefinition {
  let stockCodes: string[] | null = null;
  if (definition.target.scope === 'watchlist') {
    stockCodes = watchlistCodes;
  }
  if (definition.target.scope === 'all_a_shares' && allAshareCodes.length > 0) {
    stockCodes = allAshareCodes;
  }
  if (!stockCodes) {
    return definition;
  }
  return {
    ...definition,
    target: {
      ...definition.target,
      stockCodes,
    },
  };
}

function formatNumber(value: unknown): string {
  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) return '--';
  if (Math.abs(numberValue) >= 1000) {
    return numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  return numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}

function metricLabel(metrics: RuleMetricItem[], metricKey: string): string {
  return metrics.find((metric) => metric.key === metricKey)?.label ?? metricKey;
}

function metricOptionLabel(metric: RuleMetricItem): string {
  return metric.unit ? `${metric.label} · ${metric.key} (${metric.unit})` : `${metric.label} · ${metric.key}`;
}

function groupMetricsByCategory(metrics: RuleMetricItem[]): MetricGroup[] {
  const groups: MetricGroup[] = [];
  const groupByCategory = new Map<string, MetricGroup>();
  for (const metric of metrics) {
    const category = metric.category || '未分类';
    let group = groupByCategory.get(category);
    if (!group) {
      group = { category, items: [] };
      groupByCategory.set(category, group);
      groups.push(group);
    }
    group.items.push(metric);
  }
  return groups;
}

function valueSummary(metrics: RuleMetricItem[], value?: RuleValueExpression): string {
  if (!value) return '无右侧值';
  if (value.type === 'literal') return formatNumber(value.value);
  if (value.type === 'metric') return metricLabel(metrics, value.metric);
  if (value.type === 'range') {
    return `${valueSummary(metrics, value.min)} 到 ${valueSummary(metrics, value.max)}`;
  }
  const method = AGGREGATE_OPTIONS.find((item) => item.value === value.method)?.label ?? value.method;
  const multiplier = value.multiplier ? ` * ${formatNumber(value.multiplier)}` : '';
  return `前 ${value.window} 期 ${metricLabel(metrics, value.metric)} ${method}${multiplier}`;
}

function conditionSummary(metrics: RuleMetricItem[], condition: RuleCondition): string {
  const left = metricLabel(metrics, condition.left.metric);
  if (condition.operator === 'consecutive') {
    return `连续 ${condition.lookback ?? 1} 次满足 ${left} ${condition.compare ?? '>'} ${valueSummary(metrics, condition.right)}`;
  }
  if (condition.operator === 'frequency') {
    return `近 ${condition.lookback ?? 1} 次至少 ${condition.minCount ?? 1} 次满足 ${left} ${condition.compare ?? '>'} ${valueSummary(metrics, condition.right)}`;
  }
  if (condition.operator === 'trend_up') return `${left} 连续上升 ${condition.lookback ?? 3} 期`;
  if (condition.operator === 'trend_down') return `${left} 连续下降 ${condition.lookback ?? 3} 期`;
  if (condition.operator === 'new_high') return `${left} 创 ${condition.lookback ?? 20} 期新高`;
  if (condition.operator === 'new_low') return `${left} 创 ${condition.lookback ?? 20} 期新低`;
  if (condition.operator === 'exists') return `${left} 有值`;
  if (condition.operator === 'not_exists') return `${left} 无值`;
  return `${left} ${condition.operator} ${valueSummary(metrics, condition.right)}`;
}

function canUseRightValue(operator: RuleOperator): boolean {
  return !['trend_up', 'trend_down', 'new_high', 'new_low', 'exists', 'not_exists'].includes(operator);
}

function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  className = '',
}: {
  label?: string;
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <label className={`flex flex-col gap-1 text-xs text-muted-text ${className}`}>
      {label ? <span>{label}</span> : null}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        className={INPUT_CLASS}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} className="bg-elevated text-foreground">
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function MetricSelectField({
  label,
  value,
  metrics,
  onChange,
}: {
  label: string;
  value: string;
  metrics: RuleMetricItem[];
  onChange: (value: string) => void;
}) {
  const groups = groupMetricsByCategory(metrics);
  const hasSelectedMetric = metrics.some((metric) => metric.key === value);

  return (
    <label className="flex flex-col gap-1 text-xs text-muted-text">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={INPUT_CLASS}
      >
        {!hasSelectedMetric && value ? (
          <option value={value} className="bg-elevated text-foreground">
            {value}
          </option>
        ) : null}
        {groups.map((group) => (
          <optgroup key={group.category} label={group.category} className="bg-elevated text-foreground">
            {group.items.map((metric) => (
              <option key={metric.key} value={metric.key} className="bg-elevated text-foreground">
                {metricOptionLabel(metric)}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}

function FieldLabel({ label, help }: { label: string; help?: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span>{label}</span>
      {help ? (
        <Tooltip content={help} side="bottom" contentClassName="max-w-[22rem]">
          <button
            type="button"
            className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-text transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45"
            aria-label={`${label}说明`}
          >
            <HelpCircle className="h-3.5 w-3.5" />
          </button>
        </Tooltip>
      ) : null}
    </span>
  );
}

function NumberField({
  label,
  value,
  min,
  onChange,
  className = '',
  help,
}: {
  label: string;
  value: number | undefined;
  min?: number;
  onChange: (value: number) => void;
  className?: string;
  help?: React.ReactNode;
}) {
  return (
    <label className={`flex flex-col gap-1 text-xs text-muted-text ${className}`}>
      <FieldLabel label={label} help={help} />
      <input
        type="number"
        min={min}
        value={value ?? ''}
        onChange={(event) => onChange(Number(event.target.value || 0))}
        className={INPUT_CLASS}
      />
    </label>
  );
}

function ValueEditor({
  value,
  metrics,
  onChange,
}: {
  value: RuleValueExpression;
  metrics: RuleMetricItem[];
  onChange: (value: RuleValueExpression) => void;
}) {
  const typeOptions: Array<{ value: RuleValueExpression['type']; label: string }> = [
    { value: 'literal', label: '固定数值' },
    { value: 'metric', label: '指标引用' },
    { value: 'aggregate', label: '历史聚合' },
  ];
  const switchType = (type: RuleValueExpression['type']) => {
    if (type === 'literal') onChange(createLiteral());
    if (type === 'metric') onChange(createMetricValue(metrics[0]?.key ?? 'close'));
    if (type === 'aggregate') onChange(createAggregate(metrics[0]?.key ?? 'close'));
  };

  if (value.type === 'range') {
    return (
      <div className="grid gap-2 md:grid-cols-2">
        <NumberField
          label="下限"
          value={value.min.type === 'literal' ? value.min.value : 0}
          onChange={(next) => onChange({ ...value, min: createLiteral(next) })}
        />
        <NumberField
          label="上限"
          value={value.max.type === 'literal' ? value.max.value : 0}
          onChange={(next) => onChange({ ...value, max: createLiteral(next) })}
        />
      </div>
    );
  }

  return (
    <div className="grid gap-2 lg:grid-cols-[9rem_minmax(10rem,1fr)_8rem_7rem]">
      <SelectField
        label="值类型"
        value={value.type}
        options={typeOptions}
        onChange={switchType}
      />
      {value.type === 'literal' ? (
        <NumberField
          label="数值"
          value={value.value}
          onChange={(next) => onChange({ ...value, value: next })}
          className="lg:col-span-3"
        />
      ) : null}
      {value.type === 'metric' ? (
        <>
          <MetricSelectField
            label="指标"
            value={value.metric}
            metrics={metrics}
            onChange={(metric) => onChange({ ...value, metric })}
          />
          <NumberField
            label="取值日偏移"
            help={OFFSET_HELP_TEXT}
            min={0}
            value={value.offset ?? 0}
            onChange={(offset) => onChange({ ...value, offset })}
          />
          <NumberField
            label="倍数"
            value={value.multiplier ?? 1}
            onChange={(multiplier) => onChange({ ...value, multiplier })}
          />
        </>
      ) : null}
      {value.type === 'aggregate' ? (
        <>
          <MetricSelectField
            label="指标"
            value={value.metric}
            metrics={metrics}
            onChange={(metric) => onChange({ ...value, metric })}
          />
          <SelectField
            label="方法"
            value={value.method}
            options={AGGREGATE_OPTIONS}
            onChange={(method) => onChange({ ...value, method })}
          />
          <div className="grid grid-cols-3 gap-2 lg:col-span-4">
            <NumberField
              label="窗口"
              min={1}
              value={value.window}
              onChange={(window) => onChange({ ...value, window })}
            />
            <NumberField
              label="取值日偏移"
              help={OFFSET_HELP_TEXT}
              min={0}
              value={value.offset ?? 1}
              onChange={(offset) => onChange({ ...value, offset })}
            />
            <NumberField
              label="倍数"
              value={value.multiplier ?? 1}
              onChange={(multiplier) => onChange({ ...value, multiplier })}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}

function ConditionEditor({
  condition,
  metrics,
  onChange,
  onRemove,
}: {
  condition: RuleCondition;
  metrics: RuleMetricItem[];
  onChange: (condition: RuleCondition) => void;
  onRemove: () => void;
}) {
  const updateOperator = (operator: RuleOperator) => {
    if (operator === 'between' || operator === 'not_between') {
      onChange({ ...condition, operator, right: createRange() });
      return;
    }
    if (!canUseRightValue(operator)) {
      onChange({ ...condition, operator, right: undefined, lookback: condition.lookback ?? 3 });
      return;
    }
    if (operator === 'consecutive') {
      onChange({ ...condition, operator, compare: condition.compare ?? '>', right: condition.right ?? createLiteral(), lookback: 3 });
      return;
    }
    if (operator === 'frequency') {
      onChange({ ...condition, operator, compare: condition.compare ?? '>', right: condition.right ?? createLiteral(), lookback: 10, minCount: 6 });
      return;
    }
    onChange({ ...condition, operator, right: condition.right && condition.right.type !== 'range' ? condition.right : createLiteral() });
  };

  return (
    <div className="rounded-xl border border-border/55 bg-elevated/35 p-3">
      <div className="grid gap-2 xl:grid-cols-[minmax(9rem,1fr)_10rem_5rem_auto]">
        <MetricSelectField
          label="指标 key"
          value={condition.left.metric}
          metrics={metrics}
          onChange={(metric) => onChange({ ...condition, left: { ...condition.left, metric } })}
        />
        <SelectField
          label="关系"
          value={condition.operator}
          options={OPERATOR_OPTIONS}
          onChange={updateOperator}
        />
        <NumberField
          label="取值日偏移"
          help={OFFSET_HELP_TEXT}
          min={0}
          value={condition.left.offset ?? 0}
          onChange={(offset) => onChange({ ...condition, left: { ...condition.left, offset } })}
        />
        <div className="flex items-end">
          <Button variant="ghost" size="sm" onClick={onRemove} aria-label="删除子条件">
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {condition.operator === 'consecutive' || condition.operator === 'frequency' ? (
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          <SelectField
            label="内部比较"
            value={condition.compare ?? '>'}
            options={COMPARE_OPTIONS}
            onChange={(compare) => onChange({ ...condition, compare })}
          />
          <NumberField
            label="观察周期"
            min={1}
            value={condition.lookback ?? 1}
            onChange={(lookback) => onChange({ ...condition, lookback })}
          />
          {condition.operator === 'frequency' ? (
            <NumberField
              label="至少次数"
              min={1}
              value={condition.minCount ?? 1}
              onChange={(minCount) => onChange({ ...condition, minCount })}
            />
          ) : null}
        </div>
      ) : null}

      {condition.operator === 'trend_up' || condition.operator === 'trend_down' || condition.operator === 'new_high' || condition.operator === 'new_low' ? (
        <div className="mt-3 max-w-xs">
          <NumberField
            label="观察周期"
            min={1}
            value={condition.lookback ?? (condition.operator.startsWith('new_') ? 20 : 3)}
            onChange={(lookback) => onChange({ ...condition, lookback })}
          />
        </div>
      ) : null}

      {canUseRightValue(condition.operator) && condition.right ? (
        <div className="mt-3">
          <ValueEditor
            value={condition.right}
            metrics={metrics}
            onChange={(right) => onChange({ ...condition, right })}
          />
        </div>
      ) : null}
    </div>
  );
}

function RuleList({
  rules,
  selectedRuleId,
  onSelect,
  onCreate,
}: {
  rules: RuleItem[];
  selectedRuleId: number | null;
  onSelect: (rule: RuleItem) => void;
  onCreate: () => void;
}) {
  return (
    <aside className={PANEL_CLASS}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <span className="label-uppercase">Rule List</span>
          <h2 className="mt-1 text-lg font-semibold text-foreground">规则列表</h2>
        </div>
        <Button variant="secondary" size="sm" onClick={onCreate}>
          <Plus className="h-4 w-4" />
          新建
        </Button>
      </div>
      <div className="space-y-2">
        {rules.length === 0 ? (
          <EmptyState title="暂无规则" description="创建第一条观察规则后，可以手动运行并查看命中结果。" className="border-dashed" />
        ) : null}
        {rules.map((rule) => (
          <button
            key={rule.id}
            type="button"
            onClick={() => onSelect(rule)}
            className={`w-full rounded-xl border px-3 py-3 text-left transition-all ${
              selectedRuleId === rule.id
                ? 'border-primary/50 bg-primary/10 text-foreground'
                : 'border-border/50 bg-elevated/35 text-secondary-text hover:bg-hover hover:text-foreground'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-sm font-semibold">{rule.name}</span>
              <Badge variant={rule.isActive ? 'success' : 'default'}>{rule.isActive ? '启用' : '停用'}</Badge>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
              <span>命中 {rule.lastMatchCount}</span>
              <span>{rule.lastRunAt ? rule.lastRunAt.slice(0, 16).replace('T', ' ') : '未运行'}</span>
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}

const RulesPage: React.FC = () => {
  useEffect(() => {
    document.title = '规则 - DSA';
  }, []);

  const {
    index: stockIndex,
    loading: isLoadingStockIndex,
    error: stockIndexError,
  } = useStockIndex();
  const [rules, setRules] = useState<RuleItem[]>([]);
  const [metrics, setMetrics] = useState<RuleMetricItem[]>([]);
  const [selectedRuleId, setSelectedRuleId] = useState<number | null>(null);
  const [name, setName] = useState('放量观察');
  const [description, setDescription] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [definition, setDefinition] = useState<RuleDefinition>(() => createDefinition());
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<RuleRunResponse | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [watchlistItems, setWatchlistItems] = useState<StockListDisplayItem[]>([]);
  const [isStockListExpanded, setIsStockListExpanded] = useState(false);
  const [stockListFilter, setStockListFilter] = useState('');
  const watchlistCodes = useMemo(
    () => watchlistItems.map((item) => item.code),
    [watchlistItems],
  );

  const metricOptions = useMemo(
    () => metrics.map((metric) => ({ value: metric.key, label: metric.label })),
    [metrics],
  );
  const stockLookup = useMemo(() => buildStockLookup(stockIndex, watchlistItems), [stockIndex, watchlistItems]);
  const allAshareCodes = useMemo(
    () => Array.from(new Set(stockIndex
      .filter(isAllAshareItem)
      .sort(compareStockListItems)
      .map(getStockDisplayCode))),
    [stockIndex],
  );

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [metricItems, ruleItems, config, history] = await Promise.all([
        rulesApi.getMetrics(),
        rulesApi.list(),
        systemConfigApi.getConfig(false),
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
      const nextWatchlistItems = buildCurrentWatchlistItems(
        configuredWatchlistCodes,
        history.items,
      );
      const nextWatchlistCodes = nextWatchlistItems.map((item) => item.code);
      setWatchlistItems(nextWatchlistItems);
      setMetrics(metricItems);
      setRules(ruleItems);
      if (ruleItems.length > 0) {
        const first = ruleItems[0];
        setSelectedRuleId(first.id);
        setName(first.name);
        setDescription(first.description ?? '');
        setIsActive(first.isActive);
        setDefinition(hydrateDefinitionTarget(first.definition, nextWatchlistCodes, []));
      }
      if (ruleItems.length === 0 && metricItems.length > 0) {
        setDefinition(createDefinition(metricItems[0].key, nextWatchlistCodes));
      }
    } catch (err) {
      setError(getParsedApiError(err).message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    setDefinition((current) => hydrateDefinitionTarget(current, watchlistCodes, allAshareCodes));
  }, [allAshareCodes, watchlistCodes]);

  useEffect(() => {
    if (!isStockListExpanded) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return;
      }
      event.preventDefault();
      setIsStockListExpanded(false);
      setStockListFilter('');
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isStockListExpanded]);

  const selectRule = (rule: RuleItem) => {
    setSelectedRuleId(rule.id);
    setName(rule.name);
    setDescription(rule.description ?? '');
    setIsActive(rule.isActive);
    setDefinition(hydrateDefinitionTarget(rule.definition, watchlistCodes, allAshareCodes));
    setRunResult(null);
    setFeedback(null);
    setError(null);
  };

  const createNewRule = () => {
    const metric = metrics[0]?.key ?? 'close';
    setSelectedRuleId(null);
    setName('新规则');
    setDescription('');
    setIsActive(true);
    setDefinition(createDefinition(metric, watchlistCodes));
    setRunResult(null);
    setFeedback(null);
    setError(null);
    setShowDeleteConfirm(false);
  };

  const updateGroup = (groupIndex: number, nextGroup: RuleGroup) => {
    setDefinition((current) => ({
      ...current,
      groups: current.groups.map((group, index) => (index === groupIndex ? nextGroup : group)),
    }));
  };

  const removeGroup = (groupIndex: number) => {
    setDefinition((current) => ({
      ...current,
      groups: current.groups.filter((_, index) => index !== groupIndex),
    }));
  };

  const buildRulePayload = () => ({
    name: name.trim() || '未命名规则',
    description: description.trim() || null,
    isActive,
    definition,
  });

  const saveRule = async () => {
    setIsSaving(true);
    setError(null);
    setFeedback(null);
    try {
      const payload = buildRulePayload();
      const saved = selectedRuleId
        ? await rulesApi.update(selectedRuleId, payload)
        : await rulesApi.create(payload);
      setSelectedRuleId(saved.id);
      setFeedback('规则已保存。');
      const nextRules = await rulesApi.list();
      setRules(nextRules);
    } catch (err) {
      setError(getParsedApiError(err).message);
    } finally {
      setIsSaving(false);
    }
  };

  const confirmDeleteRule = async () => {
    if (!selectedRuleId) return;
    setShowDeleteConfirm(false);
    setError(null);
    try {
      await rulesApi.delete(selectedRuleId);
      setFeedback('规则已删除。');
      const nextRules = await rulesApi.list();
      setRules(nextRules);
      if (nextRules.length > 0) {
        selectRule(nextRules[0]);
      } else {
        createNewRule();
      }
    } catch (err) {
      setError(getParsedApiError(err).message);
    }
  };

  const runRule = async () => {
    if (!selectedRuleId) return;
    setIsRunning(true);
    setError(null);
    setFeedback(null);
    try {
      const saved = await rulesApi.update(selectedRuleId, buildRulePayload());
      setName(saved.name);
      setDescription(saved.description ?? '');
      setIsActive(saved.isActive);
      setDefinition(saved.definition);
      const result = await rulesApi.run(selectedRuleId);
      const dateCount = result.matches.reduce((count, match) => count + match.matchedDates.length, 0);
      setRunResult(result);
      setFeedback(`运行完成：扫描 ${result.targetCount} 只，命中 ${result.matchCount} 只，命中 ${dateCount} 个交易日。`);
      setRules(await rulesApi.list());
    } catch (err) {
      setError(getParsedApiError(err).message);
    } finally {
      setIsRunning(false);
    }
  };

  const targetCodesText = formatStockListText(definition.target.stockCodes, stockLookup);
  const targetListReadOnly = definition.target.scope !== 'custom';
  const stockListItems = useMemo(
    () => buildStockListLineItems(definition.target.stockCodes, stockLookup),
    [definition.target.stockCodes, stockLookup],
  );
  const filteredStockListItems = useMemo(
    () => filterStockListItems(stockListItems, stockListFilter),
    [stockListFilter, stockListItems],
  );
  const hasStockListFilter = stockListFilter.trim().length > 0;
  const updateTargetCodesFromText = useCallback((value: string) => {
    setDefinition((current) => ({
      ...current,
      target: {
        ...current.target,
        stockCodes: parseStockCodes(value),
      },
    }));
  }, []);
  const removeTargetStockCode = useCallback((sourceCode: string) => {
    setDefinition((current) => ({
      ...current,
      target: {
        ...current.target,
        stockCodes: current.target.stockCodes.filter((code) => code !== sourceCode),
      },
    }));
  }, []);
  const closeStockListExpanded = useCallback(() => {
    setIsStockListExpanded(false);
    setStockListFilter('');
  }, []);
  const scopeOptions: Array<{ value: RuleTargetScope; label: string }> = [
    { value: 'watchlist', label: '自选股 STOCK_LIST' },
    { value: 'all_a_shares', label: '所有 A 股' },
    { value: 'custom', label: '自定义股票列表' },
  ];
  const matchedDateCount = runResult
    ? runResult.matches.reduce((count, match) => count + match.matchedDates.length, 0)
    : 0;

  return (
    <AppPage className="max-w-[1500px]">
      <PageHeader
        eyebrow="Rules"
        title="规则"
        description="用条件组配置观察规则。条件组之间是或关系，组内子条件是且关系；上穿/下穿暂不纳入本版。"
        actions={(
          <>
            <Button variant="secondary" size="md" onClick={createNewRule}>
              <CopyPlus className="h-4 w-4" />
              新建规则
            </Button>
            <Button variant="primary" size="md" onClick={saveRule} isLoading={isSaving}>
              <Save className="h-4 w-4" />
              保存
            </Button>
          </>
        )}
      />

      <div className="mt-4 grid gap-4 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <RuleList
          rules={rules}
          selectedRuleId={selectedRuleId}
          onSelect={selectRule}
          onCreate={createNewRule}
        />

        <div className="space-y-4">
          {error ? <InlineAlert variant="danger" title="操作失败" message={error} /> : null}
          {feedback ? <InlineAlert variant="success" message={feedback} /> : null}
          {isLoading ? <InlineAlert variant="info" message="正在加载规则模块..." /> : null}

          <section className={PANEL_CLASS}>
            <div className="mb-4 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <ListFilter className="h-5 w-5 text-primary" />
                <h2 className="text-lg font-semibold text-foreground">基础设置</h2>
              </div>
              <label className="flex items-center gap-2 text-sm text-secondary-text">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(event) => setIsActive(event.target.checked)}
                  className="h-4 w-4 accent-cyan"
                />
                启用
              </label>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <Input label="规则名称" value={name} onChange={(event) => setName(event.target.value)} />
              <NumberField
                label="历史窗口天数"
                min={20}
                value={definition.lookbackDays}
                onChange={(lookbackDays) => setDefinition((current) => ({ ...current, lookbackDays }))}
              />
              <label className="flex flex-col gap-1 text-sm font-medium text-foreground md:col-span-2">
                描述
                <textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  className={TEXTAREA_CLASS}
                  placeholder="说明这条规则希望捕捉的市场状态"
                />
              </label>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-[16rem_minmax(0,1fr)]">
              <SelectField
                label="股票范围"
                value={definition.target.scope}
                options={scopeOptions}
                onChange={(scope) => setDefinition((current) => ({
                  ...current,
                  target: {
                    ...current.target,
                    scope,
                    stockCodes: scope === 'watchlist'
                      ? watchlistCodes
                      : scope === 'all_a_shares'
                        ? allAshareCodes
                        : current.target.stockCodes,
                  },
                }))}
              />
              <div className="flex min-w-0 flex-col gap-1 text-xs text-muted-text">
                <span>股票代码 / 股票名称</span>
                <div className="relative">
                  <textarea
                    aria-label="股票代码 / 股票名称"
                    value={targetCodesText}
                    onChange={(event) => updateTargetCodesFromText(event.target.value)}
                    readOnly={targetListReadOnly}
                    className={`${STOCK_CODES_TEXTAREA_CLASS} pr-12`}
                    style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
                    placeholder="600519 贵州茅台&#10;000001 平安银行&#10;AAPL Apple"
                  />
                  <button
                    type="button"
                    aria-label="最大化股票列表"
                    title="最大化股票列表"
                    onClick={() => setIsStockListExpanded(true)}
                    className="absolute right-2 top-2 inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border/70 bg-card/90 text-secondary-text shadow-soft-card transition-all hover:border-primary/50 hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-cyan/15"
                  >
                    <Maximize2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <span className="text-[11px] text-muted-text">
                  {definition.target.stockCodes.length} 只股票
                  {definition.target.scope === 'all_a_shares' && isLoadingStockIndex ? '，正在加载 A 股列表...' : ''}
                  {definition.target.scope === 'all_a_shares' && stockIndexError ? '，A 股列表加载失败' : ''}
                </span>
              </div>
            </div>
          </section>

          <section className={PANEL_CLASS}>
            <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <span className="label-uppercase">Condition Groups</span>
                <h2 className="mt-1 text-lg font-semibold text-foreground">条件设置</h2>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setDefinition((current) => ({
                  ...current,
                  groups: [...current.groups, createGroup(metrics[0]?.key ?? 'close')],
                }))}
              >
                <Plus className="h-4 w-4" />
                添加条件组
              </Button>
            </div>

            <div className="space-y-4">
              {definition.groups.map((group, groupIndex) => (
                <div key={group.id} className="rounded-2xl border border-primary/20 bg-primary/5 p-4">
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <div>
                      <Badge variant="info">OR 条件组 {groupIndex + 1}</Badge>
                      <p className="mt-2 text-xs text-secondary-text">组内 {group.conditions.length} 个子条件全部满足才命中。</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => updateGroup(groupIndex, {
                          ...group,
                          conditions: [...group.conditions, createCondition(metrics[0]?.key ?? 'close')],
                        })}
                      >
                        <Plus className="h-4 w-4" />
                        子条件
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeGroup(groupIndex)}
                        disabled={definition.groups.length <= 1}
                        aria-label="删除条件组"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-3">
                    {group.conditions.map((condition, conditionIndex) => (
                      <ConditionEditor
                        key={condition.id}
                        condition={condition}
                        metrics={metrics}
                        onChange={(nextCondition) => updateGroup(groupIndex, {
                          ...group,
                          conditions: group.conditions.map((item, index) => (
                            index === conditionIndex ? nextCondition : item
                          )),
                        })}
                        onRemove={() => updateGroup(groupIndex, {
                          ...group,
                          conditions: group.conditions.filter((_, index) => index !== conditionIndex),
                        })}
                      />
                    ))}
                  </div>

                  <div className="mt-3 rounded-xl border border-border/50 bg-card/70 px-3 py-2 text-xs text-secondary-text">
                    {group.conditions.map((condition) => conditionSummary(metrics, condition)).join(' 且 ')}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className={PANEL_CLASS}>
            <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-2">
                <Activity className="h-5 w-5 text-primary" />
                <div>
                  <h2 className="text-lg font-semibold text-foreground">运行结果</h2>
                  <p className="text-sm text-secondary-text">保存规则后可手动运行，结果会记录到后端。</p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="danger-subtle" size="sm" onClick={() => setShowDeleteConfirm(true)} disabled={!selectedRuleId}>
                  <Trash2 className="h-4 w-4" />
                  删除
                </Button>
                <Button variant="primary" size="sm" onClick={runRule} disabled={!selectedRuleId} isLoading={isRunning} loadingText="运行中...">
                  <Play className="h-4 w-4" />
                  运行
                </Button>
              </div>
            </div>

            {!runResult ? (
              <EmptyState title="暂无运行结果" description="选择已保存规则后点击运行，命中股票会展示在这里。" className="border-dashed" />
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant={runResult.status === 'completed' ? 'success' : 'warning'}>{runResult.status}</Badge>
                  <span className="text-secondary-text">扫描 {runResult.targetCount} 只</span>
                  <span className="text-secondary-text">命中 {runResult.matchCount} 只</span>
                  <span className="text-secondary-text">命中 {matchedDateCount} 个交易日</span>
                  <span className="text-secondary-text">耗时 {runResult.durationMs} ms</span>
                </div>
                {runResult.errors.length > 0 ? (
                  <InlineAlert variant="warning" message={`部分股票运行失败：${runResult.errors.join('；')}`} />
                ) : null}
                {runResult.matches.length === 0 ? (
                  <EmptyState title="未命中" description="本次运行没有股票满足任一条件组。" className="border-dashed" />
                ) : (
                  <div className="overflow-x-auto rounded-xl border border-border/60">
                    <table className="min-w-[760px] w-full text-sm">
                      <thead className="bg-elevated/70 text-left text-xs uppercase text-muted-text">
                        <tr>
                          <th className="px-3 py-2">股票</th>
                          <th className="px-3 py-2">命中日期</th>
                          <th className="px-3 py-2">快照</th>
                          <th className="px-3 py-2">命中解释</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runResult.matches.map((match) => (
                          <tr key={match.stockCode} className="border-t border-border/50">
                            <td className="px-3 py-3">
                              <div className="font-semibold text-foreground">{match.stockName || match.stockCode}</div>
                              <div className="font-mono text-xs text-muted-text">{match.stockCode}</div>
                            </td>
                            <td className="px-3 py-3">
                              <div className="flex max-w-[260px] flex-wrap gap-1">
                                {match.matchedDates.length > 0 ? match.matchedDates.map((date) => (
                                  <Badge key={`${match.stockCode}-${date}`} variant="info">{date}</Badge>
                                )) : <span className="text-secondary-text">--</span>}
                              </div>
                            </td>
                            <td className="px-3 py-3 text-xs text-secondary-text">
                              {Object.entries(match.snapshot).map(([key, value]) => (
                                <span key={key} className="mr-3 inline-block">
                                  {key}: {formatNumber(value)}
                                </span>
                              ))}
                            </td>
                            <td className="px-3 py-3 text-secondary-text">{match.explanation || '--'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      </div>

      {metricOptions.length === 0 ? null : <span className="sr-only">已加载 {metricOptions.length} 个指标 key</span>}

      {isStockListExpanded ? (
        <div
          className="fixed inset-0 z-[70] flex items-stretch justify-center bg-background/75 p-2 backdrop-blur-sm md:p-5"
          role="dialog"
          aria-modal="true"
          aria-label="股票列表最大化"
          onClick={closeStockListExpanded}
        >
          <div
            className="glass-card flex min-h-0 w-full max-w-7xl flex-col overflow-hidden shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex flex-col gap-3 border-b border-border/60 p-3 md:flex-row md:items-center md:justify-between md:p-4">
              <div>
                <h2 className="text-lg font-semibold text-foreground">股票代码 / 股票名称</h2>
                <p className="mt-1 text-xs text-muted-text">
                  {hasStockListFilter ? `${filteredStockListItems.length} / ` : ''}
                  {definition.target.stockCodes.length} 只股票
                </p>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
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
                aria-label="最大化股票列表内容"
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

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title="删除规则"
        message={`确认删除规则「${name || '未命名规则'}」吗？相关运行记录也会一并删除。`}
        confirmText="确认删除"
        cancelText="取消"
        isDanger
        onConfirm={() => void confirmDeleteRule()}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </AppPage>
  );
};

export default RulesPage;
