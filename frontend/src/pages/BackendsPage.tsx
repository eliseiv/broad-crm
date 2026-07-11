import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Plus, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { AddBackendModal } from '@/components/AddBackendModal';
import { BackendCard } from '@/components/BackendCard';
import { BackendCardSkeleton } from '@/components/BackendCardSkeleton';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ApiError } from '@/lib/api';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useBackends } from '@/features/backends/hooks';

const GRID = 'grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3';

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

  // RBAC-гейтинг действий (08-design-system.md «Гейтинг навигации и действий»).
  const canCreate = useCan('backends', 'create');
  const canEdit = useCan('backends', 'edit');
  const canDelete = useCan('backends', 'delete');

  const isAuthError = error instanceof ApiError && error.status === 401;
  // 403 (RBAC): показываем «Недостаточно прав» вместо generic (08-design-system.md
  // «Обработка 403»); message от apiRequest не затираем.
  const forbiddenMessage = error instanceof ApiError && error.status === 403 ? error.message : null;

  useEffect(() => {
    if (isError && !isAuthError) {
      toast.error(forbiddenMessage ?? 'Не удалось выполнить запрос. Повторите попытку');
    }
  }, [isError, isAuthError, forbiddenMessage]);

  const backends = useMemo(() => data?.items ?? [], [data?.items]);
  const isEmpty = !isLoading && !isError && backends.length === 0;

  // Клиентский фильтр по code/name/domain (регистронезависимо, ADR-039) — применяется
  // ДО сортировки (ADR-046 §2а).
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

  // Порядок карточек — стабильная клиентская сортировка по `name` (регистронезависимо,
  // localeCompare ru), tie-break `code` (UNIQUE ⇒ порядок детерминирован). Бэки с
  // одинаковым именем стоят рядом — это заменяет прежние кластеры-контейнеры «Имя · N».
  // DnD убран (ADR-046 §2а): `position`/`PATCH /api/backends/order` UI не использует (TD-054).
  const sorted = useMemo(
    () =>
      [...filtered].sort(
        (a, b) =>
          a.name.localeCompare(b.name, 'ru', { sensitivity: 'base' }) ||
          a.code.localeCompare(b.code, 'ru', { sensitivity: 'base' }),
      ),
    [filtered],
  );
  const noMatches = searchActive && sorted.length === 0;

  return (
    <>
      {/* Шапка: заголовок + правая зона действий; «Добавить» (Plus, primary) — гейт
          `backends:create` (08-design-system.md, ADR-046 §2б). AddBackendCard упразднена. */}
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Бэки</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            {isLoading ? 'Загрузка…' : `${backends.length} бэков под мониторингом`}
          </p>
        </div>
        {canCreate && (
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            Добавить
          </Button>
        )}
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

      {/* Empty: текстовая строка, без карточек-плейсхолдеров (ADR-046 §2б). */}
      {isEmpty && (
        <div className="rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <p className="text-sm font-medium text-text-primary">Бэков пока нет</p>
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
          ) : (
            // Одна плоская сетка карточек — без кластер-контейнеров и без DnD-обёрток;
            // клик по карточке открывает detail немедленно (ADR-046 §2а).
            <div className={GRID}>
              {sorted.map((backend) => (
                <div key={backend.id} className="h-full w-full min-w-0">
                  <BackendCard backend={backend} canEdit={canEdit} canDelete={canDelete} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <AddBackendModal open={addOpen} onOpenChange={setAddOpen} />
    </>
  );
}
