import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServerDetailModal } from '@/components/ServerDetailModal';
import { ProxyDetailModal } from '@/components/ProxyDetailModal';
import { AiKeyDetailModal } from '@/components/AiKeyDetailModal';
import { BackendDetailModal } from '@/components/BackendDetailModal';
import type { AiKey, Backend, Proxy, Server } from '@/types/api';

/**
 * Read-only detail-модалки карточных страниц (ADR-035/ADR-039/ADR-040). Reveal-функции
 * секрета замоканы на уровне feature-api — сеть НЕ трогаем; секрет живёт ТОЛЬКО в локальном
 * стейте SecretRevealField. Ленивый reverse-lookup «Бэки» и мутация inline-edit замоканы на
 * уровне feature-hooks → модалки рендерятся БЕЗ QueryClientProvider (доказывает, что reveal и
 * reverse-lookup идут прямыми вызовами/ленивым хуком, а не через общий react-query-кэш).
 */

const serversApi = vi.hoisted(() => ({ revealServerPassword: vi.fn() }));
const proxiesApi = vi.hoisted(() => ({ revealProxyPassword: vi.fn() }));
const aiKeysApi = vi.hoisted(() => ({ revealAiKeyValue: vi.fn() }));
const backendsApi = vi.hoisted(() => ({
  revealBackendApiKey: vi.fn(),
  revealBackendAdminApiKey: vi.fn(),
}));
const serverHooks = vi.hoisted(() => ({ updateMutate: vi.fn() }));

const lazyBackendsQuery = () => ({
  data: undefined,
  isLoading: false,
  isError: false,
  isFetching: false,
  refetch: vi.fn(),
});

