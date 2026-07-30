import { Eye, EyeOff } from 'lucide-react';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';

interface BalanceFormFieldsProps {
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  balanceUsd: string;
  onBalanceUsdChange: (value: string) => void;
  thresholdUsd: string;
  onThresholdUsdChange: (value: string) => void;
  billingAdminKey: string;
  onBillingAdminKeyChange: (value: string) => void;
  showBillingAdminKey: boolean;
  onToggleShowBillingAdminKey: () => void;
  errors: {
    balance_initial_usd?: string;
    balance_low_threshold_usd?: string;
    billing_admin_key?: string;
  };
  /** Edit: Admin key опционален (пустое = не менять). */
  billingAdminOptional?: boolean;
  disabled?: boolean;
}

const DEFAULT_THRESHOLD = '10';

/**
 * Блок формы мониторинга оценочного остатка (ADR-070).
 */
export function BalanceFormFields({
  enabled,
  onEnabledChange,
  balanceUsd,
  onBalanceUsdChange,
  thresholdUsd,
  onThresholdUsdChange,
  billingAdminKey,
  onBillingAdminKeyChange,
  showBillingAdminKey,
  onToggleShowBillingAdminKey,
  errors,
  billingAdminOptional = false,
  disabled = false,
}: BalanceFormFieldsProps) {
  return (
    <div className="flex flex-col gap-3 rounded-sub border border-border-subtle bg-surface-2 p-3">
      <Checkbox
        label="Мониторинг оценочного остатка"
        checked={enabled}
        disabled={disabled}
        onChange={(e) => onEnabledChange(e.target.checked)}
      />
      <p className="text-[12px] leading-snug text-text-tertiary">
        Укажите текущий баланс из личного кабинета провайдера. Остаток = баланс − расход по
        Admin Cost API. После пополнения обновите баланс вручную.
      </p>
      {enabled && (
        <>
          <Input
            label="Текущий баланс, $"
            type="number"
            min={0}
            step="0.01"
            value={balanceUsd}
            error={errors.balance_initial_usd}
            disabled={disabled}
            onChange={(e) => onBalanceUsdChange(e.target.value)}
          />
          <Input
            label="Порог уведомления, $"
            type="number"
            min={0}
            step="0.01"
            value={thresholdUsd || DEFAULT_THRESHOLD}
            error={errors.balance_low_threshold_usd}
            disabled={disabled}
            hint="Алерт при остатке ниже этой суммы"
            onChange={(e) => onThresholdUsdChange(e.target.value)}
          />
          <Input
            label="Admin API key"
            type={showBillingAdminKey ? 'text' : 'password'}
            placeholder={
              billingAdminOptional ? 'Оставьте пустым, чтобы не менять' : 'sk-… admin key'
            }
            mono
            value={billingAdminKey}
            error={errors.billing_admin_key}
            disabled={disabled}
            autoComplete="off"
            hint={
              billingAdminOptional
                ? 'Оставьте пустым, чтобы не менять Admin key'
                : 'Admin key из Organization → Admin keys (не inference sk-…)'
            }
            onChange={(e) => onBillingAdminKeyChange(e.target.value)}
            trailing={
              <button
                type="button"
                onClick={onToggleShowBillingAdminKey}
                aria-label={showBillingAdminKey ? 'Скрыть Admin key' : 'Показать Admin key'}
                className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {showBillingAdminKey ? (
                  <EyeOff className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Eye className="h-4 w-4" aria-hidden="true" />
                )}
              </button>
            }
          />
        </>
      )}
    </div>
  );
}
