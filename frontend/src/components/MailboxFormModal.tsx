import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, ChevronDown, Copy, ExternalLink, PlugZap } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { cn } from '@/lib/cn';
import { env } from '@/lib/env';
import { ApiError } from '@/lib/api';
import { useCan, useSeesAllMailTeams } from '@/features/auth/hooks';
import { listMailboxes } from '@/features/mail/api';
import {
  mailMailboxesKey,
  useCreateMailbox,
  useMailboxOAuthAuthorize,
  useTestMailbox,
  useUpdateMailbox,
} from '@/features/mail/hooks';
import { useTeams } from '@/features/teams/hooks';
import type {
  MailMailbox,
  MailMailboxCreateRequest,
  MailMailboxTestRequest,
  MailMailboxUpdateRequest,
  TeamListItem,
} from '@/types/api';

/** Значение опции «Без команды» (team_id = null). */
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

function teamOptions(teams: TeamListItem[]): SelectOption[] {
  return [
    { value: NO_TEAM, label: 'Без команды' },
    ...teams.map((t) => ({ value: t.id, label: t.name })),
  ];
}

/** Внешняя ссылка справки — открывается в новой вкладке (08-design-system.md). */
function Ext({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent underline-offset-2 hover:underline"
    >
      {children}
    </a>
  );
}

/** Инлайн-токен (хост/адрес/код ошибки) моноширинным. */
function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-surface-3 px-1 py-0.5 font-mono text-[12px] text-text-primary">
      {children}
    </code>
  );
}

/** Вложенный свёрнутый пункт-провайдер аккордеона «Как добавить почту?». */
function HelpItem({ summary, children }: { summary: React.ReactNode; children: React.ReactNode }) {
  return (
    <details className="group/item rounded-sub border border-border-subtle bg-surface-2">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-[13px] font-medium text-text-primary marker:content-none [&::-webkit-details-marker]:hidden">
        <span>{summary}</span>
        <ChevronDown
          className="h-4 w-4 shrink-0 text-text-tertiary transition-transform group-open/item:rotate-180"
          aria-hidden="true"
        />
      </summary>
      <div className="border-t border-border-subtle px-3 py-2 text-[13px] leading-relaxed text-text-secondary">
        {children}
      </div>
    </details>
  );
}

/**
 * Аккордеон «Как добавить почту?» — нормативный текст 08-design-system.md (перенос help-box
 * агрегатора). Свёрнут по умолчанию; только режим add. Строки/порядок/ссылки — побуквенно.
 */
