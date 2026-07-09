import { useState } from 'react';
import { PlugZap } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useCan } from '@/features/auth/hooks';
import {
  useCreateMailbox,
  useMailTeams,
  useTestMailbox,
  useUpdateMailbox,
} from '@/features/mail/hooks';
import type {
  MailMailbox,
  MailMailboxCreateRequest,
  MailMailboxTestRequest,
  MailMailboxUpdateRequest,
  MailTeam,
} from '@/types/api';

/** Значение опции «Без команды» (group_id = null). */
const NO_TEAM = '';
type SmtpSecurity = 'ssl' | 'starttls';

interface MailboxFormModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH. */
  mailbox?: MailMailbox;
}

/** Ремоунт по ключу mode+id+open → чистый сброс формы. */
export function MailboxFormModal({ open, onOpenChange, mode, mailbox }: MailboxFormModalProps) {
  const key = `${mode}-${mailbox?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  return (
    <MailboxDialog
      key={key}
      open={open}
      onOpenChange={onOpenChange}
      mode={mode}
      mailbox={mailbox}
    />
  );
}

function teamOptions(teams: MailTeam[]): SelectOption[] {
  return [
    { value: NO_TEAM, label: 'Без команды' },
    ...teams.map((t) => ({ value: String(t.id), label: t.name })),
  ];
}

type FieldErrors = { email?: string; imap?: string; smtp?: string; password?: string };

function MailboxDialog({
  open,
  onOpenChange,
  mode,
  mailbox,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'add' | 'edit';
  mailbox?: MailMailbox;
}) {
  const isEdit = mode === 'edit';
  const initialGroup = mailbox?.group_id != null ? String(mailbox.group_id) : NO_TEAM;

  const [email, setEmail] = useState(mailbox?.email ?? '');
  const [displayName, setDisplayName] = useState(mailbox?.display_name ?? '');
  const [groupId, setGroupId] = useState(initialGroup);
  const [isActive, setIsActive] = useState(mailbox?.is_active ?? true);

  const [imapHost, setImapHost] = useState('');
  const [imapPort, setImapPort] = useState(isEdit ? '' : '993');
  const [imapSsl, setImapSsl] = useState(true);
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState(isEdit ? '' : '465');
  const [smtpSecurity, setSmtpSecurity] = useState<SmtpSecurity>('ssl');
  const [smtpUsername, setSmtpUsername] = useState('');
  const [password, setPassword] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');

  const [errors, setErrors] = useState<FieldErrors>({});

  const teamsQuery = useMailTeams(open);
  const teams = teamsQuery.data?.teams ?? [];

  // «Проверить соединение» бьёт POST /mailboxes/test, закрытый гейтом mail:create
  // (backend/app/api/mail.py: CreateDep). Модалка edit открывается под mail:edit —
  // роль с edit, но без create получила бы 403 по клику; поэтому кнопку рендерим
  // только при mail:create (граница безопасности — на сервере, это UX-гейт).
  const canTest = useCan('mail', 'create');

  const testMutation = useTestMailbox();
  const createMutation = useCreateMailbox();
  const updateMutation = useUpdateMailbox();

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  // Полный набор для проверки соединения / создания. В edit доступен только когда все
  // creds введены заново (backend их не возвращает — прежние значения недоступны).
  const connectionComplete =
    email.trim() !== '' &&
    imapHost.trim() !== '' &&
    imapPort.trim() !== '' &&
    smtpHost.trim() !== '' &&
    smtpPort.trim() !== '' &&
    password.trim() !== '';

  const buildTestPayload = (): MailMailboxTestRequest => ({
    email: email.trim(),
    imap_host: imapHost.trim(),
    imap_port: Number(imapPort),
    imap_ssl: imapSsl,
    smtp_host: smtpHost.trim(),
    smtp_port: Number(smtpPort),
    smtp_ssl: smtpSecurity === 'ssl',
    smtp_starttls: smtpSecurity === 'starttls',
    smtp_username: smtpUsername.trim() || null,
    password,
    smtp_password: smtpPassword.trim() || null,
  });

  const mapError = (err: unknown): void => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        setErrors((p) => ({ ...p, email: 'Ящик с таким адресом уже заведён' }));
        return;
      }
      if (err.status === 422) {
        toast.error('Не удалось подключиться к почтовому серверу. Проверьте креды и хосты.');
        return;
      }
      if (err.status === 400) {
        const next: FieldErrors = {};
        for (const d of err.details ?? []) {
          if (d.field === 'email') next.email = d.message;
          else if (d.field.startsWith('imap')) next.imap = d.message;
          else if (d.field.startsWith('smtp')) next.smtp = d.message;
          else if (d.field === 'password') next.password = d.message;
        }
        if (Object.keys(next).length > 0) setErrors((p) => ({ ...p, ...next }));
        else toast.error('Проверьте корректность полей');
        return;
      }
      toast.error(err.message);
      return;
    }
    toast.error('Не удалось сохранить ящик');
  };

  const handleTest = () => {
    setErrors({});
    testMutation.mutate(buildTestPayload(), {
      onSuccess: (res) => {
        if (res.imap_ok && res.smtp_ok) toast.success('Соединение проверено: IMAP и SMTP доступны');
        else
          toast.error(
            `Проблема соединения: IMAP ${res.imap_ok ? 'ОК' : 'ошибка'}, SMTP ${
              res.smtp_ok ? 'ОК' : 'ошибка'
            }`,
          );
      },
      onError: mapError,
    });
  };

  const validate = (): boolean => {
    const next: FieldErrors = {};
    if (!email.trim()) next.email = 'Укажите адрес';
    if (!isEdit) {
      if (!imapHost.trim()) next.imap = 'Укажите IMAP-хост';
      if (!smtpHost.trim()) next.smtp = 'Укажите SMTP-хост';
      if (!password.trim()) next.password = 'Укажите пароль';
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleCreate = () => {
    const payload: MailMailboxCreateRequest = {
      ...buildTestPayload(),
      display_name: displayName.trim() || null,
      group_id: groupId ? Number(groupId) : null,
    };
    createMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Почта добавлена');
        onOpenChange(false);
      },
      onError: mapError,
    });
  };

  const handleUpdate = () => {
    if (!mailbox) return;
    // Presence-семантика: шлём только изменённые/заполненные поля (04-api.md).
    const payload: MailMailboxUpdateRequest = {};
    if (email.trim() !== mailbox.email) payload.email = email.trim();
    if (displayName.trim() !== (mailbox.display_name ?? ''))
      payload.display_name = displayName.trim() || null;
    if (groupId !== initialGroup) payload.group_id = groupId ? Number(groupId) : null;
    if (isActive !== mailbox.is_active) payload.is_active = isActive;
    // Креды/хосты — только если заполнены заново (backend их не отдаёт).
    if (imapHost.trim()) {
      payload.imap_host = imapHost.trim();
      if (imapPort.trim()) payload.imap_port = Number(imapPort);
      payload.imap_ssl = imapSsl;
    }
    if (smtpHost.trim()) {
      payload.smtp_host = smtpHost.trim();
      if (smtpPort.trim()) payload.smtp_port = Number(smtpPort);
      payload.smtp_ssl = smtpSecurity === 'ssl';
      payload.smtp_starttls = smtpSecurity === 'starttls';
      payload.smtp_username = smtpUsername.trim() || null;
    }
    if (password.trim()) payload.password = password;
    if (smtpPassword.trim()) payload.smtp_password = smtpPassword.trim();

    if (Object.keys(payload).length === 0) {
      onOpenChange(false);
      return;
    }
    updateMutation.mutate(
      { id: mailbox.id, payload },
      {
        onSuccess: () => {
          toast.success('Почта обновлена');
          onOpenChange(false);
        },
        onError: mapError,
      },
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    if (isEdit) handleUpdate();
    else handleCreate();
  };

  const smtpSecurityOptions: SelectOption[] = [
    { value: 'ssl', label: 'SSL/TLS' },
    { value: 'starttls', label: 'STARTTLS' },
  ];

  const passwordHint = isEdit ? 'Оставьте пустым, чтобы не менять пароль.' : undefined;
  const connectionHint = isEdit
    ? 'Параметры подключения не отображаются из соображений безопасности. Заполните только то, что нужно изменить.'
    : undefined;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title={isEdit ? 'Изменить почту' : 'Добавить почту'}
      description={
        isEdit ? undefined : 'IMAP/SMTP-доступ. Пароль передаётся защищённо и не хранится в CRM.'
      }
      dismissible={!isSubmitting}
      size="lg"
      footer={
        <div className="flex w-full items-center justify-between gap-2">
          {canTest ? (
            <Button
              variant="outline"
              onClick={handleTest}
              loading={testMutation.isPending}
              disabled={!connectionComplete || isSubmitting}
            >
              <PlugZap className="h-4 w-4" />
              Проверить соединение
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" form="mailbox-form" loading={isSubmitting}>
              {isEdit ? 'Сохранить' : 'Добавить'}
            </Button>
          </div>
        </div>
      }
    >
      <form
        id="mailbox-form"
        onSubmit={handleSubmit}
        className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto pr-1"
        noValidate
      >
        <Input
          label="Адрес почты"
          type="email"
          value={email}
          error={errors.email}
          autoComplete="off"
          mono
          onChange={(e) => {
            setEmail(e.target.value);
            if (errors.email) setErrors((p) => ({ ...p, email: undefined }));
          }}
        />
        <Input
          label="Отображаемое имя"
          value={displayName}
          autoComplete="off"
          onChange={(e) => setDisplayName(e.target.value)}
        />
        <Select
          label="Команда"
          options={teamOptions(teams)}
          value={groupId}
          onChange={(e) => setGroupId(e.target.value)}
        />

        {connectionHint && <p className="text-[12px] text-text-secondary">{connectionHint}</p>}

        <fieldset className="flex flex-col gap-3 rounded-sub border border-border-subtle p-3">
          <legend className="px-1 text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
            IMAP
          </legend>
          {errors.imap && <p className="text-[12px] text-status-red">{errors.imap}</p>}
          <Input
            label="IMAP-хост"
            value={imapHost}
            mono
            autoComplete="off"
            onChange={(e) => setImapHost(e.target.value)}
          />
          <div className="flex items-end gap-3">
            <div className="w-32">
              <Input
                label="Порт"
                type="number"
                value={imapPort}
                mono
                onChange={(e) => setImapPort(e.target.value)}
              />
            </div>
            <div className="pb-2.5">
              <Checkbox
                label="SSL/TLS"
                checked={imapSsl}
                onChange={(e) => setImapSsl(e.target.checked)}
              />
            </div>
          </div>
        </fieldset>

        <fieldset className="flex flex-col gap-3 rounded-sub border border-border-subtle p-3">
          <legend className="px-1 text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
            SMTP
          </legend>
          {errors.smtp && <p className="text-[12px] text-status-red">{errors.smtp}</p>}
          <Input
            label="SMTP-хост"
            value={smtpHost}
            mono
            autoComplete="off"
            onChange={(e) => setSmtpHost(e.target.value)}
          />
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-32">
              <Input
                label="Порт"
                type="number"
                value={smtpPort}
                mono
                onChange={(e) => setSmtpPort(e.target.value)}
              />
            </div>
            <div className="w-40">
              <Select
                label="Шифрование"
                options={smtpSecurityOptions}
                value={smtpSecurity}
                onChange={(e) => setSmtpSecurity(e.target.value as SmtpSecurity)}
              />
            </div>
          </div>
          <Input
            label="SMTP-логин (опц.)"
            value={smtpUsername}
            mono
            autoComplete="off"
            placeholder="По умолчанию — адрес почты"
            onChange={(e) => setSmtpUsername(e.target.value)}
          />
          <Input
            label="SMTP-пароль (опц.)"
            type="password"
            value={smtpPassword}
            autoComplete="new-password"
            placeholder="По умолчанию — основной пароль"
            onChange={(e) => setSmtpPassword(e.target.value)}
          />
        </fieldset>

        <div className="flex flex-col gap-1.5">
          <Input
            label="Пароль (IMAP)"
            type="password"
            value={password}
            error={errors.password}
            autoComplete="new-password"
            onChange={(e) => {
              setPassword(e.target.value);
              if (errors.password) setErrors((p) => ({ ...p, password: undefined }));
            }}
          />
          {passwordHint && <p className="text-[12px] text-text-secondary">{passwordHint}</p>}
        </div>

        {isEdit && (
          <Checkbox
            label="Активна"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
        )}
      </form>
    </Modal>
  );
}
