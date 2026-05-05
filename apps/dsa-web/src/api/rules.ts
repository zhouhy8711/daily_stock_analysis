import apiClient from './index';
import type {
  RuleCondition,
  RuleCreatePayload,
  RuleAggregateMethod,
  RuleDefinition,
  RuleGroup,
  RuleItem,
  RuleBatchRunPayload,
  RuleMatchItem,
  RuleMetricItem,
  RuleRunHistoryItem,
  RuleRunPayload,
  RuleRunResponse,
  RuleTargetScope,
  RuleUpdatePayload,
  RuleValueExpression,
} from '../types/rules';

const RULE_RUN_TIMEOUT_MS = 10 * 60 * 1000;

function toString(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '');
}

function toNullableString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function toNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function toBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(toString) : [];
}

function toNumberArray(value: unknown): number[] | undefined {
  return Array.isArray(value) ? value.map((item) => toNumber(item)) : undefined;
}

function normalizeValueExpression(raw: unknown): RuleValueExpression {
  const item = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const type = toString(item.type || 'literal');
  if (type === 'metric') {
    return {
      type: 'metric',
      metric: toString(item.metric),
      offset: toNumber(item.offset),
      multiplier: item.multiplier == null ? undefined : toNumber(item.multiplier, 1),
    };
  }
  if (type === 'aggregate') {
    return {
      type: 'aggregate',
      metric: toString(item.metric),
      method: toString(item.method || 'avg') as RuleAggregateMethod,
      window: Math.max(1, toNumber(item.window, 5)),
      offset: toNumber(item.offset, 1),
      multiplier: item.multiplier == null ? undefined : toNumber(item.multiplier, 1),
    };
  }
  if (type === 'range') {
    return {
      type: 'range',
      min: normalizeValueExpression(item.min),
      max: normalizeValueExpression(item.max),
    };
  }
  return {
    type: 'literal',
    value: toNumber(item.value),
    multiplier: item.multiplier == null ? undefined : toNumber(item.multiplier, 1),
  };
}

function serializeValueExpression(expr: RuleValueExpression | undefined): Record<string, unknown> | undefined {
  if (!expr) {
    return undefined;
  }
  if (expr.type === 'range') {
    return {
      type: 'range',
      min: serializeValueExpression(expr.min),
      max: serializeValueExpression(expr.max),
    };
  }
  return { ...expr };
}

function normalizeCondition(raw: unknown): RuleCondition {
  const item = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const left = item.left && typeof item.left === 'object' ? item.left as Record<string, unknown> : {};
  return {
    id: toString(item.id),
    left: {
      metric: toString(left.metric || 'close'),
      offset: toNumber(left.offset),
    },
    operator: toString(item.operator || '>') as RuleCondition['operator'],
    right: item.right ? normalizeValueExpression(item.right) : undefined,
    compare: item.compare ? toString(item.compare) as RuleCondition['compare'] : undefined,
    lookback: item.lookback == null ? undefined : toNumber(item.lookback),
    minCount: item.min_count == null && item.minCount == null ? undefined : toNumber(item.min_count ?? item.minCount),
  };
}

function serializeCondition(condition: RuleCondition): Record<string, unknown> {
  return {
    id: condition.id,
    left: condition.left,
    operator: condition.operator,
    right: serializeValueExpression(condition.right),
    compare: condition.compare,
    lookback: condition.lookback,
    min_count: condition.minCount,
  };
}

function normalizeDefinition(raw: unknown): RuleDefinition {
  const item = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const target = item.target && typeof item.target === 'object' ? item.target as Record<string, unknown> : {};
  const groups = Array.isArray(item.groups) ? item.groups : [];
  const targetCodes = target.stock_codes ?? target.stockCodes;
  return {
    period: 'daily',
    lookbackDays: toNumber(item.lookback_days ?? item.lookbackDays, 120),
    target: {
      scope: toString(target.scope || 'watchlist') as RuleDefinition['target']['scope'],
      stockCodes: toStringArray(targetCodes),
    },
    groups: groups.map((group): RuleGroup => {
      const groupItem = group && typeof group === 'object' ? group as Record<string, unknown> : {};
      const conditions = Array.isArray(groupItem.conditions) ? groupItem.conditions : [];
      return {
        id: toString(groupItem.id),
        conditions: conditions.map(normalizeCondition),
      };
    }),
  };
}

function serializeDefinition(definition: RuleDefinition): Record<string, unknown> {
  return {
    period: definition.period,
    lookback_days: definition.lookbackDays,
    target: {
      scope: definition.target.scope,
      stock_codes: definition.target.stockCodes,
    },
    groups: definition.groups.map((group) => ({
      id: group.id,
      conditions: group.conditions.map(serializeCondition),
    })),
  };
}

