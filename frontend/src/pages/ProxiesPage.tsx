import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import { arrayMove, rectSortingStrategy, SortableContext } from '@dnd-kit/sortable';
import { AddProxyCard } from '@/components/AddProxyCard';
import { AddProxyModal } from '@/components/AddProxyModal';
import { ProxyCard } from '@/components/ProxyCard';
import { ProxyCardSkeleton } from '@/components/ProxyCardSkeleton';
import { SortableItem } from '@/components/SortableItem';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { useProxies, useReorderProxies } from '@/features/proxies/hooks';

export function ProxiesPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useProxies();
  const [addOpen, setAddOpen] = useState(false);
  const reorderMutation = useReorderProxies();

  // PointerSensor: короткий клик (<200 мс) → edit; зажатие + движение → drag
  // (08-design-system.md, 02-tech-stack.md).
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { delay: 200, tolerance: 5 } }),
  );

  const isAuthError = error instanceof ApiError && error.status === 401;

  useEffect(() => {
    if (isError && !isAuthError) {
      toast.error('Не удалось выполнить запрос. Повторите попытку');
    }
  }, [isError, isAuthError]);

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

      {isError && !isAuthError && (
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

      {isEmpty && (
        <div className="mx-auto max-w-md">
          <AddProxyCard onClick={() => setAddOpen(true)} />
          <div className="mt-4 text-center">
            <p className="text-sm font-medium text-text-primary">Пока нет прокси</p>
            <p className="mt-1 text-[13px] text-text-secondary">Добавьте первый прокси</p>
          </div>
        </div>
      )}

      {!isLoading && !isError && proxies.length > 0 && (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            <SortableContext items={proxyIds} strategy={rectSortingStrategy}>
              {proxies.map((proxy) => (
                <SortableItem key={proxy.id} id={proxy.id}>
                  <ProxyCard proxy={proxy} />
                </SortableItem>
              ))}
            </SortableContext>
            <AddProxyCard onClick={() => setAddOpen(true)} />
          </div>
        </DndContext>
      )}

      <AddProxyModal open={addOpen} onOpenChange={setAddOpen} />
    </>
  );
}
