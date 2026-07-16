import type React from 'react';
import { Building2 } from 'lucide-react';
import { useTenant } from '../../contexts/TenantContext';
import { cn } from '../../utils/cn';

type TenantSelectorProps = {
  collapsed?: boolean;
};

export const TenantSelector: React.FC<TenantSelectorProps> = ({ collapsed = false }) => {
  const {
    tenants,
    currentTenant,
    isLoading,
    switchLocked,
    setCurrentTenantKey,
  } = useTenant();

  if (collapsed) {
    return (
      <div
        className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-2xl border border-border/70 bg-card/70 text-secondary-text"
        title={currentTenant?.name || '租户'}
        aria-label={currentTenant?.name || '租户'}
      >
        <Building2 className="h-5 w-5" />
      </div>
    );
  }

  return (
    <div className="mb-3 px-1">
      <label className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-muted-text">
        <Building2 className="h-3.5 w-3.5" />
        <span>租户</span>
      </label>
      <select
        value={currentTenant?.key || ''}
        disabled={isLoading || switchLocked}
        onChange={(event) => setCurrentTenantKey(event.target.value)}
        className={cn(
          'h-9 w-full rounded-xl border border-border/70 bg-background/70 px-2 text-xs text-foreground outline-none transition-colors',
          'focus:border-primary/60 focus:ring-2 focus:ring-primary/15',
          isLoading || switchLocked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:border-primary/40',
        )}
        aria-label="当前租户"
      >
        {tenants.map((tenant) => (
          <option key={tenant.key} value={tenant.key} className="bg-elevated text-foreground">
            {tenant.name}
          </option>
        ))}
      </select>
    </div>
  );
};
