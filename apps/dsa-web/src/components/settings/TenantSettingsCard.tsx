import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Building2, Plus } from 'lucide-react';
import { tenantsApi } from '../../api/tenants';
import { getParsedApiError } from '../../api/error';
import { useTenant } from '../../contexts/TenantContext';
import type { TenantConfig } from '../../types/tenants';
import { Button } from '../common/Button';
import { InlineAlert } from '../common/InlineAlert';
import { Input } from '../common/Input';
import { SettingsSectionCard } from './SettingsSectionCard';

function joinStockList(config: TenantConfig | null): string {
  return (config?.stockList ?? []).join('\n');
}

export const TenantSettingsCard: React.FC = () => {
  const {
    tenants,
    currentTenant,
    tenantConfig,
    isLoading,
    switchLocked,
    setCurrentTenantKey,
    reloadTenants,
    reloadTenantConfig,
  } = useTenant();
  const [stockListText, setStockListText] = useState(joinStockList(tenantConfig));
  const [feishuWebhookUrl, setFeishuWebhookUrl] = useState('');
  const [feishuWebhookSecret, setFeishuWebhookSecret] = useState('');
  const [feishuWebhookKeyword, setFeishuWebhookKeyword] = useState('');
  const [feishuMaxBytes, setFeishuMaxBytes] = useState('20000');
  const [newTenantKey, setNewTenantKey] = useState('');
  const [newTenantName, setNewTenantName] = useState('');
  const [newTenantDescription, setNewTenantDescription] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    setStockListText(joinStockList(tenantConfig));
    setFeishuWebhookUrl(tenantConfig?.feishuWebhookUrl ?? '');
    setFeishuWebhookSecret(tenantConfig?.feishuWebhookSecret ?? '');
    setFeishuWebhookKeyword(tenantConfig?.feishuWebhookKeyword ?? '');
    setFeishuMaxBytes(String(tenantConfig?.feishuMaxBytes ?? 20000));
  }, [tenantConfig]);

  const tenantOptions = useMemo(
    () => tenants.map((tenant) => ({ value: tenant.key, label: tenant.name })),
    [tenants],
  );

  const saveConfig = async () => {
    if (!currentTenant || !tenantConfig) return;
    setIsSaving(true);
    setFeedback('');
    setError('');
    try {
      const saved = await tenantsApi.updateConfig(currentTenant.key, {
        stockList: stockListText,
        feishuWebhookUrl,
        feishuWebhookSecret,
        feishuWebhookKeyword,
        feishuMaxBytes: Number(feishuMaxBytes || 20000),
        maskToken: tenantConfig.maskToken,
      });
      setFeedback('租户配置已保存。');
      await reloadTenantConfig();
      setFeishuWebhookSecret(saved.feishuWebhookSecret);
    } catch (err) {
      setError(getParsedApiError(err).message);
    } finally {
      setIsSaving(false);
    }
  };

  const createTenant = async () => {
    setIsCreating(true);
    setFeedback('');
    setError('');
    try {
      const created = await tenantsApi.create({
        key: newTenantKey,
        name: newTenantName,
        description: newTenantDescription || null,
      });
      setNewTenantKey('');
      setNewTenantName('');
      setNewTenantDescription('');
      await reloadTenants();
      setCurrentTenantKey(created.key);
      setFeedback('租户已创建。');
    } catch (err) {
      setError(getParsedApiError(err).message);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <SettingsSectionCard
      title="租户管理"
      description="维护当前租户的规则股票池和飞书 Webhook。"
    >
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(18rem,0.55fr)]">
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-[minmax(10rem,18rem)_1fr]">
            <label className="flex flex-col gap-2 text-sm font-medium text-foreground">
              当前租户
              <select
                value={currentTenant?.key || ''}
                disabled={isLoading || switchLocked}
                onChange={(event) => setCurrentTenantKey(event.target.value)}
                className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-3 text-sm"
              >
                {tenantOptions.map((option) => (
                  <option key={option.value} value={option.value} className="bg-elevated text-foreground">
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <Input
              label="飞书 Webhook"
              value={feishuWebhookUrl}
              onChange={(event) => setFeishuWebhookUrl(event.target.value)}
              placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
            />
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <Input
              label="飞书 Secret"
              value={feishuWebhookSecret}
              onChange={(event) => setFeishuWebhookSecret(event.target.value)}
              placeholder={tenantConfig?.feishuWebhookSecretExists ? tenantConfig.maskToken : ''}
            />
            <Input
              label="飞书关键词"
              value={feishuWebhookKeyword}
              onChange={(event) => setFeishuWebhookKeyword(event.target.value)}
            />
            <Input
              label="飞书最大字节"
              type="number"
              value={feishuMaxBytes}
              onChange={(event) => setFeishuMaxBytes(event.target.value)}
            />
          </div>

          <label className="flex flex-col gap-2 text-sm font-medium text-foreground">
            租户股票池
            <textarea
              value={stockListText}
              onChange={(event) => setStockListText(event.target.value)}
              placeholder={'600519\nAAPL\nHK00700'}
              className="input-surface input-focus-glow min-h-[160px] w-full resize-y rounded-xl border bg-transparent px-3 py-2 text-sm text-foreground"
            />
          </label>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="settings-primary"
              onClick={() => void saveConfig()}
              disabled={!currentTenant || isSaving}
              isLoading={isSaving}
              loadingText="保存中..."
            >
              保存租户配置
            </Button>
          </div>
        </div>

        <div className="rounded-2xl border settings-border bg-background/40 p-4">
          <div className="mb-3 flex items-center gap-2">
            <Building2 className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">新建租户</h3>
          </div>
          <div className="space-y-3">
            <Input
              label="租户 Key"
              value={newTenantKey}
              onChange={(event) => setNewTenantKey(event.target.value)}
              placeholder="team_alpha"
            />
            <Input
              label="租户名称"
              value={newTenantName}
              onChange={(event) => setNewTenantName(event.target.value)}
            />
            <Input
              label="描述"
              value={newTenantDescription}
              onChange={(event) => setNewTenantDescription(event.target.value)}
            />
            <Button
              type="button"
              variant="settings-secondary"
              onClick={() => void createTenant()}
              disabled={isCreating}
              isLoading={isCreating}
              loadingText="创建中..."
            >
              <Plus className="h-4 w-4" />
              创建租户
            </Button>
          </div>
        </div>
      </div>

      {error ? <InlineAlert variant="danger" title="租户配置失败" message={error} /> : null}
      {feedback ? <InlineAlert variant="success" message={feedback} /> : null}
    </SettingsSectionCard>
  );
};
