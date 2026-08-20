import { useState } from 'react';
import { Eye, EyeOff, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { BalanceFormFields } from '@/components/BalanceFormFields';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useCreateAiKey, useUpdateAiKey } from '@/features/ai-keys/hooks';
import type { AiKey, AiProvider, CreateAiKeyRequest, UpdateAiKeyRequest } from '@/types/api';

interface AddAiKeyModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 'add' — создание (по умолчанию); 'edit' — редактирование ключа. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила (name/provider) и id для PATCH. */
  aiKey?: AiKey;
  /** Предвыбор провайдера при создании из секции провайдера (add-режим). */
  defaultProvider?: AiProvider;
}

type Field = keyof CreateAiKeyRequest | 'balance_initial_usd' | 'billing_admin_key';
type Errors = Partial<Record<Field, string>>;

const PROVIDER_OPTIONS: SelectOption[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
];

const EMPTY: CreateAiKeyRequest = { name: '', provider: 'openai', key: '' };

function validateAdd(values: {
  name: string;
  provider: AiProvider;
  key: string;
  balanceMonitoring: boolean;
  balanceUsd: string;
  thresholdUsd: string;
  billingAdminKey: string;
}): Errors {
  const errors: Errors = {};
  const name = values.name.trim();
  if (!name) errors.name = 'Укажите название';
  else if (name.length > 64) errors.name = 'Не более 64 символов';

  if (values.provider !== 'openai' && values.provider !== 'anthropic') {
    errors.provider = 'Выберите провайдера';
  }

  if (!values.key.trim()) errors.key = 'Укажите ключ';
  else if (values.key.length > 512) errors.key = 'Не более 512 символов';

  if (values.balanceMonitoring) {
    const bal = values.balanceUsd.trim();
    if (!bal) errors.balance_initial_usd = 'Укажите текущий баланс';
    else if (Number(bal) < 0 || !Number.isFinite(Number(bal))) {
      errors.balance_initial_usd = 'Некорректная сумма';
    }
    const thr = values.thresholdUsd.trim();
    if (thr && (Number(thr) < 0 || !Number.isFinite(Number(thr)))) {
      errors.balance_low_threshold_usd = 'Некорректная сумма';
    }
    if (!values.billingAdminKey.trim()) {
      errors.billing_admin_key = 'Укажите Admin API key';
    } else if (values.billingAdminKey.length > 512) {
      errors.billing_admin_key = 'Не более 512 символов';
    }
  }

  return errors;
}

/**
 * Обёртка ремоунтит внутренний диалог по ключу mode+id+open → чистый сброс формы
 * без эффекта (паттерн AddServerModal).
 */