vi.mock('@/features/servers/api', () => serversApi);
vi.mock('@/features/proxies/api', () => proxiesApi);
vi.mock('@/features/ai-keys/api', () => aiKeysApi);
vi.mock('@/features/backends/api', () => backendsApi);
vi.mock('@/features/servers/hooks', () => ({
  useUpdateServer: () => ({ mutate: serverHooks.updateMutate, isPending: false }),
  useServerBackends: () => lazyBackendsQuery(),
}));
vi.mock('@/features/ai-keys/hooks', () => ({
  useAiKeyBackends: () => lazyBackendsQuery(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const MASK = '••••••••';

function makeServer(over: Partial<Server> = {}): Server {
  return {
    id: 'srv-1',
    name: 'Server 01',
    ip: '10.0.0.10',
    ssh_user: 'root',
    exporter_port: 9100,
    provision_status: 'online',
    position: 0,
    backend_count: 0,
    online: true,
    uptime_seconds: null,
    last_updated: null,
    metrics: null,
    ...over,
  };
}

function makeProxy(over: Partial<Proxy> = {}): Proxy {
  return {
    id: 'px-1',
    name: 'DE Residential',
    proxy_type: 'socks5',
    host: 'proxy.example.com',
    port: 1080,
    username: 'user01',
    has_password: true,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: null,
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...over,
  };
}

function makeKey(over: Partial<AiKey> = {}): AiKey {
  return {
    id: 'key-1',
    name: 'OpenAI Prod',
    provider: 'openai',
    key_masked: 'sk-p…bA3T',
    check_status: 'working',
    error_message: null,
    position: 0,
    backend_count: 0,
    last_checked_at: null,
    created_at: '2026-07-01T09:00:00Z',
    ...over,
  };
}

function makeBackend(over: Partial<Backend> = {}): Backend {
  return {
    id: 'backend-1',
    code: 'api-eu',
    name: 'API EU',
    domain: 'https://api.example.com/',
    server_id: null,
    server_name: null,
    ai_key_id: null,
    ai_key_name: null,
    has_api_key: false,
    has_admin_api_key: false,
    git: null,
    note: null,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: null,
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...over,
  };
}

beforeEach(() => vi.clearAllMocks());

describe('ServerDetailModal (ADR-039 inline-edit)', () => {
  it('рендерит detail-поля; карандаш под servers:edit открывает inline-edit имени', async () => {
    const user = userEvent.setup();
    render(<ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit />);

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('Название')).toBeInTheDocument();
    expect(dialog.getByText('Server 01')).toBeInTheDocument();
    expect(dialog.getByText('10.0.0.10')).toBeInTheDocument();
    expect(dialog.getByText('Пользователь')).toBeInTheDocument();
    expect(dialog.getByText('root')).toBeInTheDocument();

    // Карандаш открывает INLINE-редактирование прямо в detail-view (не отдельную модалку).
    await user.click(dialog.getByRole('button', { name: 'Редактировать' }));
    const nameInput = dialog.getByLabelText('Название') as HTMLInputElement;
    expect(nameInput.value).toBe('Server 01');
    expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Отмена' })).toBeInTheDocument();
  });

  it('inline-save шлёт PATCH { name } с новым именем', async () => {
    const user = userEvent.setup();
    render(<ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit />);

    const dialog = within(screen.getByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Редактировать' }));
    const nameInput = dialog.getByLabelText('Название') as HTMLInputElement;
    await user.clear(nameInput);
    await user.type(nameInput, 'Server 01 renamed');
    await user.click(dialog.getByRole('button', { name: 'Сохранить' }));

    expect(serverHooks.updateMutate).toHaveBeenCalledTimes(1);
    expect(serverHooks.updateMutate.mock.calls[0][0]).toEqual({ name: 'Server 01 renamed' });
  });

  it('«Отмена» закрывает inline-edit без запроса и возвращает read-only строку', async () => {
    const user = userEvent.setup();
    render(<ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit />);

    const dialog = within(screen.getByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Редактировать' }));
    await user.click(dialog.getByRole('button', { name: 'Отмена' }));

    expect(dialog.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
    expect(dialog.getByText('Server 01')).toBeInTheDocument();
    expect(serverHooks.updateMutate).not.toHaveBeenCalled();
  });

  it('reveal пароля вызывает api-функцию on-demand; значение только после клика', async () => {
    const user = userEvent.setup();
    serversApi.revealServerPassword.mockResolvedValue({ value: 'ssh-plE1n' });
    render(<ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit />);

    // До клика секрет не запрашивается.
    expect(serversApi.revealServerPassword).not.toHaveBeenCalled();
    expect(screen.getByText(MASK)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать пароль' }));

    expect(serversApi.revealServerPassword).toHaveBeenCalledTimes(1);
    expect(serversApi.revealServerPassword.mock.calls[0][0]).toBe('srv-1');
    expect(await screen.findByText('ssh-plE1n')).toBeInTheDocument();
  });

  it('canEdit=false → карандаша нет, пароль — статичная маска без глаза', () => {
    render(<ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit={false} />);

    expect(screen.queryByRole('button', { name: 'Редактировать' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Показать пароль' })).not.toBeInTheDocument();
    expect(screen.getByText(MASK)).toBeInTheDocument();
  });

  it('свёрнутая секция «Бэки» показывает счётчик backend_count без запроса', () => {
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ backend_count: 3 })}
        canEdit
      />,
    );

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('Бэков: 3')).toBeInTheDocument();
  });
});

describe('ProxyDetailModal (ADR-035)', () => {
  it('рендерит поля и reveal пароля при has_password', async () => {
    const user = userEvent.setup();
    proxiesApi.revealProxyPassword.mockResolvedValue({ value: 'proxy-secr3t' });
    render(
      <ProxyDetailModal open onOpenChange={vi.fn()} proxy={makeProxy()} canEdit onEdit={vi.fn()} />,
    );

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('SOCKS5')).toBeInTheDocument();
    expect(dialog.getByText('proxy.example.com')).toBeInTheDocument();
    expect(dialog.getByText('1080')).toBeInTheDocument();
    expect(dialog.getByText('user01')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать пароль' }));
    expect(proxiesApi.revealProxyPassword.mock.calls[0][0]).toBe('px-1');
    expect(await screen.findByText('proxy-secr3t')).toBeInTheDocument();
  });

  it('has_password=false → «Пароль: —» без кнопки-глаза', () => {
    render(
      <ProxyDetailModal
        open
        onOpenChange={vi.fn()}
        proxy={makeProxy({ has_password: false })}
        canEdit
        onEdit={vi.fn()}
      />,
    );

    expect(screen.queryByRole('button', { name: 'Показать пароль' })).not.toBeInTheDocument();
    // Есть строка «Пароль» со значением-прочерком.
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('Пароль')).toBeInTheDocument();
  });
});

describe('AiKeyDetailModal (ADR-035/ADR-040)', () => {
  it('поле «Ключ» показывает key_masked; reveal раскрывает полный ключ', async () => {
    const user = userEvent.setup();
    aiKeysApi.revealAiKeyValue.mockResolvedValue({ value: 'sk-proj-FULL-VALUE' });
    render(
      <AiKeyDetailModal open onOpenChange={vi.fn()} aiKey={makeKey()} canEdit onEdit={vi.fn()} />,
    );

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('OpenAI')).toBeInTheDocument();
    // Маска = key_masked (полный ключ скрыт до reveal).
    expect(dialog.getByText('sk-p…bA3T')).toBeInTheDocument();
    expect(dialog.queryByText('sk-proj-FULL-VALUE')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать ключ' }));
    expect(aiKeysApi.revealAiKeyValue.mock.calls[0][0]).toBe('key-1');
    expect(await screen.findByText('sk-proj-FULL-VALUE')).toBeInTheDocument();
  });

  it('карандаш под ai-keys:edit вызывает onEdit', async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    render(
      <AiKeyDetailModal open onOpenChange={vi.fn()} aiKey={makeKey()} canEdit onEdit={onEdit} />,
    );

    await user.click(screen.getByRole('button', { name: 'Редактировать' }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });
});

describe('BackendDetailModal (ADR-040)', () => {
  it('рендерит Код/Название/Домен/Сервер/ИИ-ключ; без секретов reveal-глаза нет; карандаш → onEdit', async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    render(
      <BackendDetailModal
        open
        onOpenChange={vi.fn()}
        backend={makeBackend()}
        canEdit
        onEdit={onEdit}
      />,
    );

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('Код')).toBeInTheDocument();
    expect(dialog.getByText('api-eu')).toBeInTheDocument();
    expect(dialog.getByText('API EU')).toBeInTheDocument();
    expect(dialog.getByText('https://api.example.com/')).toBeInTheDocument();
    expect(dialog.getByText('Сервер')).toBeInTheDocument();
    expect(dialog.getByText('ИИ-ключ')).toBeInTheDocument();
    // Секреты не заданы (has_*=false) — reveal-глаза нет.
    expect(dialog.queryByRole('button', { name: /Показать/ })).not.toBeInTheDocument();

    await user.click(dialog.getByRole('button', { name: 'Редактировать' }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it('reveal API KEY / ADMIN API KEY бэка идёт прямым вызовом api (не через кэш)', async () => {
    const user = userEvent.setup();
    backendsApi.revealBackendApiKey.mockResolvedValue({ value: 'sk-backend-PLAIN' });
    backendsApi.revealBackendAdminApiKey.mockResolvedValue({ value: 'sk-admin-PLAIN' });
    render(
      <BackendDetailModal
        open
        onOpenChange={vi.fn()}
        backend={makeBackend({ has_api_key: true, has_admin_api_key: true })}
        canEdit
        onEdit={vi.fn()}
      />,
    );

    // До клика секреты не запрашиваются — обе строки под маской.
    expect(backendsApi.revealBackendApiKey).not.toHaveBeenCalled();
    expect(backendsApi.revealBackendAdminApiKey).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Показать API KEY' }));
    expect(backendsApi.revealBackendApiKey).toHaveBeenCalledTimes(1);
    expect(backendsApi.revealBackendApiKey.mock.calls[0][0]).toBe('backend-1');
    expect(await screen.findByText('sk-backend-PLAIN')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать ADMIN API KEY' }));
    expect(backendsApi.revealBackendAdminApiKey).toHaveBeenCalledTimes(1);
    expect(backendsApi.revealBackendAdminApiKey.mock.calls[0][0]).toBe('backend-1');
    expect(await screen.findByText('sk-admin-PLAIN')).toBeInTheDocument();
  });

  it('server_name/ai_key_name показываются как значения связей, git — ссылкой', () => {
    render(
      <BackendDetailModal
        open
        onOpenChange={vi.fn()}
        backend={makeBackend({
          server_name: 'Server 01',
          ai_key_name: 'OpenAI Prod',
          git: 'https://github.com/acme/api-eu',
        })}
        canEdit
        onEdit={vi.fn()}
      />,
    );

    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByText('Server 01')).toBeInTheDocument();
    expect(dialog.getByText('OpenAI Prod')).toBeInTheDocument();
    const gitLink = dialog.getByRole('link', { name: 'https://github.com/acme/api-eu' });
    expect(gitLink).toHaveAttribute('href', 'https://github.com/acme/api-eu');
  });

  it('canEdit=false → карандаша нет', () => {
    render(
      <BackendDetailModal
        open
        onOpenChange={vi.fn()}
        backend={makeBackend()}
        canEdit={false}
        onEdit={vi.fn()}
      />,
    );

    expect(screen.queryByRole('button', { name: 'Редактировать' })).not.toBeInTheDocument();
  });
});
