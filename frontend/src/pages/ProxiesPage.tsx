import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import { arrayMove, rectSortingStrategy, SortableContext } from '@dnd-kit/sortable';
import { AddProxyCard } from '@/components/AddProxyCard';
import { AddProxyModal } from '@/components/AddProxyModal';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { ProxyCard } from '@/components/ProxyCard';
import { ProxyCardSkeleton } from '@/components/ProxyCardSkeleton';
import { SortableItem } from '@/components/SortableItem';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useProxies, useReorderProxies } from '@/features/proxies/hooks';

export function ProxiesPage() {
  // Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
  // прямой URL/навигация без `proxies:view` → заглушка «Недостаточно прав»
  // (page-scoped), а не контент. Супер-админ/admin — всегда доступ; список не
  // запрашивается без права.
  const canView = useCanViewPage('proxies');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <ProxiesList />;
}

function ProxiesList() {
  const { data, isLoading, isError, error, refetch, isFetching } = useProxies();
  const [addOpen, setAddOpen] = useState(false);
  const reorderMutation = useReorderProxies();

  // RBAC-гейтинг действий (08-design-system.md «Гейтинг навигации и действий»).
  const canCreate = useCan('proxies', 'create');
  const canEdit = useCan('proxies', 'edit');
  const canDelete = useCan('proxies', 'delete');

  // PointerSensor: короткий клик (<200 мс) → edit; зажатие + движение → drag
  // (08-design-system.md, 02-tech-stack.md).
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { delay: 200, tolerance: 5 } }),
  );

  const isAuthError = error instanceof ApiError && error.status === 401;
  // 403 (RBAC): показываем «Недостаточно прав» вместо generic (08-design-system.md
  // «Обработка 403»); message от apiRequest не затираем.
  const forbiddenMessage = error instanceof ApiError && error.status === 403 ? error.message : null;

  useEffect(() => {
    if (isError && !isAuthError) {
      toast.error(forbiddenMessage ?? 'Не удалось выполнить запрос. Повторите попытку');
    }
  }, [isError, isAuthError, forbiddenMessage]);

  // Порядок отрисовки — по position (стабильная сортировка сохраняет тай-брейк
  // из ответа GET, 04-api.md: position ASC, created_at DESC, id).
  const proxies = useMemo(
    () => [...(data?.items ?? [])].sort((a, b) => a.position - b.position),
    [data?.items],
  );
  const proxyIds = useMemo(() => proxies.map((p) => p.id), [proxies]);
  const isEmpty = !isLoading && !isError && proxies.length === 0;

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = proxyIds.indexOf(String(active.id));
    const newIndex = proxyIds.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    reorderMutation.mutate(arrayMove(proxyIds, oldIndex, newIndex));
  };

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Прокси</h1>
        <p className="mt-1 text-[13px] text-text-secondary">
          {isLoading ? 'Загрузка…' : `${proxies.length} прокси под мониторингом`}
        </p>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <ProxyCardSkeleton key={i} />
          ))}
        </div>
      )}

      {isError && forbiddenMessage && <InsufficientPermissions />}

      {isError && !isAuthError && !forbiddenMessage && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">Не удалось загрузить прокси</p>
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

      {isEmpty && canCreate && (
        <div className="mx-auto max-w-md">
          <AddProxyCard onClick={() => setAddOpen(true)} />
          <div className="mt-4 text-center">
            <p className="text-sm font-medium text-text-primary">Пока нет прокси</p>
            <p className="mt-1 text-[13px] text-text-secondary">Добавьте первый прокси</p>
          </div>
        </div>
      )}

      {isEmpty && !canCreate && (
        <div className="mx-auto max-w-md rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <p className="text-sm font-medium text-text-primary">Список прокси пуст</p>
        </div>
      )}

      {!isLoading && !isError && proxies.length > 0 && (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            <SortableContext items={proxyIds} strategy={rectSortingStrategy}>
              {proxies.map((proxy) => (
                <SortableItem key={proxy.id} id={proxy.id} disabled={!canEdit}>
                  <ProxyCard proxy={proxy} canEdit={canEdit} canDelete={canDelete} />
                </SortableItem>
              ))}
            </SortableContext>
            {canCreate && <AddProxyCard onClick={() => setAddOpen(true)} />}
          </div>
        </DndContext>
      )}

      <AddProxyModal open={addOpen} onOpenChange={setAddOpen} />
    </>
  );
}
