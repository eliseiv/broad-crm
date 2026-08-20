import { cn } from '@/lib/cn';
import { formatRelativeTime, formatUsd } from '@/lib/format';
import type { AiKey, BalanceAlertLevel } from '@/types/api';

interface AiKeyBalanceDisplayProps {
  aiKey: AiKey;
  className?: string;
}

function levelTone(level: BalanceAlertLevel | null): 'green' | 'yellow' | 'red' | 'neutral' {
  if (level === 'depleted') return 'red';
  if (level === 'low') return 'yellow';
  if (level === 'normal') return 'green';
  return 'neutral';
}

/**
 * Оценочный остаток на ключе (ADR-070) — отдельно от health-бейджа.
 */
export function AiKeyBalanceDisplay({ aiKey, className }: AiKeyBalanceDisplayProps) {
  if (!aiKey.balance_monitoring_enabled) return null;

  const initial = aiKey.balance_initial_usd != null ? Number(aiKey.balance_initial_usd) : null;
  const remaining =
    aiKey.balance_remaining_usd != null ? Number(aiKey.balance_remaining_usd) : null;
  const threshold =
    aiKey.balance_low_threshold_usd != null ? Number(aiKey.balance_low_threshold_usd) : 10;
  const tone = levelTone(aiKey.balance_alert_level);
  const pct =
    initial != null && initial > 0 && remaining != null
      ? Math.min(100, Math.max(0, (remaining / initial) * 100))
      : null;

  return (
    <div
      className={cn('rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5', className)}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[13px] font-medium text-text-secondary">Оценочный остаток</span>
        <span
          className={cn(
            'text-sm font-semibold tabular-nums',
            tone === 'red' && 'text-status-red',
            tone === 'yellow' && 'text-status-yellow',
            tone === 'green' && 'text-status-green',
            tone === 'neutral' && 'text-text-primary',
          )}
        >
          {formatUsd(aiKey.balance_remaining_usd)}
        </span>
      </div>
      {pct != null && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
          <div
            className={cn(
              'h-full rounded-full transition-all',
              tone === 'red' && 'bg-status-red',
              tone === 'yellow' && 'bg-status-yellow',
              tone === 'green' && 'bg-status-green',
              tone === 'neutral' && 'bg-accent',
            )}
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Доля оставшегося баланса"
          />
        </div>
      )}
      <p className="mt-2 text-[12px] leading-snug text-text-tertiary">
        Из {formatUsd(aiKey.balance_initial_usd)} · порог {formatUsd(threshold)}
        {aiKey.balance_last_sync_at
          ? ` · синхр. ${formatRelativeTime(aiKey.balance_last_sync_at)}`
          : ' · ожидание синхронизации'}
      </p>
      {aiKey.balance_sync_status === 'error' && aiKey.balance_sync_error && (
        <p className="mt-1 text-[12px] text-status-red">{aiKey.balance_sync_error}</p>
      )}
    </div>
  );
}
