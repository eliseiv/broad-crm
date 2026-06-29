import { Activity, Cpu, HardDrive, MemoryStick, MoreHorizontal } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Gauge } from '@/components/Gauge';
import { formatCores, formatNumber } from '@/lib/format';
import type { Metric } from '@/types/api';

export type MetricKind = 'cpu' | 'ram' | 'ssd';

interface MetricConfig {
  label: 'CPU' | 'RAM' | 'SSD';
  HeaderIcon: LucideIcon;
  FooterIcon: LucideIcon;
  /** Брендовый тон чипа иконки (как в референсе) — НЕ влияет на цвет дуги. */
  chip: string;
}

const CONFIG: Record<MetricKind, MetricConfig> = {
  cpu: { label: 'CPU', HeaderIcon: Cpu, FooterIcon: Activity, chip: 'text-sky-400 bg-sky-500/10' },
  ram: {
    label: 'RAM',
    HeaderIcon: MemoryStick,
    FooterIcon: MemoryStick,
    chip: 'text-emerald-400 bg-emerald-500/10',
  },
  ssd: {
    label: 'SSD',
    HeaderIcon: HardDrive,
    FooterIcon: HardDrive,
    chip: 'text-violet-400 bg-violet-500/10',
  },
};

/**
 * Абсолютные значения (локализовано, 08-design-system.md):
 * CPU (`unit:"cores"`) → «N ядер» (по total); RAM/SSD (`unit:"GB"`) → «value / total ГБ».
 * Нулевые detail обрабатываются безопасно (строка скрывается / только total).
 */
function renderDetail(metric: Metric | null): string | null {
  if (!metric) return null;
  const { value, total, unit } = metric.detail;
  if (unit === 'cores') {
    return total != null ? formatCores(total) : null;
  }
  const unitRu = unit === 'GB' ? 'ГБ' : unit;
  if (value != null && total != null)
    return `${formatNumber(value)} / ${formatNumber(total)} ${unitRu}`;
  if (value == null && total != null) return `${formatNumber(total)} ${unitRu}`;
  if (value != null && total == null) return `${formatNumber(value)} ${unitRu}`;
  return null;
}

interface MetricSubCardProps {
  kind: MetricKind;
  metric: Metric | null;
}

export function MetricSubCard({ kind, metric }: MetricSubCardProps) {
  const { label, HeaderIcon, FooterIcon, chip } = CONFIG[kind];
  const detailText = renderDetail(metric);

  return (
    <div className="flex flex-col rounded-sub border border-border-subtle bg-surface-2 p-2.5 shadow-sub">
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-1.5">
          <span
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-chip ${chip}`}
          >
            <HeaderIcon className="h-[15px] w-[15px]" aria-hidden="true" />
          </span>
          <span className="text-sm font-semibold text-text-primary">{label}</span>
        </div>
        <MoreHorizontal className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
      </div>

      <div className="flex justify-center">
        <Gauge value={metric?.usage_percent ?? null} label={label} />
      </div>

      <div className="mt-1 flex items-center gap-1.5 border-t border-border-subtle pt-2.5">
        <FooterIcon className={`h-3.5 w-3.5 shrink-0 ${chip.split(' ')[0]}`} aria-hidden="true" />
        {detailText ? (
          <span className="font-mono text-[12px] font-medium leading-tight text-text-primary">
            {detailText}
          </span>
        ) : (
          <span className="font-mono text-[12px] text-text-tertiary">—</span>
        )}
      </div>
    </div>
  );
}
