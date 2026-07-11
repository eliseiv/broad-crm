import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import { arrayMove, rectSortingStrategy, SortableContext } from '@dnd-kit/sortable';
import { AddAiKeyModal } from '@/components/AddAiKeyModal';
import { AiKeyCard } from '@/components/AiKeyCard';
import { AiKeyCardSkeleton } from '@/components/AiKeyCardSkeleton';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { SortableItem } from '@/components/SortableItem';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useAiKeys, useReorderAiKeys } from '@/features/ai-keys/hooks';
import type { AiKey, AiProvider } from '@/types/api';

/** Фиксированный порядок секций: сначала OpenAI, затем Anthropic (08-design-system.md). */
const PROVIDER_ORDER: AiProvider[] = ['openai', 'anthropic'];
const PROVIDER_LABEL: Record<AiProvider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
};

export function AiKeysPage() {
  // Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
  // прямой URL/навигация без `ai-keys:view` → заглушка «Недостаточно прав»
  // (page-scoped), а не контент. Супер-админ/admin — всегда доступ; список не
  // запрашивается без права.
  const canView = useCanViewPage('ai-keys');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <AiKeysList />;
}

function AiKeysList() {
  const { data, isLoading, isError, error, refetch, isFetching } = useAiKeys();
  const [addOpen, setAddOpen] = useState(false);
  const [addProvider, setAddProvider] = useState<AiProvider | undefined>(undefined);

  // RBAC-гейтинг действий (08-design-system.md «Гейтинг навигации и действий»).
  const canCreate = useCan('ai-keys', 'create');
  const canEdit = useCan('ai-keys', 'edit');
  const canDelete = useCan('ai-keys', 'delete');

  const isAuthError = error instanceof ApiError && error.status === 401;
  // 403 (RBAC): показываем «Недостаточно прав» вместо generic (08-design-system.md
  // «Обработка 403»); message от apiRequest не затираем.
  const forbiddenMessage = error instanceof ApiError && error.status === 403 ? error.message : null;

  useEffect(() => {
    if (isError && !isAuthError) {
      toast.error(forbiddenMessage ?? 'Не удалось выполнить запрос. Повторите попытку');
    }
  }, [isError, isAuthError, forbiddenMessage]);

  const keys = useMemo(() => data?.items ?? [], [data?.items]);

  // Группировка плоского списка по provider; внутри группы — сортировка по position
  // (стабильная, сохраняет тай-брейк из GET). 08-design-system.md, 04-api.md.
  const grouped = useMemo(() => {
    const map: Record<AiProvider, AiKey[]> = { openai: [], anthropic: [] };
    for (const k of keys) map[k.provider].push(k);
    for (const p of PROVIDER_ORDER) map[p].sort((a, b) => a.position - b.position);
    return map;
  }, [keys]);

  const isEmpty = !isLoading && !isError && keys.length === 0;

  const openAdd = (provider?: AiProvider) => {
    setAddProvider(provider);
    setAddOpen(true);
  };

  return (
    <>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">ИИ - ключи</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            {isLoading ? 'Загрузка…' : `${keys.length} ${pluralKeys(keys.length)} под мониторингом`}
          </p>
        </div>
        {/* Правая зона действий (08-design-system.md, ADR-046 §2б): вторичное «Обновить»
            левее, primary «Добавить» — крайнее справа (гейт `ai-keys:create`). */}
        <div className="flex items-center gap-2">
          {!isLoading && !isError && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void refetch()}
              loading={isFetching}
              aria-label="Обновить список"
            >
              <RefreshCw className="h-4 w-4" />
              Обновить
            </Button>
          )}
          {canCreate && (
            <Button size="sm" onClick={() => openAdd(undefined)}>
              <Plus className="h-4 w-4" />
              Добавить
            </Button>
          )}
        </div>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <AiKeyCardSkeleton key={i} />
          ))}
        </div>
      )}

      {isError && forbiddenMessage && <InsufficientPermissions />}

      {isError && !isAuthError && !forbiddenMessage && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">Не удалось загрузить ключи</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Проверьте соединение с сервером и попробуйте снова.
            </p>
          </div>
          <Button variant="outline" onClick={() => void refetch()} loading={isFetching}>
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        </div>
      )}

      {/* Empty: текстовая строка, без карточек-плейсхолдеров (ADR-046 §2б). */}
      {isEmpty && (
        <div className="rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <p className="text-sm font-medium text-text-primary">Ключей пока нет</p>
        </div>
      )}

      {!isLoading && !isError && keys.length > 0 && (
        <div className="flex flex-col gap-8">
          {PROVIDER_ORDER.map((provider) =>
            grouped[provider].length > 0 ? (
              <AiKeySection
                key={provider}
                provider={provider}
                label={PROVIDER_LABEL[provider]}
                keys={grouped[provider]}
                canEdit={canEdit}
                canDelete={canDelete}
              />
            ) : null,
          )}
        </div>
      )}

      <AddAiKeyModal open={addOpen} onOpenChange={setAddOpen} defaultProvider={addProvider} />
    </>
  );
}

interface AiKeySectionProps {
  provider: AiProvider;
  label: string;
  keys: AiKey[];
  canEdit: boolean;
  canDelete: boolean;
}

/**
 * Секция одного провайдера: собственный DndContext + SortableContext (перестановка
 * ТОЛЬКО внутри секции — между провайдерами карточки не перемещаются, 04-api.md,
 * 08-design-system.md). onDragEnd → оптимистичный reorder + PATCH /api/ai-keys/order.
 */
function AiKeySection({ provider, label, keys, canEdit, canDelete }: AiKeySectionProps) {
  const reorderMutation = useReorderAiKeys();
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { delay: 200, tolerance: 5 } }),
  );
  const ids = useMemo(() => keys.map((k) => k.id), [keys]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = ids.indexOf(String(active.id));
    const newIndex = ids.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    reorderMutation.mutate({ provider, ids: arrayMove(ids, oldIndex, newIndex) });
  };

  return (
    <section>
      <div className="mb-4 flex items-baseline gap-2 border-b border-border-subtle pb-2">
        <h2 className="text-base font-semibold text-text-secondary">{label}</h2>
        <span className="text-[13px] text-text-tertiary">
          {keys.length} {pluralKeys(keys.length)}
        </span>
      </div>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          <SortableContext items={ids} strategy={rectSortingStrategy}>
            {keys.map((aiKey) => (
              <SortableItem key={aiKey.id} id={aiKey.id} disabled={!canEdit}>
                <AiKeyCard aiKey={aiKey} canEdit={canEdit} canDelete={canDelete} />
              </SortableItem>
            ))}
          </SortableContext>
        </div>
      </DndContext>
    </section>
  );
}

function pluralKeys(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'ключ';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'ключа';
  return 'ключей';
}
