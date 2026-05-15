export type RuleTargetScope = 'watchlist' | 'all_a_shares' | 'custom';
export type RulePeriod = 'daily';
export type RuleRunMode = 'latest' | 'history';
export type RuleDataPolicy = 'default' | 'snapshot_only' | 'cache_only' | 'db_only';
export type RuleAggregateMethod = 'max' | 'min' | 'avg' | 'sum' | 'median' | 'std';
export type RuleOperator =
  | '>'
  | '>='
  | '<'
  | '<='
  | '='
  | '!='
  | 'between'
  | 'not_between'
  | 'consecutive'
  | 'frequency'
  | 'trend_up'
  | 'trend_down'
  | 'new_high'
  | 'new_low'
  | 'exists'
  | 'not_exists'
  | 'sandwich_number'
  | 'pair_number';
export type RuleCompareOperator = '>' | '>=' | '<' | '<=' | '=' | '!=';

export type RuleMetricExpression = {
  metric: string;
  offset?: number;
};

export type RuleValueExpression =
  | {
      type: 'literal';
      value: number;
      multiplier?: number;
    }
  | {
      type: 'metric';
      metric: string;
      offset?: number;
      multiplier?: number;
    }
  | {
      type: 'aggregate';
      metric: string;
      method: RuleAggregateMethod;
      window: number;
      offset?: number;
      multiplier?: number;
    }
  | {
      type: 'range';
      min: RuleValueExpression;
      max: RuleValueExpression;
    };

export type RuleCondition = {
  id: string;
  left: RuleMetricExpression;
  operator: RuleOperator;
  right?: RuleValueExpression;
  compare?: RuleCompareOperator;
  lookback?: number;
  minCount?: number;
};

export type RuleGroup = {
  id: string;
  conditions: RuleCondition[];
};

export type RuleDefinition = {
  period: RulePeriod;
  lookbackDays: number;
  target: {
    scope: RuleTargetScope;
    stockCodes: string[];
  };
  groups: RuleGroup[];
};

export type RuleItem = {
  id: number;
  name: string;
  description?: string | null;
  isActive: boolean;
  period: RulePeriod | string;
  lookbackDays: number;
  targetScope: RuleTargetScope | string;
  targetCodes: string[];
  definition: RuleDefinition;
  createdAt?: string | null;
  updatedAt?: string | null;
  lastRunAt?: string | null;
  lastMatchCount: number;
};

export type RuleMetricItem = {
  key: string;
  label: string;
  category: string;
  valueType: string;
  unit?: string | null;
  periods: string[];
  description?: string;
};

export type RuleMatchItem = {
  runId?: number;
  ruleId?: number;
  stockCode: string;
  stockName?: string | null;
  matchedDates: string[];
  matchedEvents: Array<Record<string, unknown>>;
  matchedGroups: Array<Record<string, unknown>>;
  snapshot: Record<string, unknown>;
  explanation?: string | null;
};

export type RuleRunResponse = {
  runId: number;
  ruleId: number;
  ruleIds?: number[];
  ruleNames?: string[];
  status: string;
  targetCount: number;
  completedCount?: number;
  matchCount: number;
  eventCount: number;
  mode: RuleRunMode | string;
  durationMs: number;
  matches: RuleMatchItem[];
  errors: string[];
  snapshotId?: string | null;
  snapshotTime?: string | null;
  snapshotAgeSeconds?: number | null;
  quoteHitCount?: number;
  quoteMissCount?: number;
};

export type RuleRunNotifyPayload = {
  executionTime?: string | null;
  ruleIds?: number[];
  ruleNames?: string[];
  compact?: boolean;
};

export type RuleRunNotifyResponse = {
  sent: boolean;
  message: string;
  matchCount: number;
  eventCount: number;
  deduplicated?: boolean;
  compact?: boolean;
  originalEventCount?: number;
};

export type RuleRunPayload = {
  mode?: RuleRunMode;
  dataPolicy?: RuleDataPolicy;
  target?: {
    scope: RuleTargetScope;
    stockCodes: string[];
  };
  startDate?: string;
  endDate?: string;
};

export type RuleBatchRunPayload = RuleRunPayload & {
  ruleIds: number[];
};

export type RuleRunHistoryItem = {
  id: number;
  runIds?: number[];
  ruleId: number;
  ruleIds?: number[];
  ruleName?: string | null;
  ruleNames?: string[];
  status: string;
  targetCount: number;
  completedCount?: number;
  matchCount: number;
  eventCount?: number;
  error?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  durationMs?: number | null;
};

export type RuleCreatePayload = {
  name: string;
  description?: string | null;
  isActive: boolean;
  definition: RuleDefinition;
};

export type RuleUpdatePayload = Partial<RuleCreatePayload>;