function normalizeRule(raw: Record<string, unknown>): RuleItem {
  return {
    id: toNumber(raw.id),
    name: toString(raw.name),
    description: toNullableString(raw.description),
    isActive: toBoolean(raw.is_active ?? raw.isActive),
    period: toString(raw.period || 'daily'),
    lookbackDays: toNumber(raw.lookback_days ?? raw.lookbackDays, 120),
    targetScope: toString(raw.target_scope ?? raw.targetScope ?? 'watchlist'),
    targetCodes: toStringArray(raw.target_codes ?? raw.targetCodes),
    definition: normalizeDefinition(raw.definition),
    createdAt: toNullableString(raw.created_at ?? raw.createdAt),
    updatedAt: toNullableString(raw.updated_at ?? raw.updatedAt),
    lastRunAt: toNullableString(raw.last_run_at ?? raw.lastRunAt),
    lastMatchCount: toNumber(raw.last_match_count ?? raw.lastMatchCount),
  };
}

function serializePayload(payload: RuleCreatePayload | RuleUpdatePayload): Record<string, unknown> {
  return {
    name: payload.name,
    description: payload.description,
    is_active: payload.isActive,
    definition: payload.definition ? serializeDefinition(payload.definition) : undefined,
  };
}

function serializeRunTarget(target: { scope: RuleTargetScope; stockCodes: string[] }): Record<string, unknown> {
  return {
    scope: target.scope,
    stock_codes: target.stockCodes,
  };
}

function normalizeMatch(raw: Record<string, unknown>): RuleMatchItem {
  const matchedGroups = raw.matched_groups ?? raw.matchedGroups;
  const matchedDates = raw.matched_dates ?? raw.matchedDates;
  const matchedEvents = raw.matched_events ?? raw.matchedEvents;
  return {
    runId: raw.run_id == null && raw.runId == null ? undefined : toNumber(raw.run_id ?? raw.runId),
    ruleId: raw.rule_id == null && raw.ruleId == null ? undefined : toNumber(raw.rule_id ?? raw.ruleId),
    stockCode: toString(raw.stock_code ?? raw.stockCode),
    stockName: toNullableString(raw.stock_name ?? raw.stockName),
    matchedDates: toStringArray(matchedDates),
    matchedEvents: Array.isArray(matchedEvents) ? matchedEvents as Array<Record<string, unknown>> : [],
    matchedGroups: Array.isArray(matchedGroups) ? matchedGroups as Array<Record<string, unknown>> : [],
    snapshot: raw.snapshot && typeof raw.snapshot === 'object'
      ? raw.snapshot as Record<string, unknown>
      : {},
    explanation: toNullableString(raw.explanation),
  };
}

function normalizeRunHistory(raw: Record<string, unknown>): RuleRunHistoryItem {
  return {
    id: toNumber(raw.id),
    runIds: toNumberArray(raw.run_ids ?? raw.runIds),
    ruleId: toNumber(raw.rule_id ?? raw.ruleId),
    ruleIds: toNumberArray(raw.rule_ids ?? raw.ruleIds),
    ruleName: toNullableString(raw.rule_name ?? raw.ruleName),
    ruleNames: toStringArray(raw.rule_names ?? raw.ruleNames),
    status: toString(raw.status),
    targetCount: toNumber(raw.target_count ?? raw.targetCount),
    matchCount: toNumber(raw.match_count ?? raw.matchCount),
    eventCount: toNumber(raw.event_count ?? raw.eventCount),
    error: toNullableString(raw.error),
    startedAt: toNullableString(raw.started_at ?? raw.startedAt),
    finishedAt: toNullableString(raw.finished_at ?? raw.finishedAt),
    durationMs: raw.duration_ms == null && raw.durationMs == null ? null : toNumber(raw.duration_ms ?? raw.durationMs),
  };
}

