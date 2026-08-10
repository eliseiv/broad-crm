import { ArrowLeft } from 'lucide-react';
import { cn } from '@/lib/cn';
import type { MailSentMessage } from '@/types/api';

function absoluteDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  return new Date(ts).toLocaleString('ru-RU', { dateStyle: 'long', timeStyle: 'short' });
}

interface MailSentDetailProps {
  message: MailSentMessage;
  onBack?: () => void;
}

/**
 * Деталь отправленного письма (ответ из CRM, ADR-071).
 */
export function MailSentDetail({ message, onBack }: MailSentDetailProps) {
  const accountLabel =
    message.mail_account.display_name || message.mail_account.email;
  const subject = message.subject ?? '(без темы)';

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border-subtle px-4 py-3">
        <div className="mb-2 flex items-center gap-2">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="inline-flex shrink-0 items-center gap-1 rounded-md text-[13px] font-medium text-text-secondary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Назад
            </button>
          )}
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-text-primary">{subject}</h2>
          <p className="mt-1 text-[13px] text-text-secondary">
            Кому: <span className="text-text-primary">{message.to_addrs}</span>
          </p>
          {message.cc_addrs && (
            <p className="text-[13px] text-text-secondary">
              Копия: <span className="text-text-primary">{message.cc_addrs}</span>
            </p>
          )}
          <p className="mt-1 text-[12px] text-text-tertiary">
            Отправлено: {absoluteDate(message.sent_at)} · с {accountLabel}
          </p>
        </div>
      </header>
      <pre
        className={cn(
          'scrollbar-none min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words',
          'bg-surface-2 px-4 py-4 font-mono text-[13px] text-text-primary',
        )}
      >
        {message.body_text}
      </pre>
    </div>
  );
}
