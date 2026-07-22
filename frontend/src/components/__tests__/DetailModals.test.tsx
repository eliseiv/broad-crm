import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServerDetailModal } from '@/components/ServerDetailModal';
import { ProxyDetailModal } from '@/components/ProxyDetailModal';
import { AiKeyDetailModal } from '@/components/AiKeyDetailModal';
import type { AiKey, Proxy, Server } from '@/types/api';

/**
 * Read-only detail-модалки карточных страниц (ADR-035/ADR-039/ADR-040; состав — ADR-049).
 * Reveal-функции секрета замоканы на уровне feature-api — сеть НЕ трогаем; секрет живёт ТОЛЬКО
 * в локальном стейте SecretRevealField. Ленивый reverse-lookup «Бэки» и мутация inline-edit
 * замоканы на уровне feature-hooks → модалки рендерятся БЕЗ QueryClientProvider (доказывает,
 * что reveal и reverse-lookup идут прямыми вызовами/ленивым хуком, а не через общий кэш).
 *
 * **ADR-049:** `BackendDetailModal` УПРАЗДНЕНА (её тесты переехали в `BackendCard.test.tsx` —
 * card-first). У `ServerDetailModal` блок «Информация» и секция «Бэки» УПРАЗДНЕНЫ: Название /
 * IP / Пользователь / Пароль видны сразу, без сворачивания (§1). Свёрнутый `DetailInfoSection`
 * остаётся ТОЛЬКО у `AiKeyDetailModal` и `ProxyDetailModal` (их норма не менялась).
 */

