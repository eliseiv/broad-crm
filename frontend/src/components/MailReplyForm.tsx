import { useState } from 'react';
import type { FormEvent } from 'react';
import { Send } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Textarea';
import { ApiError } from '@/lib/api';
import { useReplyMail } from '@/features/mail/hooks';
import type { MailMessage, MailReplyRequest } from '@/types/api';

/**
 * Inline-ответ на письмо (chat-like, 08-design-system.md «Inline-ответ»). Заменяет модалку
 * ReplyModal (ADR-013). Форма = ТОЛЬКО многострочный Textarea (`body`, обязателен) + кнопка
 * «Ответить» рядом. Блок «Расширенно» и поля `to`/`cc`/`subject` удалены (ADR-013 поправка
 * 2026-07-04): ответ шлётся телом `{body}`, дефолты (`to`=`from_addr`, `subject`=`Re: …`)
 * подставляет внешний сервис (поля опциональны в MailReplyRequest — 04-api.md не менялся).
 *
 * Компонент монтируется с `key={message.id}` в родителе — смена письма даёт чистый сброс
 * состояния без эффекта; после успешной отправки поле `body` очищается вручную.
 */
export function MailReplyForm({ message }: { message: MailMessage }) {
  const [body, setBody] = useState('');
  const [bodyError, setBodyError] = useState<string | null>(null);
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

    // Только body: to/cc/subject не передаём — дефолты подставляет внешний сервис.
    const payload: MailReplyRequest = { body: body.trim() };

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
      {/*
        Строка ответа (chat-like): Textarea занимает всю ширину (flex-1), кнопка «Ответить» —
        штатной высоты ДС, выровнена по центру высоты поля (items-center + self-center),
        НЕ растягивается на всю высоту Textarea (08-design-system.md «Выравнивание кнопки»).
      */}
      <div className="flex items-center gap-2">
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
        <Button
          type="submit"
          loading={isSubmitting}
          disabled={!body.trim()}
          className="shrink-0 self-center"
        >
          <Send className="h-4 w-4" />
          Ответить
        </Button>
      </div>
    </form>
  );
}
