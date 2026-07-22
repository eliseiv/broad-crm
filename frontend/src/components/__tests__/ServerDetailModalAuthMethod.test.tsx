import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServerDetailModal } from '@/components/ServerDetailModal';
import type { Server } from '@/types/api';

/**
 * Detail-модалка сервера: строка «Способ входа» и вид строки секрета (ADR-067 §6).
 *
 * Главный кейс — **у key-сервера кнопки-глаза нет ни при каком праве**, включая
 * `servers:edit` и супер-админа. Это не «скрыто из-за отсутствия прав»: reveal-эндпоинта
 * для приватного ключа и парольной фразы **не существует by design** (ADR-067 §4), они
 * write-only. Поэтому проверяется И отсутствие кнопки, И отсутствие сетевого вызова.
 */

const revealServerPassword = vi.hoisted(() => vi.fn());
const updateMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

vi.mock('@/features/servers/api', () => ({ revealServerPassword }));
vi.mock('@/features/servers/hooks', () => ({ useUpdateServer: () => updateMutation }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeServer(overrides: Partial<Server> = {}): Server {
  return {
    id: 's1',
    name: 'Server 01',
    ip: '10.0.0.10',
    ssh_user: 'root',
    auth_method: 'password',
    exporter_port: 9100,
    provision_status: 'online',
    position: 0,
    online: true,
    uptime_seconds: 100,
    last_updated: '2026-01-01T00:00:00Z',
    metrics: null,
    backend_count: 0,
    ...overrides,
  } as unknown as Server;
}

const eyeButton = () => screen.queryByRole('button', { name: 'Показать пароль' });

describe('ServerDetailModal — строка «Способ входа» (ADR-067 §6)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('auth_method=password → строка «Способ входа: Пароль» и строка секрета «Пароль»', () => {
    render(
      <ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit={true} />,
    );

    expect(screen.getByText('Способ входа')).toBeInTheDocument();
    // «Пароль» встречается дважды: значение строки способа входа и метка строки секрета.
    expect(screen.getAllByText('Пароль')).toHaveLength(2);
    expect(screen.queryByText('SSH-ключ')).not.toBeInTheDocument();
  });

  it('auth_method=key → строка «Способ входа: SSH-ключ» и строка секрета «SSH-ключ»', () => {
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ auth_method: 'key' })}
        canEdit={true}
      />,
    );

    expect(screen.getByText('Способ входа')).toBeInTheDocument();
    // «SSH-ключ» встречается дважды: как значение строки способа входа и как метка секрета.
    expect(screen.getAllByText('SSH-ключ').length).toBeGreaterThanOrEqual(2);
    // Маска показывает «материал задан» — но раскрывать нечего.
    expect(screen.getByText('••••••••')).toBeInTheDocument();
  });

  it('ОБЯЗАТЕЛЬНЫЙ: у key-сервера кнопки-глаза нет ПРИ canEdit=true (не «нет прав»)', () => {
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ auth_method: 'key' })}
        canEdit={true}
      />,
    );

    expect(eyeButton()).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Скрыть пароль' })).not.toBeInTheDocument();
  });

  it('ОБЯЗАТЕЛЬНЫЙ: у key-сервера кнопки-глаза нет и при canEdit=false', () => {
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ auth_method: 'key' })}
        canEdit={false}
      />,
    );

    expect(eyeButton()).not.toBeInTheDocument();
  });

  it('ОБЯЗАТЕЛЬНЫЙ: к reveal-эндпоинту не уходит НИ ОДНОГО запроса для key-сервера', async () => {
    const user = userEvent.setup();
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ auth_method: 'key' })}
        canEdit={true}
      />,
    );

    // Клик по самой маске (единственное, что можно нажать) тоже ничего не запрашивает.
    await user.click(screen.getByText('••••••••'));

    expect(revealServerPassword).not.toHaveBeenCalled();
  });

  it('строка секрета key-сервера — статический текст: не кнопка и не в таб-порядке', () => {
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ auth_method: 'key' })}
        canEdit={true}
      />,
    );

    const mask = screen.getByText('••••••••');
    expect(mask).not.toHaveAttribute('role', 'button');
    expect(mask).not.toHaveAttribute('tabindex');
  });

  it('парольный сервер под servers:edit — глаз ЕСТЬ и reveal вызывается (регресс ADR-035)', async () => {
    revealServerPassword.mockResolvedValue({ value: 'ssh-secret' });
    const user = userEvent.setup();
    render(
      <ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit={true} />,
    );

    const eye = eyeButton();
    expect(eye).toBeInTheDocument();
    await user.click(eye!);

    expect(revealServerPassword).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('ssh-secret')).toBeInTheDocument();
  });

  it('парольный сервер без servers:edit — глаза нет и запроса нет', () => {
    render(
      <ServerDetailModal open onOpenChange={vi.fn()} server={makeServer()} canEdit={false} />,
    );

    expect(eyeButton()).not.toBeInTheDocument();
    expect(revealServerPassword).not.toHaveBeenCalled();
  });

  it('порядок строк: Название → IP → Пользователь → Способ входа → секрет', () => {
    render(
      <ServerDetailModal
        open
        onOpenChange={vi.fn()}
        server={makeServer({ auth_method: 'key' })}
        canEdit={false}
      />,
    );

    const labels = screen
      .getAllByText(/^(Название|IP|Пользователь|Способ входа|SSH-ключ|Пароль)$/)
      .map((el) => el.textContent);

    expect(labels.slice(0, 5)).toEqual([
      'Название',
      'IP',
      'Пользователь',
      'Способ входа',
      'SSH-ключ',
    ]);
  });
});
