import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServerCard } from '@/components/ServerCard';
import type { BackendRefListResponse, Server } from '@/types/api';

/**
 * Секция «Бэков: N» внизу карточки сервера (ADR-049 §2).
 *
 * Нормы, которые проверяем:
 * - счётчик берётся из `ServerListItem.backend_count` — **дополнительного запроса нет**;
 * - список грузится **ТОЛЬКО по раскрытию** (ленивый `useServerBackends(id, enabled)`):
 *   свёрнутая карточка = 0 запросов, преднагрузка сетки запрещена (был бы N+1);
 * - разведение жестов: триггер — собственный `<button aria-expanded/aria-controls>`, его клик
 *   **не открывает `ServerDetailModal`** и **не инициирует drag** (всплытие погашено);
 * - `backend_count = 0` → строка «Бэков: 0» рендерится, но секция НЕ раскрывается (нет
 *   chevron, нет `role="button"`, нет запроса).
 */

// Ленивый reverse-lookup-хук: спай ловит (serverId, enabled) — так проверяем, что запрос
// уходит ТОЛЬКО при раскрытии секции (enabled=true), а свёрнутая карточка ничего не грузит.
const serverBackendsSpy = vi.hoisted(() => vi.fn());
const backendsData = vi.hoisted(() => ({
  value: undefined as BackendRefListResponse | undefined,
}));

vi.mock('@/features/servers/hooks', async () => {
  const actual = await vi.importActual<typeof import('@/features/servers/hooks')>(
    '@/features/servers/hooks',
  );
  return {
    ...actual,
    useServerStatus: () => ({ data: undefined }),
    useDeleteServer: () => ({ mutate: vi.fn(), isPending: false }),
    useUpdateServer: () => ({ mutate: vi.fn(), isPending: false }),
    useServerBackends: (id: string, enabled: boolean) => {
      serverBackendsSpy(id, enabled);
      return {
        data: enabled ? backendsData.value : undefined,
        isLoading: false,
        isError: false,
        isFetching: false,
        refetch: vi.fn(),
      };
    },
  };
});

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function server(overrides: Partial<Server> = {}): Server {
  return {
    id: 'server-1',
    name: 'Server 01',
    ip: '10.0.0.10',
    ssh_user: 'root',
    exporter_port: 9100,
    provision_status: 'online',
    position: 0,
    backend_count: 2,
    online: true,
    uptime_seconds: 1323120,
    last_updated: '2026-06-28T12:00:00Z',
    metrics: null,
    ...overrides,
  };
}

/** Был ли ленивый хук вызван с `enabled=true` (т.е. ушёл ли запрос списка бэков). */
function requestedBackends(): boolean {
  return serverBackendsSpy.mock.calls.some(([, enabled]) => enabled === true);
}

describe('ServerCard — секция «Бэков: N» на карточке (ADR-049 §2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    backendsData.value = {
      backends: [
        { code: 'api-eu', name: 'API EU', domain: 'https://eu.example/' },
        { code: 'web', name: 'Web', domain: 'https://web.example/' },
      ],
    };
  });

  it('счётчик «Бэков: N» из backend_count; свёрнутая карточка не делает НИ ОДНОГО запроса', () => {
    render(<ServerCard server={server({ backend_count: 2 })} />, { wrapper });

    expect(screen.getByText('Бэков: 2')).toBeInTheDocument();
    // Ленивый хук вызван, но с enabled=false — списка бэков не запрашивали.
    expect(serverBackendsSpy).toHaveBeenCalledWith('server-1', false);
    expect(requestedBackends()).toBe(false);
    // Строк списка в DOM нет.
    expect(screen.queryByText('api-eu')).not.toBeInTheDocument();
  });

  it('раскрытие грузит список Код/Название/Домен (запрос уходит только сейчас)', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server({ backend_count: 2 })} />, { wrapper });

    const trigger = screen.getByRole('button', { name: /Бэки/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(requestedBackends()).toBe(false);

    await user.click(trigger);

    expect(screen.getByRole('button', { name: /Бэки/ })).toHaveAttribute('aria-expanded', 'true');
    expect(requestedBackends()).toBe(true);
    expect(screen.getByText('api-eu')).toBeInTheDocument();
    expect(screen.getByText('API EU')).toBeInTheDocument();
    expect(screen.getByText('https://eu.example/')).toBeInTheDocument();
    expect(screen.getByText('web')).toBeInTheDocument();
  });

  it('клик по триггеру «Бэков: N» НЕ открывает ServerDetailModal (жесты разведены)', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server({ backend_count: 2 })} />, { wrapper });

    await user.click(screen.getByRole('button', { name: /Бэки/ }));

    // Всплытие погашено: detail-модалка сервера не открылась.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('Просмотр')).not.toBeInTheDocument();
    // Секция при этом раскрыта (клик сработал по назначению).
    expect(screen.getByRole('button', { name: /Бэки/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('клик по строке раскрытого списка бэков тоже не открывает detail-модалку', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server({ backend_count: 2 })} />, { wrapper });

    await user.click(screen.getByRole('button', { name: /Бэки/ }));
    await user.click(screen.getByText('api-eu'));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('pointerdown на триггере не всплывает к карточке (DnD-сенсор не захватывает подэлемент)', async () => {
    const user = userEvent.setup();
    const onCardPointerDown = vi.fn();
    // Обёртка играет роль SortableItem: @dnd-kit вешает listeners на неё. Если бы триггер
    // не гасил всплытие, нажатие на него инициировало бы перетаскивание карточки.
    render(
      <div onPointerDown={onCardPointerDown}>
        <ServerCard server={server({ backend_count: 2 })} />
      </div>,
      { wrapper },
    );

    await user.click(screen.getByRole('button', { name: /Бэки/ }));

    expect(onCardPointerDown).not.toHaveBeenCalled();
  });

  it('backend_count = 0 → «Бэков: 0» рендерится, но секция НЕ раскрывается и не грузит список', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server({ backend_count: 0 })} />, { wrapper });

    expect(screen.getByText('Бэков: 0')).toBeInTheDocument();
    // Нет триггера-кнопки (пустого аккордеона не заводим).
    expect(screen.queryByRole('button', { name: /Бэки/ })).not.toBeInTheDocument();
    expect(requestedBackends()).toBe(false);

    // Клик по строке счётчика ничего не раскрывает и не открывает detail-модалку.
    await user.click(screen.getByText('Бэков: 0'));

    expect(screen.queryByText('Бэков нет')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(requestedBackends()).toBe(false);
  });

  it('пустой ответ раскрытой секции → «Бэков нет»', async () => {
    const user = userEvent.setup();
    backendsData.value = { backends: [] };
    render(<ServerCard server={server({ backend_count: 2 })} />, { wrapper });

    await user.click(screen.getByRole('button', { name: /Бэки/ }));

    expect(screen.getByText('Бэков нет')).toBeInTheDocument();
  });
});
