import { useState } from 'react';
import { AlertTriangle, Mail, Plus, RefreshCw, Tag } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { MailTagModal } from '@/components/MailTagModal';
import { MailTagRow } from '@/components/MailTagRow';
import { ApiError } from '@/lib/api';
import { useCan } from '@/features/auth/hooks';
import { useMailTags } from '@/features/mail/hooks';

/** Skeleton-строки таблицы тегов при начальной загрузке. */
function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-16 animate-pulse rounded-card border border-border-subtle bg-surface-1"
        />
      ))}
    </div>
  );
}

function CenteredState({
  icon,
  title,
  hint,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
      {icon}
      <div>
        <p className="text-base font-semibold text-text-primary">{title}</p>
        {hint && <p className="mt-1 text-[13px] text-text-secondary">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

/**
 * Вкладка «Теги» (08-design-system.md «Вкладка Теги», ADR-038): таблица глобального
 * каталога тегов. Колонки — ровно три: Имя тега / Правила / Действия (колонки «Тип» нет).
 * Управление — под правом `mail:tags`; просмотр — под `mail:view`.
 */
export function TagsTab() {
  const [addOpen, setAddOpen] = useState(false);
  const canManage = useCan('mail', 'tags');

  const query = useMailTags();
  const tags = query.data?.tags ?? [];
  const isNotConfigured = query.error instanceof ApiError && query.error.status === 503;

  return (
    <div className="flex flex-col gap-4">
      {canManage && (
        <div className="flex items-center justify-end">
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            Добавить тег
          </Button>
        </div>
      )}

      {query.isLoading && <TableSkeleton />}

      {!query.isLoading && isNotConfigured && (
        <CenteredState
          icon={<Mail className="h-10 w-10 text-text-tertiary" aria-hidden="true" />}
          title="Сервис почт не настроен"
          hint="Обратитесь к администратору для настройки почтового сервиса."
        />
      )}

      {!query.isLoading && query.isError && !isNotConfigured && (
        <CenteredState
          icon={<AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />}
          title="Почтовый сервис временно недоступен"
          action={
            <Button
              variant="outline"
              onClick={() => void query.refetch()}
              loading={query.isFetching}
            >
              <RefreshCw className="h-4 w-4" />
              Повторить
            </Button>
          }
        />
      )}

      {!query.isLoading && !query.isError && tags.length === 0 && (
        <CenteredState
          icon={<Tag className="h-10 w-10 text-text-tertiary" aria-hidden="true" />}
          title="Тегов пока нет"
          hint={
            canManage ? 'Создайте первый тег, чтобы автоматически размечать письма.' : undefined
          }
        />
      )}

      {!query.isLoading && !query.isError && tags.length > 0 && (
        <div className="scrollbar-none overflow-x-auto rounded-card border border-border-subtle bg-surface-1">
          <table className="w-full min-w-[760px] border-collapse text-left">
            <thead>
              <tr className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
                <th className="w-52 px-3 py-3 font-medium">Имя тега</th>
                <th className="px-3 py-3 font-medium">Правила</th>
                {/* relative: даёт абсолютному `sr-only` позиционированного предка ВНУТРИ
                    overflow-x-auto обёртки, иначе его containing block — ICB, и он выпадает
                    из клипа обёртки, растягивая scrollWidth документа на узких вьюпортах. */}
                <th className="relative px-3 py-3 font-medium">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {tags.map((tag) => (
                <MailTagRow key={tag.id} tag={tag} canManage={canManage} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <MailTagModal open={addOpen} onOpenChange={setAddOpen} mode="add" />
    </div>
  );
}