function MailHelpAccordion() {
  return (
    <details className="group rounded-card border border-border-subtle bg-surface-1">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-[13px] font-semibold text-text-primary marker:content-none [&::-webkit-details-marker]:hidden">
        <span>Как добавить почту?</span>
        <ChevronDown
          className="h-4 w-4 shrink-0 text-text-tertiary transition-transform group-open:rotate-180"
          aria-hidden="true"
        />
      </summary>
      <div className="flex flex-col gap-2 border-t border-border-subtle px-3 py-3">
        <p className="text-[13px] leading-relaxed text-text-secondary">
          Современные почтовые сервисы запрещают сторонним приложениям вход по обычному паролю.
          Нужен <strong className="text-text-primary">«пароль приложения»</strong> (app password) —
          отдельный длинный пароль, который вставляется сюда вместо основного. Параметры IMAP/SMTP
          для популярных доменов подставятся автоматически после ввода адреса.
        </p>

        <HelpItem
          summary={
            <>
              Gmail (<Code>@gmail.com</Code>)
            </>
          }
        >
          1) Включить 2FA:{' '}
          <Ext href="https://myaccount.google.com/security">myaccount.google.com/security</Ext> →
          «2-Step Verification». 2) Включить IMAP: <Ext href="https://mail.google.com">Gmail</Ext> →
          «See all settings» → «Forwarding and POP/IMAP» → «Enable IMAP». 3) Создать app password:{' '}
          <Ext href="https://myaccount.google.com/apppasswords">
            myaccount.google.com/apppasswords
          </Ext>{' '}
          → скопировать 16-символьный пароль <em>(показывается один раз)</em>. 4) В форме: адрес{' '}
          <Code>you@gmail.com</Code>, пароль — этот 16-символьный <em>(без пробелов)</em>. Хосты:{' '}
          <Code>imap.gmail.com:993 SSL</Code>, <Code>smtp.gmail.com:465 SSL</Code>.{' '}
          <strong className="text-text-primary">
            ⚠️ Корпоративный Workspace Gmail: app passwords часто отключены администратором домена →
            подключиться не получится (OAuth не поддерживается).
          </strong>
        </HelpItem>

        <HelpItem
          summary={
            <>
              Яндекс (<Code>@yandex.ru</Code>, <Code>@yandex.com</Code>, <Code>@ya.ru</Code>)
            </>
          }
        >
          1) 2FA: <Ext href="https://id.yandex.ru/security">id.yandex.ru/security</Ext>. 2) Включить
          IMAP: <Ext href="https://mail.yandex.ru">mail.yandex.ru</Ext> → «Все настройки» →
          «Почтовые программы» → «С сервера imap.yandex.ru по протоколу IMAP». 3) «Пароли
          приложений» → «Создать» → «Почта». 4) Хосты: <Code>imap.yandex.ru:993 SSL</Code>,{' '}
          <Code>smtp.yandex.ru:465 SSL</Code>.
        </HelpItem>

        <HelpItem
          summary={
            <>
              Mail.ru и семейство (<Code>@mail.ru</Code>, <Code>@inbox.ru</Code>,{' '}
              <Code>@bk.ru</Code>, <Code>@list.ru</Code>)
            </>
          }
        >
          1) 2FA:{' '}
          <Ext href="https://account.mail.ru/user/2-step-auth/">
            account.mail.ru/user/2-step-auth
          </Ext>
          . 2) «Пароли для внешних приложений»:{' '}
          <Ext href="https://account.mail.ru/user/2-step-auth/passwords">
            account.mail.ru/user/2-step-auth/passwords
          </Ext>{' '}
          → «Добавить». 3) IMAP включён по умолчанию. 4) Хосты: <Code>imap.mail.ru:993 SSL</Code>,{' '}
          <Code>smtp.mail.ru:465 SSL</Code>.
        </HelpItem>

        <HelpItem
          summary={
            <>
              Outlook / Hotmail / Live (<Code>@outlook.com</Code>, <Code>@hotmail.com</Code>,{' '}
              <Code>@live.com</Code>)
            </>
          }
        >
          <strong className="text-text-primary">
            Личные Outlook-ящики с сентября 2024 не принимают пароль приложения — используйте кнопку
            «Подключить Outlook (OAuth)» ниже
          </strong>{' '}
          (см. секцию «Outlook» ниже).{' '}
          <strong className="text-text-primary">
            ⚠️ Корпоративный Office 365 (рабочий ящик своего домена): app passwords часто запрещены,
            OAuth рабочего тенанта недоступен — подключить нельзя.
          </strong>
        </HelpItem>

        <HelpItem
          summary={
            <>
              Apple iCloud (<Code>@icloud.com</Code>, <Code>@me.com</Code>, <Code>@mac.com</Code>)
            </>
          }
        >
          1) 2FA для Apple ID: <Ext href="https://appleid.apple.com">appleid.apple.com</Ext> →
          «Sign-In and Security». 2) «App-Specific Passwords» → «Generate Password» (формат{' '}
          <Code>xxxx-xxxx-xxxx-xxxx</Code>). 3) Хосты вручную: <Code>imap.mail.me.com:993 SSL</Code>
          , <Code>smtp.mail.me.com:587 STARTTLS</Code>; в «SMTP-логин» укажите свой email{' '}
          <em>(для iCloud обязательно)</em>.
        </HelpItem>

        <HelpItem
          summary={
            <>
              Yahoo Mail (<Code>@yahoo.com</Code>, <Code>@yahoo.ru</Code>, <Code>@ymail.com</Code>)
            </>
          }
        >
          1) 2FA + app password:{' '}
          <Ext href="https://login.yahoo.com/account/security">
            login.yahoo.com/account/security
          </Ext>{' '}
          → «Generate and manage app passwords». 2) Хосты вручную:{' '}
          <Code>imap.mail.yahoo.com:993 SSL</Code>, <Code>smtp.mail.yahoo.com:465 SSL</Code>.
        </HelpItem>

        <HelpItem summary="Другой провайдер / свой сервер">
          Найдите настройки на сайте провайдера («IMAP settings»). Обычно: IMAP{' '}
          <Code>imap.&lt;домен&gt;:993 SSL</Code>, SMTP <Code>smtp.&lt;домен&gt;:465 SSL</Code> либо{' '}
          <Code>587 STARTTLS</Code>; логин — полный email (иногда нужен отдельный SMTP-логин).{' '}
          <strong className="text-text-primary">
            ⚠️ ProtonMail без платной подписки не работает
          </strong>{' '}
          (IMAP/SMTP — только через локальный ProtonMail Bridge, недоступный серверу).
        </HelpItem>

        <HelpItem summary="Если «Проверить соединение» не работает">
          <Code>Application-specific password required</Code> (Gmail) — нужен app password, не
          обычный пароль. <Code>imap_login_failed</Code>/<Code>Invalid credentials</Code> — пароль
          неверный или нужен app password. <Code>imap_connect_error</Code>/
          <Code>smtp_connect_error</Code> — неверный host/port/SSL, сверьте с таблицей.{' '}
          <Code>invalid_host</Code> — введён <Code>localhost</Code>/приватный IP (блокируется
          защитой от SSRF). <Code>Web login required</Code> — провайдер требует первый раз войти в
          браузере. IMAP проходит, SMTP падает — заполните «SMTP-логин» своим email.
        </HelpItem>
      </div>
    </details>
  );
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
  const initialTeam = mailbox?.team_id != null ? mailbox.team_id : NO_TEAM;

  const [email, setEmail] = useState(mailbox?.email ?? '');
  // Два поля имени вместо «Отображаемого имени» (ADR-047 §3.6): `display_name` —
  // производное, сервер считает его сам и клиентом оно НЕ отправляется.
  const [number, setNumber] = useState(mailbox?.number ?? '');
  const [appName, setAppName] = useState(mailbox?.app_name ?? '');
  const [teamId, setTeamId] = useState(initialTeam);
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

  // --- Outlook (OAuth) состояние (только режим add) ---
  // authorizeUrl !== null → показана панель-ссылка и запущен пуллинг списка ящиков.
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  // 503 mail_not_configured → Outlook-OAuth выключен: кнопка скрыта, показано сообщение.
  const [oauthUnavailable, setOauthUnavailable] = useState(false);
  const [urlCopied, setUrlCopied] = useState(false);
  // Снимок id ящиков на момент старта пуллинга — детект появления нового.
  const baselineIds = useRef<Set<number> | null>(null);

  const teamsQuery = useTeams(open);
  const teams = teamsQuery.data?.items ?? [];

  // «Проверить соединение» бьёт POST /mailboxes/test, закрытый гейтом mail:create
  // (backend/app/api/mail.py: CreateDep). Модалка edit открывается под mail:edit —
  // роль с edit, но без create получила бы 403 по клику; поэтому кнопку рендерим
  // только при mail:create (граница безопасности — на сервере, это UX-гейт).
  const canTest = useCan('mail', 'create');
  // Перенос ящика между командами (смена team_id в edit) — только admin-уровень (ADR-044 §4).
  // При создании выбор команды доступен всегда (рядовой участник привязывает к своей).
  const seesAllTeams = useSeesAllMailTeams();
  const teamSelectDisabled = isEdit && !seesAllTeams;

  const testMutation = useTestMailbox();
  const createMutation = useCreateMailbox();
  const updateMutation = useUpdateMailbox();
  const authorizeMutation = useMailboxOAuthAuthorize();

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  // Пуллинг списка ящиков, пока открыта Outlook-панель: новый ящик появится, когда
  // агрегатор долетит уведомлением (POST /api/mail/oauth/ingest, ADR-045 §3/§5).
  const watchQuery = useQuery({
    queryKey: mailMailboxesKey,
    queryFn: ({ signal }) => listMailboxes({}, signal),
    enabled: authorizeUrl !== null,
    refetchInterval: authorizeUrl !== null ? env.pollIntervalMs : false,
    refetchIntervalInBackground: false,
    retry: false,
  });

  useEffect(() => {
    if (authorizeUrl === null) {
      baselineIds.current = null;
      return;
    }
    const boxes = watchQuery.data?.mailboxes;
    if (!boxes) return;
    if (baselineIds.current === null) {
      baselineIds.current = new Set(boxes.map((b) => b.id));
      return;
    }
    const appeared = boxes.some((b) => !baselineIds.current!.has(b.id));
    if (appeared) {
      toast.success('Ящик Outlook подключён');
      setAuthorizeUrl(null);
      onOpenChange(false);
    }
  }, [authorizeUrl, watchQuery.data, onOpenChange]);

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
      if (err.status === 404) {
        toast.error('Выбранная команда не найдена');
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

  // Клик «Подключить Outlook (OAuth)»: отправляет тот же team_id, что и обычное создание
  // ящика (handleCreate: `teamId || null`) — ADR-045 §5, Вариант B. Отдельного гейта
  // «сначала выберите команду» нет; admin вправе подключить без команды (team_id=null),
  // не-admin ограничен селектором своих команд. По 200 — панель-ссылка + пуллинг; 503 — «недоступно».
  const handleOAuthConnect = () => {
    authorizeMutation.mutate(teamId || null, {
      onSuccess: (res) => {
        setAuthorizeUrl(res.authorize_url);
      },
      onError: (err) => {
        if (err instanceof ApiError) {
          if (err.status === 503) {
            setOauthUnavailable(true);
            return;
          }
          if (err.status === 404) {
            toast.error('Выбранная команда не найдена');
            return;
          }
          if (err.status === 502) {
            toast.error('Почтовый сервис временно недоступен. Попробуйте позже.');
            return;
          }
          toast.error(err.message);
          return;
        }
        toast.error('Не удалось начать подключение Outlook');
      },
    });
  };

  const handleCopyUrl = async () => {
    if (authorizeUrl === null) return;
    try {
      await navigator.clipboard.writeText(authorizeUrl);
      setUrlCopied(true);
      toast.success('Скопировано');
      window.setTimeout(() => setUrlCopied(false), 1500);
    } catch {
      toast.error('Не удалось скопировать');
    }
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
      number: number.trim() || null,
      app_name: appName.trim() || null,
      team_id: teamId || null,
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
    if (number.trim() !== (mailbox.number ?? '')) payload.number = number.trim() || null;
    if (appName.trim() !== (mailbox.app_name ?? '')) payload.app_name = appName.trim() || null;
    if (teamId !== initialTeam) payload.team_id = teamId || null;
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
        {!isEdit && <MailHelpAccordion />}

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
        {/* «Номер» + «Приложение» вместо упразднённого «Отображаемого имени» (ADR-047 §3.6);
            оба опциональны. `display_name` — производное, сервер вычисляет его сам. */}
        <div className="flex flex-wrap gap-3">
          <div className="min-w-[140px] flex-1">
            <Input
              label="Номер"
              value={number}
              autoComplete="off"
              onChange={(e) => setNumber(e.target.value)}
            />
          </div>
          <div className="min-w-[180px] flex-[2]">
            <Input
              label="Приложение"
              value={appName}
              autoComplete="off"
              onChange={(e) => setAppName(e.target.value)}
            />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Select
            label="Команда"
            options={teamOptions(teams)}
            value={teamId}
            disabled={teamSelectDisabled}
            onChange={(e) => setTeamId(e.target.value)}
          />
          {teamSelectDisabled && (
            <p className="text-[12px] text-text-secondary">
              Перенос между командами доступен только администратору.
            </p>
          )}
        </div>

        {!isEdit && (
          <section className="flex flex-col gap-2 rounded-sub border border-border-subtle bg-surface-1 p-3">
            <div className="flex flex-col gap-0.5">
              <h3 className="text-[13px] font-semibold text-text-primary">Outlook</h3>
              <p className="text-[12px] text-text-secondary">
                Для ящиков <Code>@outlook.com</Code>, <Code>@hotmail.com</Code>,{' '}
                <Code>@live.com</Code>
              </p>
            </div>

            {oauthUnavailable ? (
              <p className="text-[13px] text-text-secondary">
                Подключение Outlook временно недоступно. Обратитесь к администратору или добавьте
                ящик вручную ниже.
              </p>
            ) : authorizeUrl !== null ? (
              <div className="flex flex-col gap-2">
                <p className="text-[13px] leading-relaxed text-text-secondary">
                  <strong className="text-text-primary">
                    Откройте эту ссылку в нужном профиле OctoBrowser
                  </strong>
                  , войдите в аккаунт Outlook и подтвердите доступ. После подтверждения ящик
                  появится в списке.
                </p>
                <div className="flex items-center gap-2">
                  <Input
                    aria-label="Ссылка для авторизации Outlook"
                    value={authorizeUrl}
                    readOnly
                    mono
                    className="text-[12px]"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleCopyUrl}
                    className="shrink-0"
                  >
                    {urlCopied ? (
                      <Check className="h-4 w-4 text-status-green" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                    Скопировать
                  </Button>
                  <a
                    href={authorizeUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={cn(
                      'inline-flex h-8 shrink-0 select-none items-center justify-center gap-1.5 rounded-md px-3 text-[13px] font-medium',
                      'border border-border-strong bg-surface-2 text-text-primary transition-colors duration-150',
                      'hover:border-accent hover:bg-surface-3',
                      'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                    )}
                  >
                    <ExternalLink className="h-4 w-4" />
                    Открыть
                  </a>
                </div>
                <p className="text-[12px] text-text-tertiary">
                  Кнопка «Открыть» откроет ссылку в текущем браузере — используйте только если вы
                  уже в нужном профиле; для OctoBrowser скопируйте ссылку.
                </p>
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleOAuthConnect}
                loading={authorizeMutation.isPending}
                className="self-start"
              >
                <PlugZap className="h-4 w-4" />
                Подключить Outlook (OAuth)
              </Button>
            )}
          </section>
        )}

        {connectionHint && <p className="text-[12px] text-text-secondary">{connectionHint}</p>}

        {!isEdit && (
          <div className="flex items-center gap-3 py-1">
            <span className="h-px flex-1 bg-border-subtle" />
            <span className="text-[12px] text-text-tertiary">
              или добавьте ящик вручную (IMAP / пароль)
            </span>
            <span className="h-px flex-1 bg-border-subtle" />
          </div>
        )}

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