const serversApi = vi.hoisted(() => ({ revealServerPassword: vi.fn() }));
const proxiesApi = vi.hoisted(() => ({ revealProxyPassword: vi.fn() }));
const aiKeysApi = vi.hoisted(() => ({ revealAiKeyValue: vi.fn() }));
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
vi.mock('@/features/servers/hooks', () => ({
  useUpdateServer: () => ({ mutate: serverHooks.updateMutate, isPending: false }),
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
    auth_method: 'password',
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

beforeEach(() => vi.clearAllMocks());

/**
 * Раскрывает свёрнутый по умолчанию блок «Информация» (ADR-046 §2в — в силе для ИИ-ключа и
 * прокси): при открытии их модалки видны ТОЛЬКО идентификаторы, всё прочее (тип, логин,
 * секреты, секция «Бэки») — внутри этого блока. У сервера блока «Информация» БОЛЬШЕ НЕТ
 * (ADR-049 §1) — там ничего раскрывать не нужно.
 */
async function openInfo(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole('button', { name: 'Информация' }));
}

describe('DetailInfoSection — блок «Информация» свёрнут по умолчанию (ADR-046 §2в)', () => {
  it('ИИ-ключ: содержимое «Информации» не отрендерено, пока блок не раскрыт', () => {
    render(
      <AiKeyDetailModal open onOpenChange={vi.fn()} aiKey={makeKey()} canEdit onEdit={vi.fn()} />,
    );

    const trigger = screen.getByRole('button', { name: 'Информация' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    // Идентификаторы видны сразу (Название / Провайдер).
    expect(screen.getByText('OpenAI Prod')).toBeInTheDocument();
    expect(screen.getByText('OpenAI')).toBeInTheDocument();
    // Содержимое «Информации» (маска ключа, секция «Бэки») в DOM отсутствует.
    expect(screen.queryByText('sk-p…bA3T')).not.toBeInTheDocument();
    expect(screen.queryByText('Бэков: 0')).not.toBeInTheDocument();
  });

  it('ИИ-ключ: клик по триггеру раскрывает блок (aria-expanded=true) и показывает содержимое', async () => {
    const user = userEvent.setup();
    render(
      <AiKeyDetailModal open onOpenChange={vi.fn()} aiKey={makeKey()} canEdit onEdit={vi.fn()} />,
    );

    await openInfo(user);

    expect(screen.getByRole('button', { name: 'Информация' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(screen.getByText('sk-p…bA3T')).toBeInTheDocument();
  });
});

describe('DetailRow — пустые поля в detail-view НЕ рендерятся (ADR-046 §3)', () => {
  it('строка с пустым значением не рендерится (прочерк «—» упразднён)', async () => {
    const user = userEvent.setup();
    // Прокси без логина: строка «Логин» не рендерится, «—» нигде не появляется.
    render(
      <ProxyDetailModal
        open
        onOpenChange={vi.fn()}
        proxy={makeProxy({ username: null })}
        canEdit
        onEdit={vi.fn()}
      />,
    );

    await openInfo(user);

    expect(screen.queryByText('Логин')).not.toBeInTheDocument();
    expect(screen.queryByText('—')).not.toBeInTheDocument();
    // Непустые поля на месте.
    expect(screen.getByText('SOCKS5')).toBeInTheDocument();
  });
});

describe('ServerDetailModal (ADR-049 §1: креды в главном блоке, «Информация» упразднена)', () => {
  it('Название → IP → Пользователь → Пароль видны СРАЗУ; блока «Информация» и секции «Бэки» нет', () => {
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ backend_count: 3 })}
        canEdit
      />,
    );

    const dialog = within(screen.getByRole('dialog'));
    // Все четыре строки — в видимой зоне, без сворачивания (ADR-049 §1).
    expect(dialog.getByText('Название')).toBeInTheDocument();
    expect(dialog.getByText('Server 01')).toBeInTheDocument();
    expect(dialog.getByText('IP')).toBeInTheDocument();
    expect(dialog.getByText('10.0.0.10')).toBeInTheDocument();
    expect(dialog.getByText('Пользователь')).toBeInTheDocument();
    expect(dialog.getByText('root')).toBeInTheDocument();
    // ADR-067 §6: строка «Способ входа» + строка секрета — обе со значением / меткой «Пароль».
    expect(dialog.getByText('Способ входа')).toBeInTheDocument();
    expect(dialog.getAllByText('Пароль')).toHaveLength(2);
    expect(dialog.getByText(MASK)).toBeInTheDocument();

    // Свёрнутый блок «Информация» УПРАЗДНЁН (разворот ADR-046 §2в).
    expect(screen.queryByRole('button', { name: 'Информация' })).not.toBeInTheDocument();
    // Секция «Бэки» переехала на карточку (ADR-049 §2) — дублировать её в модалке запрещено.
    expect(dialog.queryByText('Бэки')).not.toBeInTheDocument();
    expect(dialog.queryByText('Бэков: 3')).not.toBeInTheDocument();
  });

  it('карандаш под servers:edit открывает inline-edit имени', async () => {
    const user = userEvent.setup();
    render(<ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit />);

    const dialog = within(screen.getByRole('dialog'));

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

  it('reveal пароля вызывает api-функцию on-demand; значение только после клика (ADR-049 §4)', async () => {
    const user = userEvent.setup();
    serversApi.revealServerPassword.mockResolvedValue({ value: 'ssh-plE1n' });
    render(<ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit />);

    // Строка «Пароль» видна сразу, но секрет НЕ преднагружается: до клика по глазу — маска.
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
});

describe('ProxyDetailModal (ADR-035)', () => {
  it('рендерит поля и reveal пароля при has_password', async () => {
    const user = userEvent.setup();
    proxiesApi.revealProxyPassword.mockResolvedValue({ value: 'proxy-secr3t' });
    render(
      <ProxyDetailModal open onOpenChange={vi.fn()} proxy={makeProxy()} canEdit onEdit={vi.fn()} />,
    );

    const dialog = within(screen.getByRole('dialog'));
    // Видны сразу — идентификаторы (Название/Хост/Порт).
    expect(dialog.getByText('proxy.example.com')).toBeInTheDocument();
    expect(dialog.getByText('1080')).toBeInTheDocument();

    // Тип/Логин/Пароль — внутри «Информации» (ADR-046 §2в).
    await openInfo(user);
    expect(dialog.getByText('SOCKS5')).toBeInTheDocument();
    expect(dialog.getByText('user01')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать пароль' }));
    expect(proxiesApi.revealProxyPassword.mock.calls[0][0]).toBe('px-1');
    expect(await screen.findByText('proxy-secr3t')).toBeInTheDocument();
  });

  it('has_password=false → строка «Пароль» НЕ рендерится вовсе («Пароль: —» упразднён, ADR-046 §3)', async () => {
    const user = userEvent.setup();
    render(
      <ProxyDetailModal
        open
        onOpenChange={vi.fn()}
        proxy={makeProxy({ has_password: false })}
        canEdit
        onEdit={vi.fn()}
      />,
    );

    await openInfo(user);

    expect(screen.queryByRole('button', { name: 'Показать пароль' })).not.toBeInTheDocument();
    // Ни строки «Пароль», ни прочерка «—»: секрет не задан → строки нет (нормативная строка
    // словаря «Пароль: —» упразднена).
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.queryByText('Пароль')).not.toBeInTheDocument();
    expect(dialog.queryByText('—')).not.toBeInTheDocument();
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
    // Видны сразу — Название и Провайдер.
    expect(dialog.getByText('OpenAI')).toBeInTheDocument();
    expect(dialog.getByText('OpenAI Prod')).toBeInTheDocument();
    // Поле «Ключ» — внутри «Информации» (ADR-046 §2в).
    expect(dialog.queryByText('sk-p…bA3T')).not.toBeInTheDocument();

    await openInfo(user);

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
