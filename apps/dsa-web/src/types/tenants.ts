export type TenantItem = {
  id: number;
  key: string;
  name: string;
  description?: string | null;
  isActive: boolean;
  isDefault: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type TenantConfig = {
  tenant: {
    id: number;
    key: string;
    name: string;
    isDefault: boolean;
  };
  maskToken: string;
  stockList: string[];
  feishuWebhookUrl: string;
  feishuWebhookSecret: string;
  feishuWebhookSecretExists: boolean;
  feishuWebhookKeyword: string;
  feishuMaxBytes: number;
};

export type TenantCreatePayload = {
  key: string;
  name: string;
  description?: string | null;
};

export type TenantUpdatePayload = {
  name?: string;
  description?: string | null;
  isActive?: boolean;
};

export type TenantConfigUpdatePayload = {
  stockList?: string[] | string;
  feishuWebhookUrl?: string;
  feishuWebhookSecret?: string;
  feishuWebhookKeyword?: string;
  feishuMaxBytes?: number;
  maskToken?: string;
};
