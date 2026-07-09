import { AlertTriangle, ChevronDown, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import type { BackendRef, BackendRefListResponse } from '@/types/api';

/** Мин. форма react-query результата, нужная секции (data/loading/error/refetch). */
interface BackendsQueryResult {
  data?: BackendRefListResponse;
  isLoading: boolean;
  isError: boolean;
  isFetching: boolean;
  refetch: () => void;
}

interface BackendsDetailSectionProps {
  /** Счётчик из list-схемы (`backend_count`) — показывается свёрнутым, без запроса. */
  count: number;
  /** aria-controls id (уникальность внутри модалки). */
  id: string;
  /** Раскрыта ли секция (состоянием владеет родитель — он же вызывает ленивый хук). */
  open: boolean;
  onToggle: () => void;
  /** Результат ленивого reverse-lookup-хука (`enabled=open`); запрос уходит при раскрытии. */
  query: BackendsQueryResult;
}

/** Строка бэка в раскрытой секции: Код / Название / Домен (`BackendRef`), только просмотр. */
function BackendRefRow({ backend }: { backend: BackendRef }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
      <span className="break-all font-mono text-[13px] text-text-primary">{backend.code}</span>
      <span className="break-words text-[13px] text-text-secondary">{backend.name}</span>
      <span className="break-all font-mono text-[12px] text-text-tertiary">{backend.domain}</span>
    </div>
  );
}

/**
 * Сворачиваемая секция «Бэки» detail-view сервера/ИИ-ключа (08-design-system.md, ADR-040):
 * свёрнута по умолчанию, заголовок-триггер показывает «Бэков: {N}» (`backend_count`). При
 * раскрытии родитель включает ленивый reverse-lookup → список Код/Название/Домен; состояния
 * loading / empty «Бэков нет» / error с «Повторить» — внутри секции. Строки — только просмотр.
 */
export function BackendsDetailSection({
  count,
  id,
  open,
  onToggle,
  query,
}: BackendsDetailSectionProps) {
  const backends = query.data?.backends ?? [];

  return (
    <div className="rounded-sub border border-border-subtle">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
      >
        <span className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
          Бэки
        </span>
        <span className="flex items-center gap-2">
          <span className="text-[13px] text-text-secondary">Бэков: {count}</span>
          <ChevronDown
            className={cn('h-4 w-4 text-text-tertiary transition-transform', open && 'rotate-180')}
            aria-hidden="true"
          />
        </span>
      </button>
      {open && (
        <div id={id} className="flex flex-col gap-2 border-t border-border-subtle px-3 py-3">
          {query.isLoading && (
            <div className="flex items-center gap-2 py-1 text-[13px] text-text-secondary">
              <Spinner className="text-text-secondary" />
              Загрузка…
            </div>
          )}

          {!query.isLoading && query.isError && (
            <div className="flex flex-wrap items-center gap-3 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
              <AlertTriangle className="h-5 w-5 text-status-red" aria-hidden="true" />
              <span className="text-[13px] text-text-secondary">Не удалось загрузить</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => query.refetch()}
                loading={query.isFetching}
              >
                <RefreshCw className="h-4 w-4" />
                Повторить
              </Button>
            </div>
          )}

          {!query.isLoading && !query.isError && backends.length === 0 && (
            <p className="rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5 text-[13px] text-text-secondary">
              Бэков нет
            </p>
          )}

          {!query.isLoading && !query.isError && backends.length > 0 && (
            <div className="flex flex-col gap-2">
              {backends.map((b) => (
                <BackendRefRow key={b.code} backend={b} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
