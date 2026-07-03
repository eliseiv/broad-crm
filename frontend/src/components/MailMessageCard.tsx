import { useState } from 'react';
import { ChevronDown, Reply } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { ReplyModal } from '@/components/ReplyModal';
import { cn } from '@/lib/cn';
import { formatRelativeTime } from '@/lib/format';
import type { MailMessage } from '@/types/api';

/** Абсолютная дата для title/подсказки (полная дата в раскрытом виде, 08-design-system.md). */
function absoluteDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  return new Date(ts).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' });
}

/**
 * Тело письма. `body_html` (недоверенный HTML третьих лиц) рендерится ТОЛЬКО в
 * sandbox-iframe без `allow-scripts`/`allow-same-origin` (modules/mail «Изоляция
 * HTML-тела», ADR-012). Иначе — `body_text` моношрифтом с переносами. DOMPurify не нужен.
 */
function MailBody({ message }: { message: MailMessage }) {
  if (!message.body_present) {
    return <p className="text-[13px] text-text-secondary">Тело письма недоступно</p>;
  }

  const html = message.body_html;
  const hasHtml = Boolean(html && html.trim());

  return (
    <div className="flex flex-col gap-2">
      {hasHtml ? (
        <iframe
          title="Тело письма"
          srcDoc={html ?? ''}
          sandbox=""
          referrerPolicy="no-referrer"
          className="h-[420px] w-full rounded-lg border border-border-subtle bg-white"
        />
      ) : (
        <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border-subtle bg-surface-2 p-3 font-mono text-[13px] text-text-primary">
          {message.body_text}
        </pre>
      )}
      {message.body_truncated && (
        <p className="text-[12px] text-text-secondary">Письмо показано не полностью</p>
      )}
    </div>
  );
}

export function MailMessageCard({ message }: { message: MailMessage }) {
  const [expanded, setExpanded] = useState(false);
  const [replyOpen, setReplyOpen] = useState(false);

  const accountLabel = message.mail_account.display_name || message.mail_account.email;
  const subject = message.subject ?? '(без темы)';

  return (
    <div className="rounded-card border border-border-subtle bg-surface-1 shadow-card">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className={cn(
          'flex w-full flex-col gap-2 rounded-card px-4 py-3 text-left transition-colors',
          'hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <span className="text-sm font-semibold text-text-primary">
              {message.from_name || message.from_addr}
            </span>
            {message.from_name && (
              <span className="ml-2 break-all font-mono text-[12px] text-text-secondary">
                {message.from_addr}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <time
              dateTime={message.internal_date}
              title={absoluteDate(message.internal_date)}
              className="text-[12px] text-text-tertiary"
            >
              {formatRelativeTime(message.internal_date)}
            </time>
            <ChevronDown
              className={cn(
                'h-4 w-4 text-text-tertiary transition-transform',
                expanded && 'rotate-180',
              )}
              aria-hidden="true"
            />
          </div>
        </div>

        {/* Тема: усечение задизайнено ТОЛЬКО в свёрнутом виде; при раскрытии видна целиком. */}
        <p
          className={cn(
            'text-sm text-text-primary',
            !expanded && 'truncate',
            message.subject === null && 'text-text-secondary',
          )}
        >
          {subject}
        </p>

        {message.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.tags.map((tag) => (
              <span
                key={tag.id}
                className="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium"
                style={{
                  color: tag.color,
                  borderColor: `${tag.color}66`,
                  backgroundColor: `${tag.color}1f`,
                }}
              >
                {tag.name}
              </span>
            ))}
          </div>
        )}

        <p className="text-[12px] text-text-secondary">
          Получено на: <span className="text-text-primary">{accountLabel}</span>
        </p>

        {!expanded && (
          <p className="line-clamp-2 text-[13px] text-text-tertiary">{message.body_text}</p>
        )}
      </button>

      {expanded && (
        <div className="border-t border-border-subtle px-4 py-4">
          <MailBody message={message} />
          <div className="mt-4 flex justify-end">
            <Button size="sm" onClick={() => setReplyOpen(true)}>
              <Reply className="h-4 w-4" />
              Ответить
            </Button>
          </div>
        </div>
      )}

      <ReplyModal message={message} open={replyOpen} onOpenChange={setReplyOpen} />
    </div>
  );
}
