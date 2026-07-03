import { MailTags } from '@/components/MailTags';
import { cn } from '@/lib/cn';
import { formatRelativeTime } from '@/lib/format';
import type { MailMessage } from '@/types/api';

/** Полная дата для title/подсказки на элементе списка (08-design-system.md). */
function absoluteDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  return new Date(ts).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' });
}

interface MailListItemProps {
  message: MailMessage;
  isActive: boolean;
  onSelect: (id: number) => void;
}

/**
 * Строка ленты писем (левая панель master-detail, 08-design-system.md «Список писем»).
 * Тема усечена (`truncate`) — усечение задизайнено; в детали видна целиком. Значимые
 * значения (адрес, аккаунт) не обрезаются.
 */
export function MailListItem({ message, isActive, onSelect }: MailListItemProps) {
  const accountLabel = message.mail_account.display_name || message.mail_account.email;
  const subject = message.subject ?? '(без темы)';

  return (
    <button
      type="button"
      onClick={() => onSelect(message.id)}
      aria-current={isActive}
      className={cn(
        'flex w-full flex-col gap-1.5 border-l-2 px-4 py-3 text-left transition-colors',
        'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent',
        isActive ? 'border-l-accent bg-surface-2' : 'border-l-transparent hover:bg-surface-1',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-text-primary">
          {message.from_name || message.from_addr}
        </span>
        <time
          dateTime={message.internal_date}
          title={absoluteDate(message.internal_date)}
          className="shrink-0 text-[12px] text-text-tertiary"
        >
          {formatRelativeTime(message.internal_date)}
        </time>
      </div>

      {message.from_name && (
        <span className="truncate font-mono text-[12px] text-text-secondary">
          {message.from_addr}
        </span>
      )}

      <p
        className={cn(
          'truncate text-[13px]',
          message.subject === null ? 'text-text-secondary' : 'text-text-primary',
        )}
      >
        {subject}
      </p>

      <MailTags tags={message.tags} max={3} />

      <p className="truncate text-[12px] text-text-secondary">
        Получено на: <span className="text-text-primary">{accountLabel}</span>
      </p>
    </button>
  );
}
