import apiClient from './index';
import type {
  TenantConfig,
  TenantConfigUpdatePayload,
  TenantCreatePayload,
  TenantItem,
  TenantUpdatePayload,
} from '../types/tenants';

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
  return Array.isArray(value) ? value.map(toString).filter(Boolean) : [];
}

function normalizeTenant(raw: Record<string, unknown>): TenantItem {
  return {
    id: toNumber(raw.id),
    key: toString(raw.key),
    name: toString(raw.name),
    description: toNullableString(raw.description),
    isActive: toBoolean(raw.is_active ?? raw.isActive),
    isDefault: toBoolean(raw.is_default ?? raw.isDefault),
    createdAt: toNullableString(raw.created_at ?? raw.createdAt),
    updatedAt: toNullableString(raw.updated_at ?? raw.updatedAt),
  };
}

function normalizeConfig(raw: Record<string, unknown>): TenantConfig {
  const tenant = raw.tenant && typeof raw.tenant === 'object'
    ? raw.tenant as Record<string, unknown>
    : {};
  return {
    tenant: {
      id: toNumber(tenant.id),
      key: toString(tenant.key),
      name: toString(tenant.name),
      isDefault: toBoolean(tenant.is_default ?? tenant.isDefault),
    },
    maskToken: toString(raw.mask_token ?? raw.maskToken ?? '******'),
    stockList: toStringArray(raw.stock_list ?? raw.stockList),
    feishuWebhookUrl: toString(raw.feishu_webhook_url ?? raw.feishuWebhookUrl),
    feishuWebhookSecret: toString(raw.feishu_webhook_secret ?? raw.feishuWebhookSecret),
    feishuWebhookSecretExists: toBoolean(raw.feishu_webhook_secret_exists ?? raw.feishuWebhookSecretExists),
    feishuWebhookKeyword: toString(raw.feishu_webhook_keyword ?? raw.feishuWebhookKeyword),
    feishuMaxBytes: toNumber(raw.feishu_max_bytes ?? raw.feishuMaxBytes, 20000),
  };
}

function serializeConfig(payload: TenantConfigUpdatePayload): Record<string, unknown> {
  return {
    stock_list: payload.stockList,
    feishu_webhook_url: payload.feishuWebhookUrl,
    feishu_webhook_secret: payload.feishuWebhookSecret,
    feishu_webhook_keyword: payload.feishuWebhookKeyword,
    feishu_max_bytes: payload.feishuMaxBytes,
    mask_token: payload.maskToken,
  };
}

export const tenantsApi = {
  async list(includeInactive = false): Promise<TenantItem[]> {
    const response = await apiClient.get<{ items?: Array<Record<string, unknown>> }>('/api/v1/tenants', {
      params: { include_inactive: includeInactive },
    });
    return (response.data.items ?? []).map(normalizeTenant);
  },

  async create(payload: TenantCreatePayload): Promise<TenantItem> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/tenants', {
      key: payload.key,
      name: payload.name,
      description: payload.description,
    });
    return normalizeTenant(response.data);
  },

  async update(tenantKey: string, payload: TenantUpdatePayload): Promise<TenantItem> {
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/tenants/${encodeURIComponent(tenantKey)}`,
      {
        name: payload.name,
        description: payload.description,
        is_active: payload.isActive,
      },
    );
    return normalizeTenant(response.data);
  },

  async deactivate(tenantKey: string): Promise<void> {
    await apiClient.delete(`/api/v1/tenants/${encodeURIComponent(tenantKey)}`);
  },

  async getConfig(tenantKey: string): Promise<TenantConfig> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/tenants/${encodeURIComponent(tenantKey)}/config`,
    );
    return normalizeConfig(response.data);
  },

  async updateConfig(tenantKey: string, payload: TenantConfigUpdatePayload): Promise<TenantConfig> {
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/tenants/${encodeURIComponent(tenantKey)}/config`,
      serializeConfig(payload),
    );
    return normalizeConfig(response.data);
  },
};
