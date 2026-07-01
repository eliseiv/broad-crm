import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServerCard } from '@/components/ServerCard';
import type { Server, ServerMetrics } from '@/types/api';

const hooks = vi.hoisted(() => ({
  deleteMutate: vi.fn(),
  statusData: undefined as
    | { provision_status: 'installing'; error_message: string | null }
    | undefined,
}));

vi.mock('@/features/servers/hooks', async () => {
  const actual = await vi.importActual<typeof import('@/features/servers/hooks')>(
    '@/features/servers/hooks',
  );
  return {
    ...actual,
    useServerStatus: () => ({ data: hooks.statusData }),
    useDeleteServer: () => ({ mutate: hooks.deleteMutate, isPending: false }),
  };
});

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function server(overrides: Partial<Server> = {}): Server {
  return {
    id: 'server-1',
    name: 'Server 01',
    ip: '10.0.0.10',
    exporter_port: 9100,
    provision_status: 'online',
    online: true,
    uptime_seconds: 1323120,
    last_updated: '2026-06-28T12:00:00Z',
    metrics: {
      cpu: { usage_percent: 65, zone: 'green', detail: { value: null, total: 8, unit: 'cores' } },
      ram: { usage_percent: 80, zone: 'yellow', detail: { value: 11.5, total: 16, unit: 'GB' } },
      ssd: { usage_percent: 91, zone: 'red', detail: { value: 238, total: 500, unit: 'GB' } },
    },
    ...overrides,
  };
}

function nullMetrics(): ServerMetrics {
  return {
    cpu: { usage_percent: null, zone: null, detail: { value: null, total: null, unit: 'cores' } },
    ram: { usage_percent: null, zone: null, detail: { value: null, total: null, unit: 'GB' } },
    ssd: { usage_percent: null, zone: null, detail: { value: null, total: null, unit: 'GB' } },
  };
}

describe('ServerCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.statusData = undefined;
  });

  it('renders online server metrics, uptime and delete confirmation', async () => {
    const user = userEvent.setup();
    render(<ServerCard server={server()} />, { wrapper });

    expect(screen.getByText('В сети')).toBeInTheDocument();
    expect(screen.getByText('Аптайм:')).toBeInTheDocument();
    expect(screen.getByText('15д 7ч 32м')).toBeInTheDocument();
    expect(screen.getByText(/Обновлено:/)).toBeInTheDocument();
    expect(screen.getByText('8 ядер')).toBeInTheDocument();

    await user.click(screen.getByLabelText('Удалить сервер Server 01'));
    await user.click(screen.getByRole('button', { name: 'Удалить' }));

    expect(hooks.deleteMutate).toHaveBeenCalledWith('server-1', expect.any(Object));
  });

  it('renders provisioning and error states', () => {
    const { rerender } = render(<ServerCard server={server({ provision_status: 'pending' })} />, {
      wrapper,
    });

    expect(screen.getByText('Ожидание')).toBeInTheDocument();
    expect(screen.getByText('Ожидание установки…')).toBeInTheDocument();

    hooks.statusData = { provision_status: 'installing', error_message: null };
    rerender(<ServerCard server={server({ provision_status: 'pending' })} />);
    expect(screen.getByText('Установка…')).toBeInTheDocument();
    expect(screen.getByText('Установка агента…')).toBeInTheDocument();

    hooks.statusData = undefined;
    rerender(
      <ServerCard
        server={server({
          provision_status: 'error',
          online: false,
          metrics: null,
        })}
      />,
    );
    expect(screen.getByText('Ошибка')).toBeInTheDocument();
    expect(screen.getByText('Ошибка установки агента')).toBeInTheDocument();
  });

  it('renders offline placeholders when metrics are null', () => {
    render(
      <ServerCard
        server={server({
          online: false,
          last_updated: '2026-06-28T12:00:00Z',
          metrics: null,
        })}
      />,
      { wrapper },
    );

    expect(screen.getByText('Не в сети')).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: /Загрузка .* недоступна/ })).toHaveLength(3);
    expect(screen.getAllByText('—')).toHaveLength(6);
    expect(screen.getByText(/Не в сети\. Обновлено:/)).toBeInTheDocument();
  });

  it('does not render a Grafana link/icon even when VITE_GRAFANA_URL is set', () => {
    vi.stubEnv('VITE_GRAFANA_URL', 'https://grafana.example.com');
    try {
      render(<ServerCard server={server()} />, { wrapper });

      // Ссылки на Grafana в DOM нет ни в каком состоянии.
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Открыть дашборд Grafana')).not.toBeInTheDocument();
      expect(screen.queryByText(/grafana/i)).not.toBeInTheDocument();
      // Кнопка удаления при этом на месте.
      expect(screen.getByLabelText('Удалить сервер Server 01')).toBeInTheDocument();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it('renders offline placeholders when metric values are nullable', () => {
    render(
      <ServerCard
        server={server({
          online: false,
          last_updated: '2026-06-28T12:00:00Z',
          metrics: nullMetrics(),
        })}
      />,
      { wrapper },
    );

    expect(screen.getByText('Не в сети')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Загрузка CPU недоступна' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Загрузка RAM недоступна' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Загрузка SSD недоступна' })).toBeInTheDocument();
    expect(screen.getAllByText('—')).toHaveLength(6);
    expect(screen.getByText(/Не в сети\. Обновлено:/)).toBeInTheDocument();
  });
});
