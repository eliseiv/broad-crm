import { useState } from 'react';
import { CheckCircle2, ChevronDown, Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useAiKeys } from '@/features/ai-keys/hooks';
import { useCreateBackend, useUpdateBackend } from '@/features/backends/hooks';
import { useServers } from '@/features/servers/hooks';
import type { Backend, CreateBackendRequest, UpdateBackendRequest } from '@/types/api';

interface AddBackendModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 'add' — создание (по умолчанию); 'edit' — редактирование бэка. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH. */
  backend?: Backend;
}

/**
 * Значения формы бэка. Основные поля (`code`/`name`/`domain`) обязательны; доп. поля
 * секции «Информация» (ADR-040) опциональны: `serverId`/`aiKeyId` (`''` = «Не выбрано»),
 * секреты `apiKey`/`adminApiKey`, `git`, `note`.
 */
interface BackendFormValues {
  code: string;
  name: string;
  domain: string;
  serverId: string;
  aiKeyId: string;
  apiKey: string;
  adminApiKey: string;
  git: string;
  note: string;
}

type RequiredField = 'code' | 'name' | 'domain';
type ErrorField = RequiredField | 'server_id' | 'ai_key_id';
type Errors = Partial<Record<ErrorField, string>>;

const EMPTY: BackendFormValues = {
  code: '',
  name: '',
  domain: '',
  serverId: '',
  aiKeyId: '',
  apiKey: '',
  adminApiKey: '',
  git: '',
  note: '',
};

