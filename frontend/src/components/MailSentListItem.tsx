import { cn } from '@/lib/cn';
import type { MailSentMessage } from '@/types/api';

function mailListDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function snippet(text: string, max = 80): string {
  const trimmed = text.replace(/\s+/g, ' ').trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max)}…`;
}

interface MailSentListItemProps {
  message: MailSentMessage;
  isActive: boolean;
  onSelect: (id: string) => void;
}

/** Компактная строка ленты отправленных (ADR-071). */
export function MailSentListItem({ message, isActive, onSelect }: MailSentListItemProps) {
  const subject = message.subject ?? '(без темы)';
  const preview = snippet(message.body_text);

  return (
    <button
      type="button"
      onClick={() => onSelect(message.id)}
      aria-current={isActive}
      className={cn(
        'flex w-full flex-col gap-1 border-l-2 px-4 py-2.5 text-left transition-colors',
        'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent',
        isActive ? 'border-l-accent bg-surface-2' : 'border-l-transparent hover:bg-surface-1',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-sm font-medium text-text-primary">
          {message.to_addrs}
        </span>
        <time
          dateTime={message.sent_at}
          className="shrink-0 text-[12px] text-text-tertiary"
        >
          {mailListDate(message.sent_at)}
        </time>
      </div>
      <p className="truncate text-[13px] text-text-primary">{subject}</p>
      {preview && (
        <p className="truncate text-[12px] text-text-secondary">{preview}</p>
      )}
    </button>
  );
}
