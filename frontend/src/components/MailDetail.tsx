import { ArrowLeft, Mail as MailIcon } from 'lucide-react';
import { MailReplyForm } from '@/components/MailReplyForm';
import { MailTags } from '@/components/MailTags';
import { Button } from '@/components/ui/Button';
import { buildMailBodySrcDoc } from '@/features/mail/mailBody';
import { cn } from '@/lib/cn';
import { formatRelativeTime } from '@/lib/format';
import { useThemeValue } from '@/lib/theme';
import type { MailMessage } from '@/types/api';

/** Полная дата для шапки детали (08-design-system.md: в детали — полная дата). */
function absoluteDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return formatRelativeTime(iso);
  return new Date(ts).toLocaleString('ru-RU', { dateStyle: 'long', timeStyle: 'short' });
}

/**
 * Тело письма. `body_html` (недоверенный HTML третьих лиц) рендерится ТОЛЬКО в
 * sandbox-iframe без `allow-scripts`/`allow-same-origin` (ADR-012, modules/mail
 * «Изоляция HTML-тела»). Иначе — `body_text` моношрифтом с переносами. DOMPurify не нужен.
 * Фон/цвет тела следуют ТЕМЕ CRM (ADR-047 §6): srcDoc собирает ЕДИНЫЙ билдер
 * `buildMailBodySrcDoc(bodyHtml, theme)` (общий с Mini App `/tg/mail`), тема — реактивная
 * (`useThemeValue`), смена темы перерисовывает iframe.
 * Возвращает flex-1-контейнер: тело скроллится внутри себя, страница по вертикали не едет.
 */
function MailBody({ message }: { message: MailMessage }) {
  const theme = useThemeValue();

  if (!message.body_present) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-10 text-center">
        <p className="text-[13px] text-text-secondary">Тело письма недоступно</p>
      </div>
    );
  }

  const html = message.body_html;
  const hasHtml = Boolean(html && html.trim());

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 bg-surface-2 px-4 py-4">
      {hasHtml ? (
        <iframe
          title="Тело письма"
          srcDoc={buildMailBodySrcDoc(html ?? '', theme)}
          sandbox=""
          referrerPolicy="no-referrer"
          className="min-h-0 w-full flex-1 rounded-lg border border-border-subtle bg-surface-2"
        />
      ) : (
        <pre className="scrollbar-none min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border-subtle bg-surface-2 p-3 font-mono text-[13px] text-text-primary">
          {message.body_text}
        </pre>
      )}
      {message.body_truncated && (
        <p className="shrink-0 text-[12px] text-text-secondary">Письмо показано не полностью</p>
      )}
    </div>
  );
}

interface MailDetailProps {
  message: MailMessage;
  /** Кнопка «Назад» к списку — только на узких вьюпортах (передаётся из MailPage). */
  onBack: () => void;
  /**
   * Возврат письма в «непрочитано» (`DELETE …/read`, ADR-050 §2.7). Кнопка «Отметить
   * непрочитанным» рендерится ТОЛЬКО когда письмо уже прочитано (`is_unread === false`) и
   * обработчик передан (у супер-админа из `.env` личного состояния нет — MailPage его не
   * передаёт). Клик не закрывает деталь: письмо остаётся открытым, индикатор в ленте
   * загорается, авто-пометка повторно не срабатывает (её триггер — смена письма).
   */
  onMarkUnread?: (id: number) => void;
  /** Идёт запрос `DELETE …/read` (disabled-состояние кнопки). */
  markUnreadPending?: boolean;
}

/** Правая панель master-detail: шапка + тело + inline-reply (08-design-system.md). */
export function MailDetail({
  message,
  onBack,
  onMarkUnread,
  markUnreadPending = false,
}: MailDetailProps) {
  const { email, display_name: displayName } = message.mail_account;
  const subject = message.subject ?? '(без темы)';
  const canMarkUnread = Boolean(onMarkUnread) && !message.is_unread;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border-subtle px-4 py-4">
        {/* Строка действий шапки: «Назад» (только узкие вьюпорты) + «Отметить непрочитанным».
            Когда кнопки отката нет, на desktop строка пуста (Назад скрыт) и не занимает места. */}
        <div
          className={cn(
            'flex flex-wrap items-center justify-between gap-2 md:justify-end',
            canMarkUnread ? 'mb-3' : 'mb-3 md:mb-0',
          )}
        >
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1 rounded-md text-[13px] font-medium text-text-secondary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent md:hidden"
          >
            <ArrowLeft className="h-4 w-4" />
            Назад
          </button>

          {canMarkUnread && (
            <Button
              variant="ghost"
              size="sm"
              disabled={markUnreadPending}
              onClick={() => onMarkUnread?.(message.id)}
            >
              <MailIcon className="h-4 w-4" aria-hidden="true" />
              Отметить непрочитанным
            </Button>
          )}
        </div>

        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <span className="text-sm font-semibold text-text-primary">
            {message.from_name || message.from_addr}
          </span>
          <time dateTime={message.internal_date} className="text-[12px] text-text-tertiary">
            {absoluteDate(message.internal_date)}
          </time>
        </div>

        {message.from_name && (
          <p className="mt-0.5 break-all font-mono text-[12px] text-text-secondary">
            {message.from_addr}
          </p>
        )}

        <h2
          className={
            message.subject === null
              ? 'mt-2 text-base font-semibold text-text-secondary'
              : 'mt-2 text-base font-semibold text-text-primary'
          }
        >
          {subject}
        </h2>

        <div className="mt-2">
          <MailTags tags={message.tags} />
        </div>

        {/*
          «Получено на: {display_name} <{email}>» — оба значения видны полностью
          (08-design-system.md; правило CLAUDE.md — значимый контент не обрезать). Длинный
          адрес переносится (break-words), НЕ truncate. При пустом display_name — только email.
        */}
        <p className="mt-2 break-words text-[12px] text-text-secondary">
          Получено на: {displayName && <span className="text-text-primary">{displayName} </span>}
          <span className="font-mono text-text-secondary">
            {displayName ? `<${email}>` : email}
          </span>
        </p>
      </header>

      <MailBody message={message} />

      <MailReplyForm key={message.id} message={message} />
    </div>
  );
}
