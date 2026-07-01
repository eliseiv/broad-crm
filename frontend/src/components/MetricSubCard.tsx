import { Cpu, HardDrive, MemoryStick, MoreHorizontal } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Gauge } from '@/components/Gauge';
import { formatCores, formatNumber } from '@/lib/format';
import type { Metric } from '@/types/api';

export type MetricKind = 'cpu' | 'ram' | 'ssd';

interface MetricConfig {
  label: 'CPU' | 'RAM' | 'SSD';
  HeaderIcon: LucideIcon;
  /** Брендовый тон чипа иконки (как в референсе) — НЕ влияет на цвет дуги. */
  chip: string;
}

const CONFIG: Record<MetricKind, MetricConfig> = {
  cpu: { label: 'CPU', HeaderIcon: Cpu, chip: 'text-sky-400 bg-sky-500/10' },
  ram: { label: 'RAM', HeaderIcon: MemoryStick, chip: 'text-emerald-400 bg-emerald-500/10' },
  ssd: { label: 'SSD', HeaderIcon: HardDrive, chip: 'text-violet-400 bg-violet-500/10' },
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
    return `${formatNumber(value)}/${formatNumber(total)} ${unitRu}`;
  if (value == null && total != null) return `${formatNumber(total)} ${unitRu}`;
  if (value != null && total == null) return `${formatNumber(value)} ${unitRu}`;
  return null;
}

interface MetricSubCardProps {
  kind: MetricKind;
  metric: Metric | null;
}

export function MetricSubCard({ kind, metric }: MetricSubCardProps) {
  const { label, HeaderIcon, chip } = CONFIG[kind];
  const detailText = renderDetail(metric);

  return (
    <div className="flex min-w-0 flex-col overflow-hidden rounded-sub border border-border-subtle bg-surface-2 p-2.5 shadow-sub">
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

      {/*
        Значение детали — на ОТДЕЛЬНОЙ строке во всю внутреннюю ширину под-карточки,
        text-[10px] leading-tight whitespace-nowrap (одна строка) + min-w-0 (не даёт
        под-карточке расширять grid-трек, защита от наезда на соседа).
        Иконка убрана из строки значения, чтобы освободить место под текст.
        Формат «value/total unit» без пробелов вокруг «/»: «728.6/913.8 ГБ» = 14 моно-глифов
        × ~0.6em ≈ 84px нужной ширины (needed@10px ≈ 84px).
        overflow-hidden стоит на КОРНЕ под-карточки (L51) как КОНТЕЙНЕР:
          • десктоп xl (3-кол ≈ 90px, max ≈ 104px ≥ needed ~84px) — значение помещается
            ЦЕЛИКОМ и НЕ режется;
          • планшет-портрет (md ≤ ~823px, 2-кол ≈ 75px < 84px) — значение контейнерно
            усекается в границах под-карточки, без наезда на соседнюю метрику.
        Усечение на md — принятое ограничение TD-023.
      */}
      <div className="mt-1 border-t border-border-subtle pt-2.5">
        {detailText ? (
          <p className="w-full min-w-0 whitespace-nowrap text-center font-mono text-[10px] font-medium leading-tight text-text-primary">
            {detailText}
          </p>
        ) : (
          <p className="w-full text-center font-mono text-[10px] leading-tight text-text-tertiary">
            —
          </p>
        )}
      </div>
    </div>
  );
}
