import { useState } from 'react';
import { BackendsDetailSection } from '@/components/BackendsDetailSection';
import { DetailEditPencil, DetailRow, SecretRevealField } from '@/components/DetailFields';
import { Modal } from '@/components/ui/Modal';
import { revealAiKeyValue } from '@/features/ai-keys/api';
import { useAiKeyBackends } from '@/features/ai-keys/hooks';
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

/**
 * Read-only detail-модалка ИИ-ключа (08-design-system.md «Detail-view», ADR-035).
 * Поля: Название / Провайдер / Ключ (маска `key_masked`) + reveal полного ключа
 * по требованию под `ai-keys:edit`. Снизу — сворачиваемая секция «Бэки» (`backend_count`,
 * ленивый reverse-lookup, ADR-040). Карандаш под `ai-keys:edit`.
 */
export function AiKeyDetailModal({
  open,
  onOpenChange,
  aiKey,
  canEdit,
  onEdit,
}: AiKeyDetailModalProps) {
  const [backendsOpen, setBackendsOpen] = useState(false);
  const backendsQuery = useAiKeyBackends(aiKey.id, backendsOpen);

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Просмотр"
      headerAction={canEdit ? <DetailEditPencil onClick={onEdit} /> : undefined}
    >
      <div className="flex flex-col gap-4">
        <DetailRow label="Название" value={aiKey.name} />
        <DetailRow label="Провайдер" value={PROVIDER_LABEL[aiKey.provider]} />
        {/* Поле «Ключ» = key_masked; reveal раскрывает полное значение (08-design-system). */}
        <SecretRevealField
          label="Ключ"
          canReveal={canEdit}
          maskDisplay={aiKey.key_masked}
          reveal={(signal) => revealAiKeyValue(aiKey.id, signal)}
          showAria="Показать ключ"
          hideAria="Скрыть ключ"
        />

        <BackendsDetailSection
          count={aiKey.backend_count}
          id={`ai-key-${aiKey.id}-backends`}
          open={backendsOpen}
          onToggle={() => setBackendsOpen((v) => !v)}
          query={backendsQuery}
        />
      </div>
    </Modal>
  );
}
