import { MailTagChip } from '@/components/MailTagChip';
import { cn } from '@/lib/cn';
import type { MailMessage } from '@/types/api';

function mailListDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function snippet(text: string | null, max = 80): string {
  if (!text) return '';
  const trimmed = text.replace(/\s+/g, ' ').trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max)}…`;
}

interface MailListItemProps {
  message: MailMessage;
  isActive: boolean;
  onSelect: (id: number) => void;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
  showCheckbox?: boolean;
}

/**
 * Компактная строка ленты писем (ADR-071, 08-design-system.md).
 */
export function MailListItem({
  message,
  isActive,
  onSelect,
  selected = false,
  onToggleSelect,
  showCheckbox = false,
}: MailListItemProps) {
  const subject = message.subject ?? '(без темы)';
  const unread = message.is_unread;
  const firstTag = message.tags[0];
  const preview = snippet(message.body_text);
  const sender = message.from_name || message.from_addr;

  return (
    <div
      className={cn(
        'flex items-stretch border-l-2 transition-colors',
        isActive ? 'border-l-accent bg-surface-2' : 'border-l-transparent hover:bg-surface-1',
      )}
    >
      {showCheckbox && onToggleSelect && (
        <div className="flex shrink-0 items-center px-2">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(message.id)}
            aria-label={`Выбрать письмо ${message.id}`}
            className="h-4 w-4 rounded border-border-subtle accent-accent"
          />
        </div>
      )}
      <button
        type="button"
        onClick={() => onSelect(message.id)}
        aria-current={isActive}
        className={cn(
          'flex min-w-0 flex-1 flex-col gap-1 px-3 py-2.5 text-left',
          'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent',
        )}
      >
        <div className="flex items-center gap-2">
          <span className="shrink-0 font-mono text-[11px] text-text-tertiary">#{message.id}</span>
          {unread && (
            <>
              <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-accent" />
              <span className="sr-only">Непрочитано</span>
            </>
          )}
          <span
            className={cn(
              'min-w-0 truncate text-sm text-text-primary',
              unread && 'font-semibold',
            )}
          >
            {sender}
          </span>
          {firstTag && (
            <MailTagChip name={firstTag.name} color={firstTag.color} className="shrink-0" />
          )}
          <time
            dateTime={message.internal_date}
            className="ml-auto shrink-0 text-[12px] text-text-tertiary"
          >
            {mailListDate(message.internal_date)}
          </time>
        </div>
        <p
          className={cn(
            'truncate text-[13px]',
            message.subject === null ? 'text-text-secondary' : 'text-text-primary',
            unread && 'font-semibold',
          )}
        >
          {subject}
          {preview && (
            <span className="font-normal text-text-secondary"> — {preview}</span>
          )}
        </p>
      </button>
    </div>
  );
}
