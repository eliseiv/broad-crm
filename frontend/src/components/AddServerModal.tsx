import { useState } from 'react';
import { CheckCircle2, Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useCreateServer, useUpdateServer } from '@/features/servers/hooks';
import type { CreateServerRequest, Server, ServerAuthMethod } from '@/types/api';

interface AddServerModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 'add' — создание (по умолчанию); 'edit' — редактирование существующего сервера. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH. */
  server?: Server;
}

/** Поля формы добавления (объединение обеих веток способа входа, ADR-067 §6). */
interface FormValues {
  name: string;
  ip: string;
  ssh_user: string;
  ssh_password: string;
  ssh_private_key: string;
  ssh_key_passphrase: string;
}

type Field = keyof FormValues;
type Errors = Partial<Record<Field, string>>;

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const IPV6 =
  /^(([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|::|([0-9a-fA-F]{1,4}:){1,7}:|(:[0-9a-fA-F]{1,4}){1,7}|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5})$/;

/**
 * Лимит приватного ключа — `SSH_KEY_MAX_BYTES` (04-api.md §POST /api/servers, ADR-067 §3.2):
 * серверный env с дефолтом 16384 байт (16 КБ, RSA-4096 PEM с запасом). Клиент повторяет
 * дефолт, чтобы не отправлять заведомо отклоняемое тело; авторитет — сервер (`422`).
 */
const SSH_KEY_MAX_BYTES = 16384;

/** Длина в БАЙТАХ (лимит контракта — байтовый, ключ может содержать не-ASCII в комментарии). */
function byteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

function isValidIp(value: string): boolean {
  return IPV4.test(value) || IPV6.test(value);
}

const EMPTY: FormValues = {
  name: '',
  ip: '',
  ssh_user: '',
  ssh_password: '',
  ssh_private_key: '',
  ssh_key_passphrase: '',
};

/** Поля, которые сервер может вернуть в `details[].field` (04-api.md §POST /api/servers). */
const FORM_FIELDS: readonly Field[] = [
  'name',
  'ip',
  'ssh_user',
  'ssh_password',
  'ssh_private_key',
  'ssh_key_passphrase',
];

function isFormField(field: string): field is Field {
  return (FORM_FIELDS as readonly string[]).includes(field);
}

/** Поля SSH-материала — единственные, чей текст ошибки `422` приходит с сервера. */
type SecretField = 'ssh_password' | 'ssh_private_key' | 'ssh_key_passphrase';

/**
 * Формулировки отказов по SSH-материалу зафиксированы контрактом (04-api.md §POST
 * /api/servers: «Неверная парольная фраза», «Тип ключа не поддерживается», …) и уже
 * человекочитаемы по-русски — локальные варианты для них не придумываем.
 */
const SERVER_TEXT_FIELDS: readonly SecretField[] = [
  'ssh_password',
  'ssh_private_key',
  'ssh_key_passphrase',
];

function isServerTextField(field: Field): field is SecretField {
  return (SERVER_TEXT_FIELDS as readonly string[]).includes(field);
}

/**
 * Локальные сообщения для общих полей: там backend отдаёт сырой текст pydantic
 * («value is not a valid IPv4 or IPv6 address», «String should have at most 64 characters»),
 * показывать который пользователю нельзя.
 */
const LOCAL_FIELD_ERROR: Record<'name' | 'ip' | 'ssh_user', string> = {
  name: 'Некорректное название (1–64 символа)',
  ip: 'Некорректный IP-адрес',
  ssh_user: 'Некорректный пользователь (1–64 символа)',
};

function validateName(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'Укажите название';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

/**
 * Клиентская валидация по контракту 04-api.md (ADR-067 §3): общие поля + материал **ровно
 * одного** способа входа. Поля чужого режима не проверяются и в тело запроса не попадают —
 * иначе сервер вернёт `422` по правилу «ровно один способ» на ошибку, которой пользователь
 * не совершал.
 */
function validate(values: FormValues, authMethod: ServerAuthMethod): Errors {
  const errors: Errors = {};
  const nameError = validateName(values.name);
  if (nameError) errors.name = nameError;

  const ip = values.ip.trim();
  if (!ip) errors.ip = 'Укажите IP-адрес';
  else if (!isValidIp(ip)) errors.ip = 'Некорректный IPv4/IPv6-адрес';

  const user = values.ssh_user.trim();
  if (!user) errors.ssh_user = 'Укажите пользователя';
  else if (user.length > 64) errors.ssh_user = 'Не более 64 символов';

  if (authMethod === 'password') {
    if (!values.ssh_password) errors.ssh_password = 'Укажите пароль';
    else if (values.ssh_password.length > 256) errors.ssh_password = 'Не более 256 символов';
    return errors;
  }

  if (!values.ssh_private_key.trim()) errors.ssh_private_key = 'Вставьте приватный ключ';
  else if (byteLength(values.ssh_private_key) > SSH_KEY_MAX_BYTES) {
    errors.ssh_private_key = 'Ключ больше 16 КБ';
  }
  if (values.ssh_key_passphrase.length > 256) {
    errors.ssh_key_passphrase = 'Не более 256 символов';
  }
  return errors;
}

/** Тело запроса — материал ровно одного способа (04-api.md §POST /api/servers). */
function buildPayload(values: FormValues, authMethod: ServerAuthMethod): CreateServerRequest {
  const base = {
    name: values.name.trim(),
    ip: values.ip.trim(),
    ssh_user: values.ssh_user.trim(),
  };
  if (authMethod === 'password') {
    // `auth_method` не отправляется: контракт задаёт дефолт `password`, и это ровно та
    // «прежняя форма» тела, что зафиксирована в 04-api.md (ломающего изменения нет).
    return { ...base, ssh_password: values.ssh_password };
  }
  // Ключ уходит как есть: нормализация (CRLF→LF, срез хвостовых пробелов, завершающий \n) —
  // серверная (04-api.md §3), клиент не должен подменять хранимую форму.
  return {
    ...base,
    auth_method: 'key',
    ssh_private_key: values.ssh_private_key,
    // Пустая фраза НЕ отправляется: при незашифрованном ключе заданная фраза → 422.
    ...(values.ssh_key_passphrase ? { ssh_key_passphrase: values.ssh_key_passphrase } : {}),
  };
}

/**
 * Тонкая обёртка: ремоунтит внутренний диалог по ключу mode+id+open, что даёт
 * чистый сброс состояния формы без эффекта (и без подавления линтера).
 */
export function AddServerModal({ open, onOpenChange, mode = 'add', server }: AddServerModalProps) {
  const key = `${mode}-${server?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  if (mode === 'edit' && server) {
    return <EditServerDialog key={key} open={open} onOpenChange={onOpenChange} server={server} />;
  }
  return <AddServerDialog key={key} open={open} onOpenChange={onOpenChange} />;
}

const AUTH_OPTIONS: { value: ServerAuthMethod; label: string }[] = [
  { value: 'password', label: 'Пароль' },
  { value: 'key', label: 'SSH-ключ' },
];

function AddServerDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [values, setValues] = useState<FormValues>(EMPTY);
  const [authMethod, setAuthMethod] = useState<ServerAuthMethod>('password');
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [provisioning, setProvisioning] = useState(false);
  const createMutation = useCreateServer();

  const update = (field: Field, value: string) => {
    setValues((prev) => ({ ...prev, [field]: value }));
    if (touched) setErrors(validate({ ...values, [field]: value }, authMethod));
  };

  /**
   * Переключение способа входа ОЧИЩАЕТ поля другого режима (ADR-067 §6): тело запроса
   * всегда несёт материал ровно одного способа. Ошибки очищенных полей снимаются вместе
   * с ними — иначе на форме осталась бы подсветка невидимого контрола.
   */
  const switchAuthMethod = (next: ServerAuthMethod) => {
    if (next === authMethod) return;
    const cleared: FormValues = {
      ...values,
      ssh_password: '',
      ssh_private_key: '',
      ssh_key_passphrase: '',
    };
    setAuthMethod(next);
    setValues(cleared);
    setShowPassword(false);
    setErrors(touched ? validate(cleared, next) : {});
  };

  const applyApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        setErrors((prev) => ({ ...prev, ip: 'Сервер с таким IP уже добавлен' }));
        toast.error('Сервер с таким IP уже добавлен');
        return;
      }
      // 422/400 с `details[].field` — точное поле нарушения (04-api.md §POST /api/servers).
      //
      // Текст берётся с сервера ТОЛЬКО для полей SSH-материала: их сообщения зафиксированы
      // контрактом и уже человекочитаемы по-русски («Неверная парольная фраза», «Тип ключа
      // не поддерживается» и т.д. — 08-design-system.md §Переключатель «Пароль / SSH-ключ»).
      // Для общих полей (`name`/`ip`/`ssh_user`) в `details[].message` приходит СЫРОЙ текст
      // pydantic («value is not a valid IPv4 or IPv6 address»), поэтому там подставляется
      // локальное русское сообщение. Ветка достижима: клиентская IPv6-регулярка пропускает,
      // например, `:1`, который `IPvAnyAddress` отвергает.
      if ((err.status === 422 || err.status === 400) && err.details?.length) {
        const mapped: Errors = {};
        for (const d of err.details) {
          if (!isFormField(d.field)) continue;
          mapped[d.field] = isServerTextField(d.field) ? d.message : LOCAL_FIELD_ERROR[d.field];
        }
        if (Object.keys(mapped).length > 0) {
          setErrors((prev) => ({ ...prev, ...mapped }));
          toast.error('Проверьте корректность полей');
          return;
        }
      }
      if (err.status === 422) {
        // 422 unprocessable без details — невалидный IP (04-api.md).
        setErrors((prev) => ({ ...prev, ip: 'Некорректный IP-адрес' }));
        toast.error('Некорректный IP-адрес');
        return;
      }
      toast.error(err.message);
      return;
    }
    toast.error('Не удалось добавить сервер');
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nextErrors = validate(values, authMethod);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    createMutation.mutate(buildPayload(values, authMethod), {
      onSuccess: () => {
        toast.success('Сервер добавлен');
        setProvisioning(true);
      },
      onError: applyApiError,
    });
  };

  const isSubmitting = createMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title={provisioning ? 'Сервер добавлен' : 'Добавить сервер'}
      description={
        provisioning
          ? undefined
          : 'Укажите данные для подключения по SSH. Агент мониторинга установится автоматически.'
      }
      dismissible={!isSubmitting}
      footer={
        provisioning ? (
          <Button onClick={() => onOpenChange(false)}>Готово</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" form="add-server-form" loading={isSubmitting}>
              Добавить
            </Button>
          </>
        )
      }
    >
      {provisioning ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <div className="relative">
            <Loader2 className="h-10 w-10 animate-spin text-accent" aria-hidden="true" />
            <CheckCircle2
              className="absolute -bottom-1 -right-1 h-5 w-5 text-status-green"
              aria-hidden="true"
            />
          </div>
          <p className="text-sm font-medium text-text-primary">Установка агента…</p>
          <p className="text-[13px] text-text-secondary">
            Статус установки отображается на карточке сервера и обновляется автоматически.
          </p>
        </div>
      ) : (
        <form
          id="add-server-form"
          onSubmit={handleSubmit}
          // Ветка ключа делает форму высокой (textarea + парольная фраза): на невысоком
          // вьюпорте контент прокручивается ВНУТРИ модалки, а не обрезается ей.
          // -mx/px — чтобы скролл-контейнер не срезал focus-ring полей.
          className="-mx-0.5 flex max-h-[60vh] flex-col gap-4 overflow-y-auto px-0.5 py-0.5"
          noValidate
        >
          <Input
            label="Название"
            placeholder="Сервер 02"
            value={values.name}
            error={errors.name}
            autoFocus
            maxLength={64}
            onChange={(e) => update('name', e.target.value)}
          />
          <Input
            label="IP-адрес"
            placeholder="10.0.0.13"
            mono
            value={values.ip}
            error={errors.ip}
            onChange={(e) => update('ip', e.target.value)}
          />
          <Input
            label="Пользователь"
            placeholder="root"
            value={values.ssh_user}
            error={errors.ssh_user}
            maxLength={64}
            autoComplete="off"
            onChange={(e) => update('ssh_user', e.target.value)}
          />

          {/* Сегментированная radio-группа «Пароль / SSH-ключ» — сразу под «Пользователь»
              (08-design-system.md §Переключатель, ADR-067 §6). Только в режиме add. */}
          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-[13px] font-medium text-text-secondary">
              Способ входа
            </legend>
            <div className="flex w-full rounded-[10px] border border-border-strong bg-surface-2 p-0.5">
              {AUTH_OPTIONS.map((opt) => {
                const active = authMethod === opt.value;
                return (
                  <label
                    key={opt.value}
                    className={cn(
                      'flex flex-1 cursor-pointer items-center justify-center rounded-[8px] px-3 py-1.5 text-sm transition-colors',
                      'focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent',
                      active
                        ? 'bg-accent/15 font-medium text-accent'
                        : 'text-text-secondary hover:text-text-primary',
                    )}
                  >
                    {/* aria-label уточняет назначение опции: без него доступное имя было бы
                        просто «Пароль» — как у поля пароля ниже, и на форме оказались бы два
                        контрола с одинаковым именем. Видимый текст (норма 08-design-system.md)
                        входит в доступное имя целиком (WCAG 2.5.3 Label in Name). */}
                    <input
                      type="radio"
                      name="server-auth-method"
                      value={opt.value}
                      checked={active}
                      disabled={isSubmitting}
                      aria-label={`Способ входа: ${opt.label}`}
                      onChange={() => switchAuthMethod(opt.value)}
                      className="sr-only"
                    />
                    {opt.label}
                  </label>
                );
              })}
            </div>
          </fieldset>

          {authMethod === 'password' ? (
            <Input
              label="Пароль"
              type={showPassword ? 'text' : 'password'}
              placeholder="••••••••"
              value={values.ssh_password}
              error={errors.ssh_password}
              maxLength={256}
              autoComplete="new-password"
              onChange={(e) => update('ssh_password', e.target.value)}
              trailing={
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              }
            />
          ) : (
            <>
              <Textarea
                label="Приватный ключ"
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                className="font-mono text-[13px]"
                rows={7}
                spellCheck={false}
                autoComplete="off"
                value={values.ssh_private_key}
                error={errors.ssh_private_key}
                hint="Вставьте содержимое файла ключа целиком, включая строки BEGIN/END."
                onChange={(e) => update('ssh_private_key', e.target.value)}
              />
              <Input
                label="Парольная фраза (опц.)"
                type="password"
                placeholder="••••••••"
                value={values.ssh_key_passphrase}
                error={errors.ssh_key_passphrase}
                maxLength={256}
                autoComplete="new-password"
                hint="Заполняйте, только если ключ защищён парольной фразой."
                onChange={(e) => update('ssh_key_passphrase', e.target.value)}
              />
            </>
          )}
        </form>
      )}
    </Modal>
  );
}

/**
 * Режим редактирования (08-design-system.md «Режим редактирования модалок»):
 * заголовок «Изменить сервер», кнопка «Сохранить», редактируется ТОЛЬКО «Название»
 * (IP/Пользователь/способ входа и весь SSH-материал вне scope Этапа 1 — ADR-067 §3,
 * TD-072). PATCH /api/servers/{id} { name }.
 */
function EditServerDialog({
  open,
  onOpenChange,
  server,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  server: Server;
}) {
  const [name, setName] = useState(server.name);
  const [error, setError] = useState<string | undefined>(undefined);
  const [touched, setTouched] = useState(false);
  const updateMutation = useUpdateServer(server.id);

  const update = (value: string) => {
    setName(value);
    if (touched) setError(validateName(value));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nameError = validateName(name);
    setError(nameError);
    if (nameError) return;

    updateMutation.mutate(
      { name: name.trim() },
      {
        onSuccess: () => {
          toast.success('Сервер обновлён');
          onOpenChange(false);
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            if (err.status === 400) {
              const detail = err.details?.find((d) => d.field === 'name');
              setError(detail?.message ?? 'Некорректное название');
              toast.error('Проверьте корректность названия');
              return;
            }
            toast.error(err.message);
            return;
          }
          toast.error('Не удалось обновить сервер');
        },
      },
    );
  };

  const isSubmitting = updateMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title="Изменить сервер"
      description="На этом этапе редактируется только название сервера."
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="edit-server-form" loading={isSubmitting}>
            Сохранить
          </Button>
        </>
      }
    >
      <form
        id="edit-server-form"
        onSubmit={handleSubmit}
        className="flex flex-col gap-4"
        noValidate
      >
        <Input
          label="Название"
          placeholder="Сервер 01"
          value={name}
          error={error}
          autoFocus
          maxLength={64}
          onChange={(e) => update(e.target.value)}
        />
      </form>
    </Modal>
  );
}
