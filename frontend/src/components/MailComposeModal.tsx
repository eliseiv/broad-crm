import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { Send } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { ApiError } from '@/lib/api';
import { MAIL_UNAVAILABLE_MESSAGE, mailErrorMessage } from '@/features/mail/errorMessages';
import { useComposeMail, useMailMailboxes } from '@/features/mail/hooks';
import type { MailComposeRequest } from '@/types/api';

interface MailComposeModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type FieldErrors = {
  mailbox?: string;
  to?: string;
  body?: string;
};

function parseAddressList(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function mailboxLabel(email: string, displayName: string | null): string {
  return displayName?.trim() ? `${displayName} (${email})` : email;
}

/**
 * Модалка «Написать» — выбор ящика, адресат, тема и текст; отправка через compose API.
 */
export function MailComposeModal({ open, onOpenChange }: MailComposeModalProps) {
  const { data: mailboxesData, isLoading: mailboxesLoading } = useMailMailboxes();
  const composeMutation = useComposeMail();

  const activeMailboxes = useMemo(
    () => (mailboxesData?.mailboxes ?? []).filter((m) => m.is_active),
    [mailboxesData],
  );

  const mailboxOptions: SelectOption[] = useMemo(
    () =>
      activeMailboxes.map((m) => ({
        value: String(m.id),
        label: mailboxLabel(m.email, m.display_name),
      })),
    [activeMailboxes],
  );

  const mailboxSelectOptions: SelectOption[] = useMemo(() => {
    if (mailboxOptions.length <= 1) return mailboxOptions;
    return [{ value: '', label: 'Выберите ящик' }, ...mailboxOptions];
  }, [mailboxOptions]);

  const [mailboxId, setMailboxId] = useState('');
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [errors, setErrors] = useState<FieldErrors>({});

  useEffect(() => {
    if (!open) return;
    setErrors({});
    if (mailboxId === '' && activeMailboxes.length === 1) {
      setMailboxId(String(activeMailboxes[0].id));
    }
  }, [open, activeMailboxes, mailboxId]);

  const isSubmitting = composeMutation.isPending;

  const resetForm = () => {
    setMailboxId(activeMailboxes.length === 1 ? String(activeMailboxes[0].id) : '');
    setTo('');
    setSubject('');
    setBody('');
    setErrors({});
  };

  const handleOpenChange = (next: boolean) => {
    if (!next && isSubmitting) return;
    if (!next) resetForm();
    onOpenChange(next);
  };

  const applyApiError = (err: unknown) => {
    const known = mailErrorMessage(err, 'compose');
    if (known !== null) {
      toast.error(known);
      return;
    }
    if (err instanceof ApiError) {
      if (err.status === 404) {
        toast.error('Почтовый ящик не найден');
        return;
      }
      if (err.status === 422 || err.status === 400) {
        setErrors((prev) => ({
          ...prev,
          to: 'Проверьте адреса получателей',
          body: prev.body ?? 'Проверьте текст письма',
        }));
        return;
      }
      if (err.status === 502 && err.code === 'mail_unavailable') {
        toast.error(MAIL_UNAVAILABLE_MESSAGE);
        return;
      }
      toast.error(err.message);
      return;
    }
    toast.error('Не удалось отправить письмо');
  };

  const validate = (): boolean => {
    const next: FieldErrors = {};
    if (!mailboxId) next.mailbox = 'Выберите почтовый ящик';
    const toList = parseAddressList(to);
    if (toList.length === 0) next.to = 'Укажите адрес получателя';
    if (!body.trim()) next.body = 'Введите текст письма';
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!validate()) return;

    const payload: MailComposeRequest = {
      to: parseAddressList(to),
      subject: subject.trim() || undefined,
      body: body.trim(),
    };

    composeMutation.mutate(
      { mailboxId: Number(mailboxId), payload },
      {
        onSuccess: () => {
          toast.success('Письмо отправлено');
          resetForm();
          onOpenChange(false);
        },
        onError: applyApiError,
      },
    );
  };

  return (
    <Modal
      open={open}
      onOpenChange={handleOpenChange}
      title="Новое письмо"
      description="Выберите ящик, укажите получателя и текст сообщения."
      size="lg"
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" disabled={isSubmitting} onClick={() => handleOpenChange(false)}>
            Отмена
          </Button>
          <Button
            type="submit"
            form="mail-compose-form"
            loading={isSubmitting}
            disabled={mailboxSelectOptions.length === 0}
          >
            <Send className="h-4 w-4" />
            Отправить
          </Button>
        </>
      }
    >
      <form
        id="mail-compose-form"
        onSubmit={handleSubmit}
        className="flex flex-col gap-4"
        noValidate
      >
        <Select
          label="От кого"
          options={mailboxSelectOptions}
          value={mailboxId}
          disabled={isSubmitting || mailboxesLoading || mailboxSelectOptions.length === 0}
          error={errors.mailbox}
          onChange={(e) => {
            setMailboxId(e.target.value);
            if (errors.mailbox) setErrors((prev) => ({ ...prev, mailbox: undefined }));
          }}
        />
        {mailboxSelectOptions.length === 0 && !mailboxesLoading && (
          <p className="text-[13px] text-text-secondary">
            Нет доступных активных ящиков для отправки.
          </p>
        )}
        <Input
          label="Кому"
          placeholder="email@example.com"
          hint="Несколько адресов — через запятую"
          value={to}
          error={errors.to}
          disabled={isSubmitting}
          onChange={(e) => {
            setTo(e.target.value);
            if (errors.to) setErrors((prev) => ({ ...prev, to: undefined }));
          }}
        />
        <Input
          label="Тема"
          placeholder="Тема письма"
          value={subject}
          disabled={isSubmitting}
          onChange={(e) => setSubject(e.target.value)}
        />
        <Textarea
          label="Сообщение"
          rows={8}
          value={body}
          error={errors.body}
          disabled={isSubmitting}
          placeholder="Текст письма…"
          onChange={(e) => {
            setBody(e.target.value);
            if (errors.body) setErrors((prev) => ({ ...prev, body: undefined }));
          }}
        />
      </form>
    </Modal>
  );
}
