import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import { arrayMove, rectSortingStrategy, SortableContext } from '@dnd-kit/sortable';
import { AddBackendCard } from '@/components/AddBackendCard';
import { AddBackendModal } from '@/components/AddBackendModal';
import { BackendCard } from '@/components/BackendCard';
import { BackendCardSkeleton } from '@/components/BackendCardSkeleton';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { SortableItem } from '@/components/SortableItem';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ApiError } from '@/lib/api';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useBackends, useReorderBackends } from '@/features/backends/hooks';
import type { Backend } from '@/types/api';

const GRID = 'grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3';

/**
 * Презентационный блок страницы: кластер бэков с одинаковым `name` (≥2) ИЛИ ряд
 * одиночных карточек (уникальные `name`). Формируется поверх `position`-порядка (ADR-039).
 */
type Block =
  | { kind: 'group'; name: string; items: Backend[] }
  | { kind: 'singles'; items: Backend[] };

/**
 * Клиентская группировка по точному (case-sensitive) совпадению `name` над уже
 * отсортированным по `position` списком (ADR-039). Группа (≥2) появляется на месте своего
 * первого по порядку члена; одиночные `name` — обычные карточки. Соседние одиночные
 * карточки собираются в один grid-ряд (сохраняя взаимный порядок), кластеры — отдельными
 * контейнерами между ними.
 */
function buildBlocks(backends: Backend[]): Block[] {
  const byName = new Map<string, Backend[]>();
  for (const b of backends) {
    const list = byName.get(b.name);
    if (list) list.push(b);
    else byName.set(b.name, [b]);
  }

  const blocks: Block[] = [];
  const seen = new Set<string>();
  let singles: Backend[] = [];
  const flushSingles = () => {
    if (singles.length > 0) {
      blocks.push({ kind: 'singles', items: singles });
      singles = [];
    }
  };

  for (const b of backends) {
    if (seen.has(b.name)) continue;
    seen.add(b.name);
    const items = byName.get(b.name)!;
    if (items.length >= 2) {
      flushSingles();
      blocks.push({ kind: 'group', name: b.name, items });
    } else {
      singles.push(b);
    }
  }
  flushSingles();
  return blocks;
}

export function BackendsPage() {
  // Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
  // прямой URL/навигация без `backends:view` → заглушка «Недостаточно прав»
  // (page-scoped), а не контент. Супер-админ/admin — всегда доступ; список не
  // запрашивается без права.
  const canView = useCanViewPage('backends');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <BackendsList />;
}

function BackendsList() {
  const { data, isLoading, isError, error, refetch, isFetching } = useBackends();
  const [addOpen, setAddOpen] = useState(false);
  const [search, setSearch] = useState('');
  const reorderMutation = useReorderBackends();

  // RBAC-гейтинг действий (08-design-system.md «Гейтинг навигации и действий»).
  const canCreate = useCan('backends', 'create');
  const canEdit = useCan('backends', 'edit');
  const canDelete = useCan('backends', 'delete');

  // PointerSensor: короткий клик (<200 мс) → detail; зажатие + движение → drag
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
  const backends = useMemo(
    () => [...(data?.items ?? [])].sort((a, b) => a.position - b.position),
    [data?.items],
  );
  const backendIds = useMemo(() => backends.map((b) => b.id), [backends]);
  const isEmpty = !isLoading && !isError && backends.length === 0;

  // Клиентский фильтр по code/name/domain (регистронезависимо, ADR-039).
  const query = search.trim().toLowerCase();
  const searchActive = query.length > 0;
  const filtered = useMemo(() => {
    if (!searchActive) return backends;
    return backends.filter(
      (b) =>
        b.code.toLowerCase().includes(query) ||
        b.name.toLowerCase().includes(query) ||
        b.domain.toLowerCase().includes(query),
    );
  }, [backends, query, searchActive]);

  const blocks = useMemo(() => buildBlocks(filtered), [filtered]);
  const noMatches = searchActive && filtered.length === 0;

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = backendIds.indexOf(String(active.id));
    const newIndex = backendIds.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;
    reorderMutation.mutate(arrayMove(backendIds, oldIndex, newIndex));
  };

  // Карточка внутри блока: перестановка активна только вне поиска (ADR-039 — при
  // активном фильтре drag не выполняется; порядок применяется к полному списку). В
  // режиме поиска рендерим без SortableItem (нет DndContext-обёртки).
  const renderCard = (backend: Backend) =>
    searchActive ? (
      <div key={backend.id} className="h-full w-full min-w-0">
        <BackendCard backend={backend} canEdit={canEdit} canDelete={canDelete} />
      </div>
    ) : (
      <SortableItem key={backend.id} id={backend.id} disabled={!canEdit}>
        <BackendCard backend={backend} canEdit={canEdit} canDelete={canDelete} />
      </SortableItem>
    );

  const renderBlock = (block: Block, index: number) => {
    if (block.kind === 'group') {
      return (
        <div
          key={`group-${block.name}-${index}`}
          className="rounded-card border border-border-subtle bg-surface-2 p-4"
        >
          <h3 className="mb-3 text-sm font-semibold text-text-primary">
            {block.name} · {block.items.length}
          </h3>
          <div className={GRID}>{block.items.map(renderCard)}</div>
        </div>
      );
    }
    return (
      <div key={`singles-${index}`} className={GRID}>
        {block.items.map(renderCard)}
      </div>
    );
  };

  const content = (
    <div className="flex flex-col gap-6">
      {blocks.map(renderBlock)}
      {/* AddBackendCard — вне групп, в конце; при активном поиске скрыта (ADR-039). */}
      {canCreate && !searchActive && (
        <div className={GRID}>
          <AddBackendCard onClick={() => setAddOpen(true)} />
        </div>
      )}
    </div>
  );

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Бэки</h1>
        <p className="mt-1 text-[13px] text-text-secondary">
          {isLoading ? 'Загрузка…' : `${backends.length} бэков под мониторингом`}
        </p>
      </div>

      {isLoading && (
        <div className={GRID}>
          {[0, 1, 2].map((i) => (
            <BackendCardSkeleton key={i} />
          ))}
        </div>
      )}

      {isError && forbiddenMessage && <InsufficientPermissions />}

      {isError && !isAuthError && !forbiddenMessage && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">Не удалось загрузить бэки</p>
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

      {/* Empty (нет бэков, есть create) — ТОЛЬКО карточка добавления, без текста (ADR-039). */}
      {isEmpty && canCreate && (
        <div className="mx-auto max-w-md">
          <AddBackendCard onClick={() => setAddOpen(true)} />
        </div>
      )}

      {isEmpty && !canCreate && (
        <div className="mx-auto max-w-md rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <p className="text-sm font-medium text-text-primary">Список бэков пуст</p>
        </div>
      )}

      {!isLoading && !isError && backends.length > 0 && (
        <div className="flex flex-col gap-4">
          <div className="w-64">
            <Input
              aria-label="Поиск по бэкам"
              placeholder="Поиск по бэкам…"
              value={search}
              trailing={<Search className="h-4 w-4 text-text-tertiary" aria-hidden="true" />}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {noMatches ? (
            <div className="rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
              <p className="text-sm font-medium text-text-primary">Ничего не найдено</p>
            </div>
          ) : searchActive ? (
            // При активном поиске drag не выполняется — рендер без DndContext (ADR-039).
            content
          ) : (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext items={backendIds} strategy={rectSortingStrategy}>
                {content}
              </SortableContext>
            </DndContext>
          )}
        </div>
      )}

      <AddBackendModal open={addOpen} onOpenChange={setAddOpen} />
    </>
  );
}
