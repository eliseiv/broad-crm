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
    <div className="flex min-w-0 flex-col rounded-sub border border-border-subtle bg-surface-2 px-1 py-2.5 shadow-sub xl:px-2.5">
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
        Значение детали — на ОТДЕЛЬНОЙ строке во всю внутреннюю ширину под-карточки.
        Формат («value/total ГБ», десятичные) — НОРМАТИВНЫЙ (08-design-system.md, «Под-карточки
        метрик») и НЕ меняется. Числовое значение обязано читаться ЦЕЛИКОМ на всех штатных
        вьюпортах: усечение (truncate / overflow-hidden / клиппинг) как способ «уместить»
        значимый контент — ЗАПРЕЩЕНО (CLAUDE.md: «переполнение решается размером, а не
        скрытием контента»). Прежнее контейнерное усечение на узких экранах (TD-023) снято.

        Переполнение решается РАЗМЕРОМ и РАСКЛАДКОЙ (grid-cols-3 сохранена):
          1) горизонтальный padding под-карточки на узких вьюпортах ужат (px-1.5, на xl —
             прежние px-2.5) → внутренняя ширина растёт с ~72px до ~84px;
          2) шаг сетки метрик на узких вьюпортах ужат (gap-2, на xl — прежний gap-3,
             см. ServerCard) → ещё ~+3px;
          3) `whitespace-nowrap` снят: если значение всё же длиннее строки (экстремально
             длинные тоталы), оно ПЕРЕНОСИТСЯ по пробелу перед единицей («1899.9/2048.5» +
             «ГБ»), а не обрезается; `break-words` — страховка для патологически длинного
             числового токена. Контент остаётся читаемым ПОЛНОСТЬЮ в любом случае.
        `overflow-hidden` с корня под-карточки СНЯТ (он и был механизмом усечения): Gauge —
        `w-full` и не переполняет контейнер, а `min-w-0` по-прежнему не даёт под-карточке
        расширять grid-трек и наезжать на соседнюю метрику.
      */}
      <div className="mt-1 border-t border-border-subtle pt-2.5">
        {detailText ? (
          <p className="w-full min-w-0 break-words text-center font-mono text-[10px] font-medium leading-tight text-text-primary">
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
