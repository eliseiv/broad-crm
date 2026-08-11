import { Archive, MailOpen, RefreshCw, Trash2, Undo2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Combobox, type ComboboxOption } from '@/components/ui/Combobox';
import type { MailNavFolder } from '@/components/MailSidebar';

interface MailListToolbarProps {
  navFolder: MailNavFolder;
  selectedCount: number;
  onMarkRead: () => void;
  onArchive: () => void;
  onDelete: () => void;
  onRestore: () => void;
  onRefresh: () => void;
  isRefreshing?: boolean;
  markReadPending?: boolean;
  archivePending?: boolean;
  deletePending?: boolean;
  restorePending?: boolean;
  showMailboxFilter?: boolean;
  mailboxOptions?: ComboboxOption[];
  mailboxValue?: string;
  mailboxQuery?: string;
  onMailboxChange?: (value: string | null) => void;
  onMailboxQueryChange?: (query: string) => void;
  mailboxesLoading?: boolean;
}

/**
 * Тулбар списка писем: фильтр по ящику (ADR-052 §2) и bulk-действия (ADR-074).
 */
export function MailListToolbar({
  navFolder,
  selectedCount,
  onMarkRead,
  onArchive,
  onDelete,
  onRestore,
  onRefresh,
  isRefreshing,
  markReadPending,
  archivePending,
  deletePending,
  restorePending,
  showMailboxFilter = false,
  mailboxOptions = [],
  mailboxValue = '',
  mailboxQuery = 'Все почты',
  onMailboxChange,
  onMailboxQueryChange,
  mailboxesLoading = false,
}: MailListToolbarProps) {
  const disabled = selectedCount === 0;
  const isSent = navFolder === 'sent';
  const isDeleted = navFolder === 'deleted';

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-border-subtle px-2 py-2">
      {showMailboxFilter && (
        <div className="min-w-[12rem] w-56 max-w-full shrink-0">
          <Combobox
            aria-label="Почта"
            mode="select"
            options={mailboxOptions}
            value={mailboxValue}
            onChange={onMailboxChange ?? (() => undefined)}
            query={mailboxQuery}
            onQueryChange={onMailboxQueryChange ?? (() => undefined)}
            disabled={mailboxesLoading}
          />
        </div>
      )}
      {!isSent && (
        <>
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            loading={markReadPending}
            onClick={onMarkRead}
          >
            <MailOpen className="h-4 w-4" aria-hidden="true" />
            Прочитано
          </Button>
          {isDeleted ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              loading={restorePending}
              onClick={onRestore}
            >
              <Undo2 className="h-4 w-4" aria-hidden="true" />
              Восстановить
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              loading={archivePending}
              onClick={onArchive}
            >
              <Archive className="h-4 w-4" aria-hidden="true" />
              Архивировать
            </Button>
          )}
          {!isDeleted && (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              loading={deletePending}
              onClick={onDelete}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Удалить
            </Button>
          )}
        </>
      )}
      <Button variant="ghost" size="sm" loading={isRefreshing} onClick={onRefresh}>
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
        Обновить
      </Button>
      {selectedCount > 0 && (
        <span className="ml-1 text-[12px] text-text-secondary">Выбрано: {selectedCount}</span>
      )}
    </div>
  );
}
