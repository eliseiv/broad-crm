import { useState } from 'react';
import { AiKeyBalanceDisplay } from '@/components/AiKeyBalanceDisplay';
import { BackendsDetailSection } from '@/components/BackendsDetailSection';
import {
  DetailEditPencil,
  DetailInfoSection,
  DetailRow,
  SecretRevealField,
} from '@/components/DetailFields';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { revealAiKeyBillingAdminKey, revealAiKeyValue } from '@/features/ai-keys/api';
import { useAiKeyBackends, useResetAiKeyBalance } from '@/features/ai-keys/hooks';
import type { AiKey, AiProvider } from '@/types/api';

/** Локализованное имя провайдера (08-design-system.md, словарь). */
const PROVIDER_LABEL: Record<AiProvider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
};

interface AiKeyDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  aiKey: AiKey;
  /** Право `ai-keys:edit` — гейт карандаша и кнопки-глаза reveal. */
  canEdit: boolean;
  /** Клик по карандашу: закрыть detail и открыть edit-модалку (ADR-035). */
  onEdit: () => void;
}

export function AiKeyDetailModal({
  open,
  onOpenChange,
  aiKey,
  canEdit,
  onEdit,
}: AiKeyDetailModalProps) {
  const [backendsOpen, setBackendsOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBalanceUsd, setResetBalanceUsd] = useState('');
  const backendsQuery = useAiKeyBackends(aiKey.id, backendsOpen);
  const resetMutation = useResetAiKeyBalance(aiKey.id);

  const openReset = () => {
    setResetBalanceUsd(aiKey.balance_remaining_usd ?? aiKey.balance_initial_usd ?? '');
    setResetOpen(true);
  };

  const submitReset = () => {
    const trimmed = resetBalanceUsd.trim();
    if (!trimmed || Number(trimmed) < 0 || !Number.isFinite(Number(trimmed))) return;
    resetMutation.mutate(
      { balance_initial_usd: trimmed },
      { onSuccess: () => setResetOpen(false) },
    );
  };

  return (
    <>
      <Modal
        open={open}
        onOpenChange={onOpenChange}
        title="Просмотр"
        headerAction={canEdit ? <DetailEditPencil onClick={onEdit} /> : undefined}
      >
        <div className="flex flex-col gap-4">
          <DetailRow label="Название" value={aiKey.name} />
          <DetailRow label="Провайдер" value={PROVIDER_LABEL[aiKey.provider]} />

          {aiKey.balance_monitoring_enabled && (
            <div className="flex flex-col gap-2">
              <AiKeyBalanceDisplay aiKey={aiKey} />
              {canEdit && (
                <Button variant="outline" size="sm" onClick={openReset}>
                  Обновить баланс после пополнения
                </Button>
              )}
            </div>
          )}

          <DetailInfoSection>
            <SecretRevealField
              label="Ключ"
              canReveal={canEdit}
              maskDisplay={aiKey.key_masked}
              reveal={(signal) => revealAiKeyValue(aiKey.id, signal)}
              showAria="Показать ключ"
              hideAria="Скрыть ключ"
            />

            {aiKey.balance_monitoring_enabled && (
              <SecretRevealField
                label="Admin API key"
                canReveal={canEdit}
                maskDisplay="Admin key (скрыт)"
                reveal={(signal) => revealAiKeyBillingAdminKey(aiKey.id, signal)}
                showAria="Показать Admin key"
                hideAria="Скрыть Admin key"
              />
            )}

            <BackendsDetailSection
              count={aiKey.backend_count}
              id={`ai-key-${aiKey.id}-backends`}
              open={backendsOpen}
              onToggle={() => setBackendsOpen((v) => !v)}
              query={backendsQuery}
            />
          </DetailInfoSection>
        </div>
      </Modal>

      <Modal
        open={resetOpen}
        onOpenChange={(next) => !resetMutation.isPending && setResetOpen(next)}
        title="Обновить баланс"
        description="Укажите актуальный баланс из личного кабинета провайдера. Якорь расхода будет сброшен."
        dismissible={!resetMutation.isPending}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setResetOpen(false)}
              disabled={resetMutation.isPending}
            >
              Отмена
            </Button>
            <Button loading={resetMutation.isPending} onClick={submitReset}>
              Сохранить
            </Button>
          </>
        }
      >
        <Input
          label="Текущий баланс, $"
          type="number"
          min={0}
          step="0.01"
          value={resetBalanceUsd}
          onChange={(e) => setResetBalanceUsd(e.target.value)}
          autoFocus
        />
      </Modal>
    </>
  );
}
