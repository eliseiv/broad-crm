import { useState } from 'react';
import type { FormEvent } from 'react';
import { ChevronDown, Send } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { cn } from '@/lib/cn';
import { ApiError } from '@/lib/api';
import { useReplyMail } from '@/features/mail/hooks';
import type { MailMessage, MailReplyRequest } from '@/types/api';

/** Разбор строки адресов формы («a@x, b@y») в массив для API (04-api.md: to/cc — string[]). */
function parseAddrs(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Inline-ответ на письмо (chat-like, 08-design-system.md «Inline-ответ»). Заменяет модалку
 * ReplyModal (ADR-013). Textarea (`body`, обязателен) + кнопка «Ответить» рядом; поля
 * `to`/`cc`/`subject` предзаполнены и свёрнуты за раскрытием «Расширенно».
 *
 * Компонент монтируется с `key={message.id}` в родителе — смена письма даёт чистый сброс
 * состояния без эффекта; после успешной отправки поле `body` очищается вручную.
 */
export function MailReplyForm({ message }: { message: MailMessage }) {
  const [body, setBody] = useState('');
  const [bodyError, setBodyError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [to, setTo] = useState(message.from_addr);
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState(message.subject ? `Re: ${message.subject}` : 'Re: ');
  const replyMutation = useReplyMail(message.id);
  const isSubmitting = replyMutation.isPending;

  const applyApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 404) {
        toast.error('Письмо не найдено');
        return;
      }
      if (err.status === 422 || err.status === 400) {
        setBodyError('Введите текст сообщения');
        return;
      }
      if (err.status === 502) {
        toast.error('Почтовый сервис временно недоступен');
        return;
      }
      toast.error(err.message);
      return;
    }
    toast.error('Не удалось отправить ответ');
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!body.trim()) {
      setBodyError('Введите текст сообщения');
      return;
    }
    setBodyError(null);

    const payload: MailReplyRequest = { body: body.trim() };
    const toList = parseAddrs(to);
    if (toList.length > 0) payload.to = toList;
    const ccList = parseAddrs(cc);
    if (ccList.length > 0) payload.cc = ccList;
    const trimmedSubject = subject.trim();
    if (trimmedSubject) payload.subject = trimmedSubject;

    replyMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Ответ отправлен');
        setBody('');
        setBodyError(null);
      },
      onError: applyApiError,
    });
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="shrink-0 border-t border-border-subtle bg-surface-1 px-4 py-3"
      noValidate
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="mb-2 inline-flex items-center gap-1 rounded-md text-[12px] font-medium text-text-secondary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        Расширенно
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', expanded && 'rotate-180')} />
      </button>

      {expanded && (
        <div className="mb-3 flex flex-col gap-3">
          <Input
            label="Кому"
            value={to}
            mono
            autoComplete="off"
            disabled={isSubmitting}
            placeholder="name@example.com"
            onChange={(e) => setTo(e.target.value)}
          />
          <Input
            label="Копия"
            value={cc}
            mono
            autoComplete="off"
            disabled={isSubmitting}
            placeholder="Необязательно"
            onChange={(e) => setCc(e.target.value)}
          />
          <Input
            label="Тема"
            value={subject}
            disabled={isSubmitting}
            onChange={(e) => setSubject(e.target.value)}
          />
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <Textarea
            aria-label="Сообщение"
            value={body}
            error={bodyError}
            rows={3}
            disabled={isSubmitting}
            placeholder="Напишите ответ…"
            onChange={(e) => {
              setBody(e.target.value);
              if (bodyError) setBodyError(null);
            }}
          />
        </div>
        <Button type="submit" loading={isSubmitting} disabled={!body.trim()} className="shrink-0">
          <Send className="h-4 w-4" />
          Ответить
        </Button>
      </div>
    </form>
  );
}
