import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import { arrayMove, rectSortingStrategy, SortableContext } from '@dnd-kit/sortable';
import { AddServerCard } from '@/components/AddServerCard';
import { AddServerModal } from '@/components/AddServerModal';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { ServerCard } from '@/components/ServerCard';
import { ServerCardSkeleton } from '@/components/ServerCardSkeleton';
import { SortableItem } from '@/components/SortableItem';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useReorderServers, useServers } from '@/features/servers/hooks';

export function ServersPage() {
  // Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
  // прямой URL/навигация без `servers:view` → заглушка «Недостаточно прав»
  // (page-scoped), а не контент. Супер-админ/admin — всегда доступ; список не
  // запрашивается без права.
  const canView = useCanViewPage('servers');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <ServersList />;
}

function ServersList() {
  const { data, isLoading, isError, error, refetch, isFetching } = useServers();
  const [addOpen, setAddOpen] = useState(false);
  const reorderMutation = useReorderServers();

  // RBAC-гейтинг действий (08-design-system.md «Гейтинг навигации и действий»).
  const canCreate = useCan('servers', 'create');
  const canEdit = useCan('servers', 'edit');
  const canDelete = useCan('servers', 'delete');

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
  const servers = useMemo(
    () => [...(data?.items ?? [])].sort((a, b) => a.position - b.position),
    [data?.items],
  );
  const serverIds = useMemo(() => servers.map((s) => s.id), [servers]);
  const isEmpty = !isLoading && !isError && servers.length === 0;

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = serverIds.indexOf(String(active.id));
    const newIndex = serverIds.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    reorderMutation.mutate(arrayMove(serverIds, oldIndex, newIndex));
  };

  return (
    <>
      {/*
        Без ручной кнопки «Обновить» (08-design-system.md «Страница Серверы», ADR-013
        смежная правка): данные обновляются штатным polling/refetch TanStack Query.
      */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Серверы</h1>
        <p className="mt-1 text-[13px] text-text-secondary">
          {isLoading
            ? 'Загрузка…'
            : `${servers.length} ${pluralServers(servers.length)} под мониторингом`}
        </p>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <ServerCardSkeleton key={i} />
          ))}
        </div>
      )}

      {isError && forbiddenMessage && <InsufficientPermissions />}

      {isError && !isAuthError && !forbiddenMessage && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">
              Не удалось загрузить серверы
            </p>
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
          <AddServerCard onClick={() => setAddOpen(true)} />
          <div className="mt-4 text-center">
            <p className="text-sm font-medium text-text-primary">Пока нет серверов</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Добавьте первый сервер, чтобы начать мониторинг
            </p>
          </div>
        </div>
      )}

      {isEmpty && !canCreate && (
        <div className="mx-auto max-w-md rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <p className="text-sm font-medium text-text-primary">Список серверов пуст</p>
        </div>
      )}

      {!isLoading && !isError && servers.length > 0 && (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
            <SortableContext items={serverIds} strategy={rectSortingStrategy}>
              {servers.map((server) => (
                <SortableItem key={server.id} id={server.id} disabled={!canEdit}>
                  <ServerCard server={server} canEdit={canEdit} canDelete={canDelete} />
                </SortableItem>
              ))}
            </SortableContext>
            {canCreate && <AddServerCard onClick={() => setAddOpen(true)} />}
          </div>
        </DndContext>
      )}

      <AddServerModal open={addOpen} onOpenChange={setAddOpen} />
    </>
  );
}

function pluralServers(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'сервер';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'сервера';
  return 'серверов';
}
