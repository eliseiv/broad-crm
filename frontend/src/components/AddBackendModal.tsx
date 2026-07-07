import { useState } from 'react';
import { CheckCircle2, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { useCreateBackend, useUpdateBackend } from '@/features/backends/hooks';
import type { Backend, CreateBackendRequest, UpdateBackendRequest } from '@/types/api';

interface AddBackendModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 'add' — создание (по умолчанию); 'edit' — редактирование бэка. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH. */
  backend?: Backend;
}

interface BackendFormValues {
  code: string;
  name: string;
  domain: string;
}

type Field = keyof BackendFormValues;
type Errors = Partial<Record<Field, string>>;

const EMPTY: BackendFormValues = { code: '', name: '', domain: '' };

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
 * Маппинг ошибок API в пофилдовые (04-api.md):
 *  • 409 backend_code_taken → под полем «Код»: «Код занят»;
 *  • 422 unprocessable → под полем «Домен» (невалидный формат);
 *  • 400 validation_error → пофилдово по details (длины code/name/domain).
 */
function mapApiError(err: unknown, setErrors: (u: (prev: Errors) => Errors) => void): void {
  if (err instanceof ApiError) {
    if (err.status === 409) {
      setErrors((prev) => ({ ...prev, code: 'Код занят' }));
      return;
    }
    if (err.status === 422) {
      setErrors((prev) => ({ ...prev, domain: 'Некорректный домен' }));
      toast.error('Некорректный домен');
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
  toast.error('Не удалось сохранить бэк');
}

/**
 * Обёртка ремоунтит внутренний диалог по ключу mode+id+open → чистый сброс формы
 * без эффекта (паттерн AddProxyModal/AddServerModal).
 */
export function AddBackendModal({ open, onOpenChange, mode = 'add', backend }: AddBackendModalProps) {
  const key = `${mode}-${backend?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  if (mode === 'edit' && backend) {
    return <EditBackendDialog key={key} open={open} onOpenChange={onOpenChange} backend={backend} />;
  }
  return <AddBackendDialog key={key} open={open} onOpenChange={onOpenChange} />;
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

  const update = (field: Field, value: string) => {
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

    const payload: CreateBackendRequest = {
      code: values.code.trim(),
      name: values.name.trim(),
      domain: values.domain.trim(),
    };

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
      description={checking ? undefined : 'Доступность проверяется автоматически по https://{домен}/health.'}
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
        <form id="add-backend-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
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
        </form>
      )}
    </Modal>
  );
}

/**
 * Режим редактирования (08-design-system.md): заголовок «Изменить бэк», кнопка «Сохранить».
 * Префил code/name/domain. PATCH /api/backends/{id}: отправляются ТОЛЬКО изменённые поля
 * (04-api.md семантика exclude_unset). При смене domain backend вернёт check_status='pending' →
 * карточка (после invalidate) возобновит polling через useBackendStatus. Смена code на занятый
 * другим бэком → 409 пофилдово под «Код».
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
  });
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const updateMutation = useUpdateBackend(backend.id);

  const update = (field: Field, value: string) => {
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
      description="Обновите код, название или домен бэка."
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
      <form id="edit-backend-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
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
      </form>
    </Modal>
  );
}