export function AddAiKeyModal({
  open,
  onOpenChange,
  mode = 'add',
  aiKey,
  defaultProvider,
}: AddAiKeyModalProps) {
  const key = `${mode}-${aiKey?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  if (mode === 'edit' && aiKey) {
    return <EditAiKeyDialog key={key} open={open} onOpenChange={onOpenChange} aiKey={aiKey} />;
  }
  return (
    <AddAiKeyDialog
      key={key}
      open={open}
      onOpenChange={onOpenChange}
      defaultProvider={defaultProvider}
    />
  );
}

function AddAiKeyDialog({
  open,
  onOpenChange,
  defaultProvider,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultProvider?: AiProvider;
}) {
  const [values, setValues] = useState<CreateAiKeyRequest>({
    ...EMPTY,
    provider: defaultProvider ?? EMPTY.provider,
  });
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [checking, setChecking] = useState(false);
  const [balanceMonitoring, setBalanceMonitoring] = useState(false);
  const [balanceUsd, setBalanceUsd] = useState('');
  const [thresholdUsd, setThresholdUsd] = useState('10');
  const [billingAdminKey, setBillingAdminKey] = useState('');
  const [showBillingAdminKey, setShowBillingAdminKey] = useState(false);
  const createMutation = useCreateAiKey();

  const revalidateAdd = (
    overrides?: Partial<{
      name: string;
      provider: AiProvider;
      key: string;
      balanceMonitoring: boolean;
      balanceUsd: string;
      thresholdUsd: string;
      billingAdminKey: string;
    }>,
  ) => {
    if (touched) {
      setErrors(
        validateAdd({
          name: values.name,
          provider: values.provider,
          key: values.key,
          balanceMonitoring,
          balanceUsd,
          thresholdUsd,
          billingAdminKey,
          ...overrides,
        }),
      );
    }
  };

  const update = (field: 'name' | 'provider' | 'key', value: string) => {
    const next = { ...values, [field]: value } as CreateAiKeyRequest;
    setValues(next);
    if (touched) {
      revalidateAdd({
        name: next.name,
        provider: next.provider,
        key: next.key,
      });
    }
  };

  const applyApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 422) {
        setErrors((prev) => ({ ...prev, provider: 'Недопустимый провайдер' }));
        toast.error('Недопустимый провайдер');
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
    toast.error('Не удалось добавить ключ');
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nextErrors = validateAdd({
      name: values.name,
      provider: values.provider,
      key: values.key,
      balanceMonitoring,
      balanceUsd,
      thresholdUsd,
      billingAdminKey,
    });
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    const payload: CreateAiKeyRequest = {
      name: values.name.trim(),
      provider: values.provider,
      key: values.key.trim(),
    };
    if (balanceMonitoring) {
      payload.balance_monitoring_enabled = true;
      payload.balance_initial_usd = balanceUsd.trim();
      payload.balance_low_threshold_usd = thresholdUsd.trim() || '10';
      payload.billing_admin_key = billingAdminKey.trim();
    }

    createMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Ключ добавлен');
        setChecking(true);
      },
      onError: applyApiError,
    });
  };

  const isSubmitting = createMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title={checking ? 'Ключ добавлен' : 'Добавить AI-ключ'}
      description={
        checking ? undefined : 'Ключ будет зашифрован. Валидность проверяется автоматически.'
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
            <Button type="submit" form="add-ai-key-form" loading={isSubmitting}>
              Добавить
            </Button>
          </>
        )
      }
    >
      {checking ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <Loader2 className="h-10 w-10 animate-spin text-accent" aria-hidden="true" />
          <p className="text-sm font-medium text-text-primary">Проверка ключа…</p>
          <p className="text-[13px] text-text-secondary">
            Статус проверки отображается на карточке ключа и обновляется автоматически.
          </p>
        </div>
      ) : (
        <form
          id="add-ai-key-form"
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <Input
            label="Название"
            placeholder="OpenAI Prod"
            value={values.name}
            error={errors.name}
            autoFocus
            maxLength={64}
            onChange={(e) => update('name', e.target.value)}
          />
          <Select
            label="Провайдер"
            options={PROVIDER_OPTIONS}
            value={values.provider}
            error={errors.provider}
            onChange={(e) => update('provider', e.target.value as AiProvider)}
          />
          <Input
            label="Ключ"
            type={showKey ? 'text' : 'password'}
            placeholder="sk-proj-…"
            mono
            value={values.key}
            error={errors.key}
            maxLength={512}
            autoComplete="off"
            onChange={(e) => update('key', e.target.value)}
            trailing={
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                aria-label={showKey ? 'Скрыть ключ' : 'Показать ключ'}
                className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            }
          />
          <BalanceFormFields
            enabled={balanceMonitoring}
            onEnabledChange={(v) => {
              setBalanceMonitoring(v);
              revalidateAdd({ balanceMonitoring: v });
            }}
            balanceUsd={balanceUsd}
            onBalanceUsdChange={(v) => {
              setBalanceUsd(v);
              revalidateAdd({ balanceUsd: v });
            }}
            thresholdUsd={thresholdUsd}
            onThresholdUsdChange={(v) => {
              setThresholdUsd(v);
              revalidateAdd({ thresholdUsd: v });
            }}
            billingAdminKey={billingAdminKey}
            onBillingAdminKeyChange={(v) => {
              setBillingAdminKey(v);
              revalidateAdd({ billingAdminKey: v });
            }}
            showBillingAdminKey={showBillingAdminKey}
            onToggleShowBillingAdminKey={() => setShowBillingAdminKey((s) => !s)}
            errors={{
              balance_initial_usd: errors.balance_initial_usd,
              balance_low_threshold_usd: errors.balance_low_threshold_usd,
              billing_admin_key: errors.billing_admin_key,
            }}
            disabled={isSubmitting}
          />
        </form>
      )}
    </Modal>
  );
}

/**
 * Режим редактирования (08-design-system.md): заголовок «Изменить ключ», кнопка
 * «Сохранить». Префил name+provider; поле «Ключ» ПУСТОЕ (секрет не префилится —
 * backend его не отдаёт), с подсказкой «Оставьте пустым, чтобы не менять ключ».
 * PATCH /api/ai-keys/{id}: name/provider отправляются, key — только если непустой.
 * При смене provider/key backend вернёт check_status='pending' → карточка (после
 * invalidate) возобновит polling через useAiKeyStatus.
 */
function EditAiKeyDialog({
  open,
  onOpenChange,
  aiKey,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  aiKey: AiKey;
}) {
  const [name, setName] = useState(aiKey.name);
  const [provider, setProvider] = useState<AiProvider>(aiKey.provider);
  const [key, setKey] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [touched, setTouched] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [balanceMonitoring, setBalanceMonitoring] = useState(aiKey.balance_monitoring_enabled);
  const [balanceUsd, setBalanceUsd] = useState(
    aiKey.balance_remaining_usd ?? aiKey.balance_initial_usd ?? '',
  );
  const [thresholdUsd, setThresholdUsd] = useState(aiKey.balance_low_threshold_usd ?? '10');
  const [billingAdminKey, setBillingAdminKey] = useState('');
  const [showBillingAdminKey, setShowBillingAdminKey] = useState(false);
  const updateMutation = useUpdateAiKey(aiKey.id);

  const validateEdit = (values: {
    name: string;
    provider: AiProvider;
    key: string;
    balanceMonitoring: boolean;
    balanceUsd: string;
    thresholdUsd: string;
    billingAdminKey: string;
  }): Errors => {
    const next: Errors = {};
    const trimmedName = values.name.trim();
    if (!trimmedName) next.name = 'Укажите название';
    else if (trimmedName.length > 64) next.name = 'Не более 64 символов';

    if (values.provider !== 'openai' && values.provider !== 'anthropic')
      next.provider = 'Выберите провайдера';

    if (values.key.length > 512) next.key = 'Не более 512 символов';

    if (values.balanceMonitoring) {
      const bal = values.balanceUsd.trim();
      if (!bal) next.balance_initial_usd = 'Укажите текущий баланс';
      else if (Number(bal) < 0 || !Number.isFinite(Number(bal))) {
        next.balance_initial_usd = 'Некорректная сумма';
      }
      const thr = values.thresholdUsd.trim();
      if (thr && (Number(thr) < 0 || !Number.isFinite(Number(thr)))) {
        next.balance_low_threshold_usd = 'Некорректная сумма';
      }
      if (!aiKey.balance_monitoring_enabled && !values.billingAdminKey.trim()) {
        next.billing_admin_key = 'Укажите Admin API key';
      }
      if (values.billingAdminKey.length > 512) {
        next.billing_admin_key = 'Не более 512 символов';
      }
    }

    return next;
  };

  const revalidate = (
    overrides?: Partial<{
      name: string;
      provider: AiProvider;
      key: string;
      balanceMonitoring: boolean;
      balanceUsd: string;
      thresholdUsd: string;
      billingAdminKey: string;
    }>,
  ) => {
    if (touched) {
      setErrors(
        validateEdit({
          name,
          provider,
          key,
          balanceMonitoring,
          balanceUsd,
          thresholdUsd,
          billingAdminKey,
          ...overrides,
        }),
      );
    }
  };

  const applyApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 422) {
        setErrors((prev) => ({ ...prev, provider: 'Недопустимый провайдер' }));
        toast.error('Недопустимый провайдер');
        return;
      }
      if (err.status === 400 && err.details) {
        const mapped: Errors = {};
        for (const d of err.details) {
          if (d.field === 'name' || d.field === 'key' || d.field === 'provider') {
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
    toast.error('Не удалось обновить ключ');
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const nextErrors = validateEdit({
      name,
      provider,
      key,
      balanceMonitoring,
      balanceUsd,
      thresholdUsd,
      billingAdminKey,
    });
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    const payload: UpdateAiKeyRequest = { name: name.trim(), provider };
    const trimmedKey = key.trim();
    if (trimmedKey) payload.key = trimmedKey;

    payload.balance_monitoring_enabled = balanceMonitoring;
    if (balanceMonitoring) {
      payload.balance_initial_usd = balanceUsd.trim();
      payload.balance_low_threshold_usd = thresholdUsd.trim() || '10';
      if (billingAdminKey.trim()) payload.billing_admin_key = billingAdminKey.trim();
    }

    updateMutation.mutate(payload, {
      onSuccess: () => {
        toast.success('Ключ обновлён');
        onOpenChange(false);
      },
      onError: applyApiError,
    });
  };

  const isSubmitting = updateMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title="Изменить ключ"
      description="Обновите название, провайдера или сам ключ."
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="edit-ai-key-form" loading={isSubmitting}>
            Сохранить
          </Button>
        </>
      }
    >
      <form
        id="edit-ai-key-form"
        onSubmit={handleSubmit}
        className="flex flex-col gap-4"
        noValidate
      >
        <Input
          label="Название"
          placeholder="OpenAI Prod"
          value={name}
          error={errors.name}
          autoFocus
          maxLength={64}
          onChange={(e) => {
            setName(e.target.value);
            revalidate({ name: e.target.value });
          }}
        />
        <Select
          label="Провайдер"
          options={PROVIDER_OPTIONS}
          value={provider}
          error={errors.provider}
          onChange={(e) => {
            const nextProvider = e.target.value as AiProvider;
            setProvider(nextProvider);
            revalidate({ provider: nextProvider });
          }}
        />
        {/* Подсказка — в примитиве (`hint`), связана с полем через `aria-describedby` (TD-061).
            Она НЕ исчезает при появлении ошибки: `aria-describedby` композируется из ОБОИХ id
            (подсказка, затем ошибка) — ошибка не вытесняет подсказку (08-design-system.md). */}
        <Input
          label="Ключ"
          type={showKey ? 'text' : 'password'}
          placeholder="Оставьте пустым, чтобы не менять"
          mono
          value={key}
          error={errors.key}
          maxLength={512}
          autoComplete="off"
          hint="Оставьте пустым, чтобы не менять ключ"
          onChange={(e) => {
            setKey(e.target.value);
            revalidate({ key: e.target.value });
          }}
          trailing={
            <button
              type="button"
              onClick={() => setShowKey((v) => !v)}
              aria-label={showKey ? 'Скрыть ключ' : 'Показать ключ'}
              className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          }
        />
        <BalanceFormFields
          enabled={balanceMonitoring}
          onEnabledChange={(v) => {
            setBalanceMonitoring(v);
            revalidate({ balanceMonitoring: v });
          }}
          balanceUsd={balanceUsd}
          onBalanceUsdChange={(v) => {
            setBalanceUsd(v);
            revalidate({ balanceUsd: v });
          }}
          thresholdUsd={thresholdUsd}
          onThresholdUsdChange={(v) => {
            setThresholdUsd(v);
            revalidate({ thresholdUsd: v });
          }}
          billingAdminKey={billingAdminKey}
          onBillingAdminKeyChange={(v) => {
            setBillingAdminKey(v);
            revalidate({ billingAdminKey: v });
          }}
          showBillingAdminKey={showBillingAdminKey}
          onToggleShowBillingAdminKey={() => setShowBillingAdminKey((s) => !s)}
          errors={{
            balance_initial_usd: errors.balance_initial_usd,
            balance_low_threshold_usd: errors.balance_low_threshold_usd,
            billing_admin_key: errors.billing_admin_key,
          }}
          billingAdminOptional={aiKey.balance_monitoring_enabled}
          disabled={isSubmitting}
        />
      </form>
    </Modal>
  );
}