function validateCode(code: string): string | undefined {
  const trimmed = code.trim();
  if (!trimmed) return 'Укажите код';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

function validateName(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'Укажите название';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

function validateDomain(domain: string): string | undefined {
  const trimmed = domain.trim();
  if (!trimmed) return 'Укажите домен';
  if (trimmed.length > 255) return 'Не более 255 символов';
  return undefined;
}

function validate(values: BackendFormValues): Errors {
  const errors: Errors = {};
  const codeError = validateCode(values.code);
  if (codeError) errors.code = codeError;
  const nameError = validateName(values.name);
  if (nameError) errors.name = nameError;
  const domainError = validateDomain(values.domain);
  if (domainError) errors.domain = domainError;
  return errors;
}

/**
 * Маппинг ошибок API в пофилдовые (04-api.md, ADR-040):
 *  • 409 backend_code_taken → под полем «Код»: «Код занят»;
 *  • 422 unprocessable → по `details[].field` (`server_id`/`ai_key_id` — несуществующая связь);
 *    без details — невалидный домен под полем «Домен»;
 *  • 400 validation_error → пофилдово по details (длины code/name/domain).
 */
function mapApiError(err: unknown, setErrors: (u: (prev: Errors) => Errors) => void): void {
  if (err instanceof ApiError) {
    if (err.status === 409) {
      setErrors((prev) => ({ ...prev, code: 'Код занят' }));
      return;
    }
    if (err.status === 422) {
      const relational = err.details?.filter(
        (d) => d.field === 'server_id' || d.field === 'ai_key_id',
      );
      if (relational && relational.length > 0) {
        const mapped: Errors = {};
        for (const d of relational) mapped[d.field as ErrorField] = 'Связь не найдена';
        setErrors((prev) => ({ ...prev, ...mapped }));
        toast.error('Проверьте выбранные связи');
        return;
      }
      setErrors((prev) => ({ ...prev, domain: 'Некорректный домен' }));
      toast.error('Некорректный домен');
      return;
    }
    if (err.status === 400 && err.details) {
      const mapped: Errors = {};
      for (const d of err.details) {
        if (d.field === 'code' || d.field === 'name' || d.field === 'domain') {
          mapped[d.field] = d.message;
        }
      }
      setErrors((prev) => ({ ...prev, ...mapped }));
      toast.error('Проверьте корректность полей');
      return;
    }
    toast.error(err.message);
    return;
  }
  toast.error('Не удалось сохранить бэк');
}

/**
 * Обёртка ремоунтит внутренний диалог по ключу mode+id+open → чистый сброс формы
 * без эффекта (паттерн AddProxyModal/AddServerModal).
 */
export function AddBackendModal({
  open,
  onOpenChange,
  mode = 'add',
  backend,
}: AddBackendModalProps) {
  const key = `${mode}-${backend?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  if (mode === 'edit' && backend) {
    return (
      <EditBackendDialog key={key} open={open} onOpenChange={onOpenChange} backend={backend} />
    );
  }
  return <AddBackendDialog key={key} open={open} onOpenChange={onOpenChange} />;
}

/** Опции Select серверов/ключей с ведущей опцией «Не выбрано» (`''` → null связь). */
function useRelationOptions(): { serverOptions: SelectOption[]; aiKeyOptions: SelectOption[] } {
  const serversQuery = useServers();
  const aiKeysQuery = useAiKeys();
  const serverOptions: SelectOption[] = [
    { value: '', label: 'Не выбрано' },
    ...(serversQuery.data?.items ?? []).map((s) => ({ value: s.id, label: s.name })),
  ];
  const aiKeyOptions: SelectOption[] = [
    { value: '', label: 'Не выбрано' },
    ...(aiKeysQuery.data?.items ?? []).map((k) => ({ value: k.id, label: k.name })),
  ];
  return { serverOptions, aiKeyOptions };
}

interface InfoSectionProps {
  values: BackendFormValues;
  errors: Errors;
  update: (field: keyof BackendFormValues, value: string) => void;
  serverOptions: SelectOption[];
  aiKeyOptions: SelectOption[];
  /** true в edit-режиме → подсказка «Оставьте пустым, чтобы не менять» под секретами. */
  editHints: boolean;
}

/**
 * Сворачиваемая секция «Информация» (ADR-040, свёрнута по умолчанию, все поля опциональны).
 * Порядок: Сервер → ИИ-ключ → API KEY → ADMIN API KEY → Git → Примечания (последнее).
 */
function BackendInfoSection({
  values,
  errors,
  update,
  serverOptions,
  aiKeyOptions,
  editHints,
}: InfoSectionProps) {
  const [open, setOpen] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [showAdminApiKey, setShowAdminApiKey] = useState(false);

  const eyeButton = (shown: boolean, onToggle: () => void) => (
    <button
      type="button"
      onClick={onToggle}
      aria-label={shown ? 'Скрыть значение' : 'Показать значение'}
      className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {shown ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
    </button>
  );

  return (
    <div className="rounded-sub border border-border-subtle">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="backend-info-section"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
      >
        <span className="text-[13px] font-medium text-text-secondary">Информация</span>
        <ChevronDown
          className={cn('h-4 w-4 text-text-tertiary transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div
          id="backend-info-section"
          className="flex flex-col gap-4 border-t border-border-subtle px-3 py-3"
        >
          <Select
            label="Сервер"
            options={serverOptions}
            value={values.serverId}
            error={errors.server_id}
            onChange={(e) => update('serverId', e.target.value)}
          />
          <Select
            label="ИИ-ключ"
            options={aiKeyOptions}
            value={values.aiKeyId}
            error={errors.ai_key_id}
            onChange={(e) => update('aiKeyId', e.target.value)}
          />
          {/* Подсказка секрета — в примитиве (`hint`), связана с полем через `aria-describedby`
              (TD-061); соседним `<p>` её рендерить нельзя — скринридер не озвучит. */}
          <Input
            label="API KEY"
            type={showApiKey ? 'text' : 'password'}
            placeholder={editHints ? 'Оставьте пустым, чтобы не менять' : 'sk-backend-…'}
            mono
            value={values.apiKey}
            maxLength={512}
            autoComplete="off"
            hint={editHints ? 'Оставьте пустым, чтобы не менять' : undefined}
            onChange={(e) => update('apiKey', e.target.value)}
            trailing={eyeButton(showApiKey, () => setShowApiKey((v) => !v))}
          />
          <Input
            label="ADMIN API KEY"
            type={showAdminApiKey ? 'text' : 'password'}
            placeholder={editHints ? 'Оставьте пустым, чтобы не менять' : 'sk-admin-…'}
            mono
            value={values.adminApiKey}
            maxLength={512}
            autoComplete="off"
            hint={editHints ? 'Оставьте пустым, чтобы не менять' : undefined}
            onChange={(e) => update('adminApiKey', e.target.value)}
            trailing={eyeButton(showAdminApiKey, () => setShowAdminApiKey((v) => !v))}
          />
          <Input
            label="Git"
            placeholder="https://github.com/acme/api-eu"
            mono
            value={values.git}
            maxLength={512}
            autoComplete="off"
            onChange={(e) => update('git', e.target.value)}
          />
          <Textarea
            label="Примечания"
            placeholder="Свободные примечания"
            rows={3}
            value={values.note}
            maxLength={2000}
            onChange={(e) => update('note', e.target.value)}
          />
        </div>
      )}
    </div>
  );
}

function AddBackendDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [values, setValues] = useState<BackendFormValues>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [checking, setChecking] = useState(false);
  const createMutation = useCreateBackend();
  const { serverOptions, aiKeyOptions } = useRelationOptions();

  const update = (field: keyof BackendFormValues, value: string) => {
    const next = { ...values, [field]: value } as BackendFormValues;
    setValues(next);
    if (touched) setErrors(validate(next));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nextErrors = validate(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    // Опциональные поля отправляются только заданными (иначе — без связи/секрета).
    const payload: CreateBackendRequest = {
      code: values.code.trim(),
      name: values.name.trim(),
      domain: values.domain.trim(),
    };
    if (values.serverId) payload.server_id = values.serverId;
    if (values.aiKeyId) payload.ai_key_id = values.aiKeyId;
    const apiKey = values.apiKey.trim();
    if (apiKey) payload.api_key = apiKey;
    const adminApiKey = values.adminApiKey.trim();
    if (adminApiKey) payload.admin_api_key = adminApiKey;
    const git = values.git.trim();
    if (git) payload.git = git;
    const note = values.note.trim();
    if (note) payload.note = note;

    createMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Бэк добавлен');
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
      title={checking ? 'Бэк добавлен' : 'Добавить бэк'}
      description={
        checking ? undefined : 'Доступность проверяется автоматически по https://{домен}/health.'
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
            <Button type="submit" form="add-backend-form" loading={isSubmitting}>
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
          <p className="text-sm font-medium text-text-primary">Проверка бэка…</p>
          <p className="text-[13px] text-text-secondary">
            Статус проверки отображается на карточке бэка и обновляется автоматически.
          </p>
        </div>
      ) : (
        <form
          id="add-backend-form"
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <Input
            label="Код"
            placeholder="api-eu"
            mono
            value={values.code}
            error={errors.code}
            autoFocus
            maxLength={64}
            autoComplete="off"
            onChange={(e) => update('code', e.target.value)}
          />
          <Input
            label="Название"
            placeholder="API EU"
            value={values.name}
            error={errors.name}
            maxLength={64}
            onChange={(e) => update('name', e.target.value)}
          />
          <Input
            label="Домен"
            placeholder="api.example.com"
            mono
            value={values.domain}
            error={errors.domain}
            maxLength={255}
            autoComplete="off"
            onChange={(e) => update('domain', e.target.value)}
          />
          <BackendInfoSection
            values={values}
            errors={errors}
            update={update}
            serverOptions={serverOptions}
            aiKeyOptions={aiKeyOptions}
            editHints={false}
          />
        </form>
      )}
    </Modal>
  );
}

/**
 * Режим редактирования (08-design-system.md, ADR-040): заголовок «Изменить бэк», кнопка
 * «Сохранить». Префил code/name/domain + server_id/ai_key_id/git/note; секреты — пустые
 * (не префилятся; пустое = «не менять», TD-035). PATCH /api/backends/{id}: отправляются
 * ТОЛЬКО изменённые поля (04-api.md exclude_unset). FK: «Не выбрано» → null (обнулить);
 * git/note: очистка поля → null. Смена domain → check_status='pending' (возобновление
 * polling). Смена code на занятый → 409 пофилдово под «Код».
 */
function EditBackendDialog({
  open,
  onOpenChange,
  backend,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backend: Backend;
}) {
  const [values, setValues] = useState<BackendFormValues>({
    code: backend.code,
    name: backend.name,
    domain: backend.domain,
    serverId: backend.server_id ?? '',
    aiKeyId: backend.ai_key_id ?? '',
    apiKey: '',
    adminApiKey: '',
    git: backend.git ?? '',
    note: backend.note ?? '',
  });
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const updateMutation = useUpdateBackend(backend.id);
  const { serverOptions, aiKeyOptions } = useRelationOptions();

  const update = (field: keyof BackendFormValues, value: string) => {
    const next = { ...values, [field]: value } as BackendFormValues;
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
    const payload: UpdateBackendRequest = {};
    const code = values.code.trim();
    if (code !== backend.code) payload.code = code;
    const name = values.name.trim();
    if (name !== backend.name) payload.name = name;
    const domain = values.domain.trim();
    if (domain !== backend.domain) payload.domain = domain;

    // FK: «Не выбрано» (`''`) → null (обнулить); выбранный uuid → установить.
    const serverId = values.serverId || null;
    if (serverId !== (backend.server_id ?? null)) payload.server_id = serverId;
    const aiKeyId = values.aiKeyId || null;
    if (aiKeyId !== (backend.ai_key_id ?? null)) payload.ai_key_id = aiKeyId;

    // Секреты: непустое → установить; пустое = не менять (очистка через UI не выполняется, TD-035).
    const apiKey = values.apiKey.trim();
    if (apiKey) payload.api_key = apiKey;
    const adminApiKey = values.adminApiKey.trim();
    if (adminApiKey) payload.admin_api_key = adminApiKey;

    // git/note: значение → установить; очистка поля (`''`) → null (очистить).
    const git = values.git.trim();
    const prevGit = backend.git ?? '';
    if (git !== prevGit) payload.git = git || null;
    const note = values.note.trim();
    const prevNote = backend.note ?? '';
    if (note !== prevNote) payload.note = note || null;

    // Нет изменений — просто закрываем без запроса.
    if (Object.keys(payload).length === 0) {
      onOpenChange(false);
      return;
    }

    updateMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Бэк обновлён');
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
      title="Изменить бэк"
      description="Обновите основные поля бэка и дополнительную информацию."
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="edit-backend-form" loading={isSubmitting}>
            Сохранить
          </Button>
        </>
      }
    >
      <form
        id="edit-backend-form"
        onSubmit={handleSubmit}
        className="flex flex-col gap-4"
        noValidate
      >
        <Input
          label="Код"
          placeholder="api-eu"
          mono
          value={values.code}
          error={errors.code}
          autoFocus
          maxLength={64}
          autoComplete="off"
          onChange={(e) => update('code', e.target.value)}
        />
        <Input
          label="Название"
          placeholder="API EU"
          value={values.name}
          error={errors.name}
          maxLength={64}
          onChange={(e) => update('name', e.target.value)}
        />
        <Input
          label="Домен"
          placeholder="api.example.com"
          mono
          value={values.domain}
          error={errors.domain}
          maxLength={255}
          autoComplete="off"
          onChange={(e) => update('domain', e.target.value)}
        />
        <BackendInfoSection
          values={values}
          errors={errors}
          update={update}
          serverOptions={serverOptions}
          aiKeyOptions={aiKeyOptions}
          editHints
        />
      </form>
    </Modal>
  );
}
