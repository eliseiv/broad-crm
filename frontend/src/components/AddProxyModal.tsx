import { useState } from 'react';
import { CheckCircle2, Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useCreateProxy, useUpdateProxy } from '@/features/proxies/hooks';
import type { CreateProxyRequest, Proxy, ProxyType, UpdateProxyRequest } from '@/types/api';

interface AddProxyModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 'add' — создание (по умолчанию); 'edit' — редактирование прокси. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH. */
  proxy?: Proxy;
}

/** Значения формы прокси (порт — строка, т.к. нативный <input>). */
interface ProxyFormValues {
  name: string;
  proxy_type: ProxyType;
  host: string;
  port: string;
  username: string;
  password: string;
}

type Field = keyof ProxyFormValues;
type Errors = Partial<Record<Field, string>>;

const TYPE_OPTIONS: SelectOption[] = [
  { value: 'http', label: 'HTTP' },
  { value: 'https', label: 'HTTPS' },
  { value: 'socks5', label: 'SOCKS5' },
];

const EMPTY: ProxyFormValues = {
  name: '',
  proxy_type: 'http',
  host: '',
  port: '',
  username: '',
  password: '',
};

function validateName(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'Укажите название';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

function validateHost(host: string): string | undefined {
  const trimmed = host.trim();
  if (!trimmed) return 'Укажите хост';
  if (trimmed.length > 255) return 'Не более 255 символов';
  return undefined;
}

function validatePort(port: string): string | undefined {
  const trimmed = port.trim();
  if (!trimmed) return 'Укажите порт';
  if (!/^\d+$/.test(trimmed)) return 'Только цифры';
  const num = Number(trimmed);
  if (num < 1 || num > 65535) return 'Порт от 1 до 65535';
  return undefined;
}

function validateUsername(username: string): string | undefined {
  if (username.length > 255) return 'Не более 255 символов';
  return undefined;
}

function validatePassword(password: string): string | undefined {
  if (password.length > 512) return 'Не более 512 символов';
  return undefined;
}

/** Общая валидация (create и edit): пароль/логин опциональны. */
function validate(values: ProxyFormValues): Errors {
  const errors: Errors = {};
  const nameError = validateName(values.name);
  if (nameError) errors.name = nameError;

  if (
    values.proxy_type !== 'http' &&
    values.proxy_type !== 'https' &&
    values.proxy_type !== 'socks5'
  ) {
    errors.proxy_type = 'Выберите тип';
  }

  const hostError = validateHost(values.host);
  if (hostError) errors.host = hostError;

  const portError = validatePort(values.port);
  if (portError) errors.port = portError;

  const usernameError = validateUsername(values.username);
  if (usernameError) errors.username = usernameError;

  const passwordError = validatePassword(values.password);
  if (passwordError) errors.password = passwordError;

  return errors;
}

/** Маппинг ошибок API в пофилдовые (образец AddServerModal / AddAiKeyModal). */
function mapApiError(err: unknown, setErrors: (u: (prev: Errors) => Errors) => void): void {
  if (err instanceof ApiError) {
    if (err.status === 422) {
      // 04-api.md: невалидный proxy_type / port вне диапазона.
      setErrors((prev) => ({ ...prev, port: 'Недопустимый тип или порт' }));
      toast.error('Недопустимый тип или порт прокси');
      return;
    }
    if (err.status === 400 && err.details) {
      const mapped: Errors = {};
      for (const d of err.details) {
        if (d.field in EMPTY) mapped[d.field as Field] = d.message;
      }
      setErrors((prev) => ({ ...prev, ...mapped }));
      toast.error('Проверьте корректность полей');
      return;
    }
    toast.error(err.message);
    return;
  }
  toast.error('Не удалось сохранить прокси');
}

/**
 * Обёртка ремоунтит внутренний диалог по ключу mode+id+open → чистый сброс формы
 * без эффекта (паттерн AddServerModal/AddAiKeyModal).
 */
export function AddProxyModal({ open, onOpenChange, mode = 'add', proxy }: AddProxyModalProps) {
  const key = `${mode}-${proxy?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  if (mode === 'edit' && proxy) {
    return <EditProxyDialog key={key} open={open} onOpenChange={onOpenChange} proxy={proxy} />;
  }
  return <AddProxyDialog key={key} open={open} onOpenChange={onOpenChange} />;
}

function AddProxyDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [values, setValues] = useState<ProxyFormValues>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [checking, setChecking] = useState(false);
  const createMutation = useCreateProxy();

  const update = (field: Field, value: string) => {
    const next = { ...values, [field]: value } as ProxyFormValues;
    setValues(next);
    if (touched) setErrors(validate(next));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nextErrors = validate(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    // username/password опциональны: отправляются только если непустые
    // (04-api.md: отсутствует/пусто → без логина/пароля).
    const payload: CreateProxyRequest = {
      name: values.name.trim(),
      proxy_type: values.proxy_type,
      host: values.host.trim(),
      port: Number(values.port.trim()),
    };
    const username = values.username.trim();
    if (username) payload.username = username;
    if (values.password) payload.password = values.password;

    createMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Прокси добавлен');
        setChecking(true);
      },
      onError: (err) => mapApiError(err, setErrors),
    });
  };

  const isSubmitting = createMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title={checking ? 'Прокси добавлен' : 'Добавить прокси'}
      description={
        checking ? undefined : 'Данные будут зашифрованы. Доступность проверяется автоматически.'
      }
      dismissible={!isSubmitting}
      footer={
        checking ? (
          <Button onClick={() => onOpenChange(false)}>Готово</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
              Отмена
            </Button>
            <Button type="submit" form="add-proxy-form" loading={isSubmitting}>
              Добавить
            </Button>
          </>
        )
      }
    >
      {checking ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <div className="relative">
            <Loader2 className="h-10 w-10 animate-spin text-accent" aria-hidden="true" />
            <CheckCircle2
              className="absolute -bottom-1 -right-1 h-5 w-5 text-status-green"
              aria-hidden="true"
            />
          </div>
          <p className="text-sm font-medium text-text-primary">Проверка прокси…</p>
          <p className="text-[13px] text-text-secondary">
            Статус проверки отображается на карточке прокси и обновляется автоматически.
          </p>
        </div>
      ) : (
        <form
          id="add-proxy-form"
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <Input
            label="Название"
            placeholder="DE Residential"
            value={values.name}
            error={errors.name}
            autoFocus
            maxLength={64}
            onChange={(e) => update('name', e.target.value)}
          />
          <Select
            label="Тип"
            options={TYPE_OPTIONS}
            value={values.proxy_type}
            error={errors.proxy_type}
            onChange={(e) => update('proxy_type', e.target.value as ProxyType)}
          />
          <Input
            label="Хост"
            placeholder="proxy.example.com"
            mono
            value={values.host}
            error={errors.host}
            maxLength={255}
            onChange={(e) => update('host', e.target.value)}
          />
          <Input
            label="Порт"
            placeholder="1080"
            mono
            inputMode="numeric"
            value={values.port}
            error={errors.port}
            maxLength={5}
            onChange={(e) => update('port', e.target.value)}
          />
          <Input
            label="Логин"
            placeholder="user01"
            value={values.username}
            error={errors.username}
            maxLength={255}
            autoComplete="off"
            onChange={(e) => update('username', e.target.value)}
          />
          <Input
            label="Пароль"
            type={showPassword ? 'text' : 'password'}
            placeholder="••••••••"
            value={values.password}
            error={errors.password}
            maxLength={512}
            autoComplete="new-password"
            onChange={(e) => update('password', e.target.value)}
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
        </form>
      )}
    </Modal>
  );
}

/**
 * Режим редактирования (08-design-system.md): заголовок «Изменить прокси», кнопка
 * «Сохранить». Префил name/proxy_type/host/port/username; поле «Пароль» ПУСТОЕ
 * (секрет не префилится — backend его не отдаёт), с подсказкой «Оставьте пустым,
 * чтобы не менять пароль». PATCH /api/proxies/{id}: отправляются ТОЛЬКО изменённые поля
 * (04-api.md семантика exclude_unset). Пароль отправляется только если введён непустой.
 * При смене связанного с подключением поля backend вернёт check_status='pending' →
 * карточка (после invalidate) возобновит polling через useProxyStatus.
 */
function EditProxyDialog({
  open,
  onOpenChange,
  proxy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  proxy: Proxy;
}) {
  const [values, setValues] = useState<ProxyFormValues>({
    name: proxy.name,
    proxy_type: proxy.proxy_type,
    host: proxy.host,
    port: String(proxy.port),
    username: proxy.username ?? '',
    password: '',
  });
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const updateMutation = useUpdateProxy(proxy.id);

  const update = (field: Field, value: string) => {
    const next = { ...values, [field]: value } as ProxyFormValues;
    setValues(next);
    if (touched) setErrors(validate(next));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nextErrors = validate(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    // Отправляем ТОЛЬКО изменённые поля (04-api.md: exclude_unset).
    const payload: UpdateProxyRequest = {};
    const name = values.name.trim();
    if (name !== proxy.name) payload.name = name;
    if (values.proxy_type !== proxy.proxy_type) payload.proxy_type = values.proxy_type;
    const host = values.host.trim();
    if (host !== proxy.host) payload.host = host;
    const port = Number(values.port.trim());
    if (port !== proxy.port) payload.port = port;

    // username: сравниваем с текущим (null трактуем как ''). Пусто → очистить логин (null).
    const username = values.username.trim();
    const currentUsername = proxy.username ?? '';
    if (username !== currentUsername) payload.username = username === '' ? null : username;

    // password: пустое поле = «не менять» (не отправляем); непустое → заменить.
    if (values.password) payload.password = values.password;

    // Нет изменений — просто закрываем без запроса.
    if (Object.keys(payload).length === 0) {
      onOpenChange(false);
      return;
    }

    updateMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Прокси обновлён');
        onOpenChange(false);
      },
      onError: (err) => mapApiError(err, setErrors),
    });
  };

  const isSubmitting = updateMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title="Изменить прокси"
      description="Обновите параметры подключения прокси."
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="edit-proxy-form" loading={isSubmitting}>
            Сохранить
          </Button>
        </>
      }
    >
      <form id="edit-proxy-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <Input
          label="Название"
          placeholder="DE Residential"
          value={values.name}
          error={errors.name}
          autoFocus
          maxLength={64}
          onChange={(e) => update('name', e.target.value)}
        />
        <Select
          label="Тип"
          options={TYPE_OPTIONS}
          value={values.proxy_type}
          error={errors.proxy_type}
          onChange={(e) => update('proxy_type', e.target.value as ProxyType)}
        />
        <Input
          label="Хост"
          placeholder="proxy.example.com"
          mono
          value={values.host}
          error={errors.host}
          maxLength={255}
          onChange={(e) => update('host', e.target.value)}
        />
        <Input
          label="Порт"
          placeholder="1080"
          mono
          inputMode="numeric"
          value={values.port}
          error={errors.port}
          maxLength={5}
          onChange={(e) => update('port', e.target.value)}
        />
        <Input
          label="Логин"
          placeholder="user01"
          value={values.username}
          error={errors.username}
          maxLength={255}
          autoComplete="off"
          onChange={(e) => update('username', e.target.value)}
        />
        <div className="flex flex-col gap-1.5">
          <Input
            label="Пароль"
            type={showPassword ? 'text' : 'password'}
            placeholder="Оставьте пустым, чтобы не менять"
            value={values.password}
            error={errors.password}
            maxLength={512}
            autoComplete="new-password"
            onChange={(e) => update('password', e.target.value)}
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
          {!errors.password && (
            <p className="text-[12px] text-text-secondary">
              Оставьте пустым, чтобы не менять пароль
            </p>
          )}
        </div>
      </form>
    </Modal>
  );
}