export const rulesApi = {
  async getMetrics(): Promise<RuleMetricItem[]> {
    const response = await apiClient.get<{ items?: Array<Record<string, unknown>> }>('/api/v1/rules/metrics');
    return (response.data.items ?? []).map((item) => ({
      key: toString(item.key),
      label: toString(item.label),
      category: toString(item.category),
      valueType: toString(item.value_type ?? item.valueType ?? 'number'),
      unit: toNullableString(item.unit),
      periods: Array.isArray(item.periods) ? item.periods.map(toString) : [],
      description: toString(item.description),
    }));
  },

  async list(): Promise<RuleItem[]> {
    const response = await apiClient.get<{ items?: Array<Record<string, unknown>> }>('/api/v1/rules');
    return (response.data.items ?? []).map(normalizeRule);
  },

  async create(payload: RuleCreatePayload): Promise<RuleItem> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/rules', serializePayload(payload));
    return normalizeRule(response.data);
  },

  async update(ruleId: number, payload: RuleUpdatePayload): Promise<RuleItem> {
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/rules/${encodeURIComponent(String(ruleId))}`,
      serializePayload(payload),
    );
    return normalizeRule(response.data);
  },

  async delete(ruleId: number): Promise<void> {
    await apiClient.delete(`/api/v1/rules/${encodeURIComponent(String(ruleId))}`);
  },

  async listRuns(limit = 30): Promise<RuleRunHistoryItem[]> {
    const response = await apiClient.get<{ items?: Array<Record<string, unknown>> }>('/api/v1/rules/runs', {
      params: { limit },
    });
    return (response.data.items ?? []).map(normalizeRunHistory);
  },

  async getRunMatches(runId: number): Promise<RuleMatchItem[]> {
    const response = await apiClient.get<{ items?: Array<Record<string, unknown>> }>(
      `/api/v1/rules/runs/${encodeURIComponent(String(runId))}/matches`,
    );
    return (response.data.items ?? []).map(normalizeMatch);
  },

  async deleteRun(runId: number): Promise<void> {
    await apiClient.delete(`/api/v1/rules/runs/${encodeURIComponent(String(runId))}`);
  },

  async run(
    ruleId: number,
    payload?: RuleRunPayload,
  ): Promise<RuleRunResponse> {
    const requestPayload = {
      mode: payload?.mode,
      target: payload?.target ? serializeRunTarget(payload.target) : undefined,
      start_date: payload?.startDate || undefined,
      end_date: payload?.endDate || undefined,
    };
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/rules/${encodeURIComponent(String(ruleId))}/run`,
      requestPayload,
      { timeout: RULE_RUN_TIMEOUT_MS },
    );
    const rawMatches = Array.isArray(response.data.matches) ? response.data.matches : [];
    return {
      runId: toNumber(response.data.run_id ?? response.data.runId),
      ruleId: toNumber(response.data.rule_id ?? response.data.ruleId),
      ruleIds: toNumberArray(response.data.rule_ids ?? response.data.ruleIds),
      ruleNames: toStringArray(response.data.rule_names ?? response.data.ruleNames),
      status: toString(response.data.status),
      targetCount: toNumber(response.data.target_count ?? response.data.targetCount),
      matchCount: toNumber(response.data.match_count ?? response.data.matchCount),
      eventCount: toNumber(response.data.event_count ?? response.data.eventCount),
      mode: toString(response.data.mode || payload?.mode || 'history'),
      durationMs: toNumber(response.data.duration_ms ?? response.data.durationMs),
      matches: (rawMatches as Array<Record<string, unknown>>).map(normalizeMatch),
      errors: Array.isArray(response.data.errors) ? response.data.errors.map(toString) : [],
    };
  },

  async runBatch(payload: RuleBatchRunPayload): Promise<RuleRunResponse> {
    const requestPayload = {
      rule_ids: payload.ruleIds,
      mode: payload.mode,
      target: payload.target ? serializeRunTarget(payload.target) : undefined,
      start_date: payload.startDate || undefined,
      end_date: payload.endDate || undefined,
    };
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/rules/run-batch',
      requestPayload,
      { timeout: RULE_RUN_TIMEOUT_MS },
    );
    const rawMatches = Array.isArray(response.data.matches) ? response.data.matches : [];
    return {
      runId: toNumber(response.data.run_id ?? response.data.runId),
      ruleId: toNumber(response.data.rule_id ?? response.data.ruleId),
      ruleIds: toNumberArray(response.data.rule_ids ?? response.data.ruleIds),
      ruleNames: toStringArray(response.data.rule_names ?? response.data.ruleNames),
      status: toString(response.data.status),
      targetCount: toNumber(response.data.target_count ?? response.data.targetCount),
      matchCount: toNumber(response.data.match_count ?? response.data.matchCount),
      eventCount: toNumber(response.data.event_count ?? response.data.eventCount),
      mode: toString(response.data.mode || payload.mode || 'history'),
      durationMs: toNumber(response.data.duration_ms ?? response.data.durationMs),
      matches: (rawMatches as Array<Record<string, unknown>>).map(normalizeMatch),
      errors: Array.isArray(response.data.errors) ? response.data.errors.map(toString) : [],
    };
  },
};
