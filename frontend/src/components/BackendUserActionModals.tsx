import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import {
  useAddBackendUserTokens,
  useBackendProducts,
  useGrantBackendUserSubscription,
} from '@/features/backend-users/hooks';

interface ActionModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backendId: string;
  userId: string;
}

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Не удалось выполнить операцию';
}

/**
 * «Начислить токены» (contract v1 §3.1). Операция НЕ идемпотентна — повторный сабмит
 * начислит повторно, поэтому кнопка блокируется на время запроса (`loading`), а модалка
 * становится недismissible. Отрицательное значение — списание (минус-баланс бэк отвергнет 400).
 */
export function AddTokensModal({ open, onOpenChange, backendId, userId }: ActionModalProps) {
  const [amount, setAmount] = useState('');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const mutation = useAddBackendUserTokens(backendId, userId);

  useEffect(() => {
    if (open) {
      setAmount('');
      setFieldError(null);
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset только при открытии
  }, [open]);

  const submit = () => {
    const parsed = Number(amount);
    if (!Number.isInteger(parsed) || parsed === 0) {
      setFieldError('Введите целое число, не равное 0');
      return;
    }
    setFieldError(null);
    mutation.mutate(
      { amount: parsed },
      {
        onSuccess: (res) => {
          toast.success(
            `${parsed > 0 ? 'Начислено' : 'Списано'} ${Math.abs(parsed).toLocaleString('ru-RU')} токенов. Баланс: ${res.tokens.toLocaleString('ru-RU')}`,
          );
          onOpenChange(false);
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  };

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Начислить токены"
      description="Отрицательное значение — списание. Операция необратима и выполняется сразу."
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={submit} loading={mutation.isPending}>
            Начислить
          </Button>
        </>
      }
    >
      <Input
        label="Количество токенов"
        placeholder="Например: 1000 или -500"
        inputMode="numeric"
        value={amount}
        error={fieldError}
        onChange={(e) => setAmount(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && !mutation.isPending && submit()}
      />
    </Modal>
  );
}

/**
 * «Установить план» (contract v1 §3.2). Идемпотентность: `grant_id` генерируется при
 * ОТКРЫТИИ модалки — повторный сабмит той же формы (даблклик/ретрай) бэк распознает и
 * не продлит подписку дважды (`applied=false`).
 */
export function GrantPlanModal({ open, onOpenChange, backendId, userId }: ActionModalProps) {
  const [productId, setProductId] = useState('');
  const [days, setDays] = useState('30');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [grantId, setGrantId] = useState('');
  const mutation = useGrantBackendUserSubscription(backendId, userId);
  const productsQuery = useBackendProducts(backendId, open);

  useEffect(() => {
    if (open) {
      setProductId('');
      setDays('30');
      setFieldError(null);
      setGrantId(crypto.randomUUID());
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset только при открытии
  }, [open]);

  const options = useMemo(() => {
    const items = productsQuery.data?.items ?? [];
    return [
      { value: '', label: items.length > 0 ? 'Выберите тариф…' : 'Тарифы недоступны' },
      ...items.map((p) => ({
        value: p.product_id,
        label: [p.name, p.price, p.period].filter(Boolean).join(' · '),
      })),
    ];
  }, [productsQuery.data?.items]);

  const submit = () => {
    const parsedDays = Number(days);
    if (!productId) {
      setFieldError('Выберите тариф');
      return;
    }
    if (!Number.isInteger(parsedDays) || parsedDays <= 0 || parsedDays > 3660) {
      setFieldError('Срок — целое число дней от 1 до 3660');
      return;
    }
    setFieldError(null);
    mutation.mutate(
      { product_id: productId, expires_in_days: parsedDays, grant_id: grantId },
      {
        onSuccess: (res) => {
          if (res.applied) {
            toast.success('План установлен');
          } else {
            toast.info('Повтор: этот план уже был выдан данным действием');
          }
          onOpenChange(false);
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  };

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Установить план"
      description="Продление активной подписки добавляет дни к текущему сроку."
      dismissible={!mutation.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            Отмена
          </Button>
          <Button onClick={submit} loading={mutation.isPending} disabled={productsQuery.isLoading}>
            Установить
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Select
          label="Тариф"
          options={options}
          value={productId}
          onChange={(e) => setProductId(e.target.value)}
          disabled={productsQuery.isLoading}
          error={productsQuery.error instanceof ApiError ? productsQuery.error.message : undefined}
        />
        <Input
          label="Срок, дней"
          inputMode="numeric"
          value={days}
          error={fieldError}
          onChange={(e) => setDays(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !mutation.isPending && submit()}
        />
      </div>
    </Modal>
  );
}
