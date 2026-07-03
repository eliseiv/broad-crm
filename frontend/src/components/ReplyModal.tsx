import { useState } from 'react';
import type { FormEvent } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { ApiError } from '@/lib/api';
import { useReplyMail } from '@/features/mail/hooks';
import type { MailMessage, MailReplyRequest } from '@/types/api';

interface ReplyModalProps {
  message: MailMessage;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Разбор строки адресов формы («a@x, b@y») в массив для API (04-api.md: to/cc — string[]). */
function parseAddrs(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Форма ответа на письмо (08-design-system.md «Форма ответа»). Ремоунт по ключу
 * message.id + open даёт чистый сброс полей без эффекта (паттерн AddAiKeyModal).
 */
export function ReplyModal({ message, open, onOpenChange }: ReplyModalProps) {
  const key = `${message.id}-${open ? 'open' : 'closed'}`;
  return <ReplyDialog key={key} message={message} open={open} onOpenChange={onOpenChange} />;
}

function ReplyDialog({ message, open, onOpenChange }: ReplyModalProps) {
  const [to, setTo] = useState(message.from_addr);
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState(message.subject ? `Re: ${message.subject}` : 'Re: ');
  const [body, setBody] = useState('');
  const [bodyError, setBodyError] = useState<string | null>(null);
  const replyMutation = useReplyMail(message.id);

  const applyApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 404) {
        toast.error('Письмо не найдено');
        return;
      }
      if (err.status === 422 || err.status === 400) {
        setBodyError('Введите текст сообщения');
        toast.error('Проверьте текст сообщения');
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
        onOpenChange(false);
      },
      onError: applyApiError,
    });
  };

  const isSubmitting = replyMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title="Ответить на письмо"
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="reply-form" loading={isSubmitting}>
            Отправить
          </Button>
        </>
      }
    >
      <form id="reply-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <Input
          label="Кому"
          value={to}
          mono
          placeholder="name@example.com"
          autoComplete="off"
          onChange={(e) => setTo(e.target.value)}
        />
        <Input
          label="Копия"
          value={cc}
          mono
          placeholder="Необязательно"
          autoComplete="off"
          onChange={(e) => setCc(e.target.value)}
        />
        <Input label="Тема" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <Textarea
          label="Сообщение"
          value={body}
          error={bodyError}
          rows={7}
          autoFocus
          placeholder="Текст ответа…"
          onChange={(e) => {
            setBody(e.target.value);
            if (bodyError) setBodyError(null);
          }}
        />
      </form>
    </Modal>
  );
}
