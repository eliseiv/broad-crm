import { useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { AddServerCard } from '@/components/AddServerCard';
import { AddServerModal } from '@/components/AddServerModal';
import { ServerCard } from '@/components/ServerCard';
import { ServerCardSkeleton } from '@/components/ServerCardSkeleton';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { useServers } from '@/features/servers/hooks';

export function ServersPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useServers();
  const [addOpen, setAddOpen] = useState(false);

  const isAuthError = error instanceof ApiError && error.status === 401;

  useEffect(() => {
    if (isError && !isAuthError) {
      toast.error('Не удалось выполнить запрос. Повторите попытку');
    }
  }, [isError, isAuthError]);

  const servers = data?.items ?? [];
  const isEmpty = !isLoading && !isError && servers.length === 0;

  return (
    <>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Серверы</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            {isLoading
              ? 'Загрузка…'
              : `${servers.length} ${pluralServers(servers.length)} под мониторингом`}
          </p>
        </div>
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
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <ServerCardSkeleton key={i} />
          ))}
        </div>
      )}

      {isError && !isAuthError && (
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

      {isEmpty && (
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

      {!isLoading && !isError && servers.length > 0 && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {servers.map((server) => (
            <ServerCard key={server.id} server={server} />
          ))}
          <AddServerCard onClick={() => setAddOpen(true)} />
        </div>
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
