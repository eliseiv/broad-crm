import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddAiKeyModal } from '@/components/AddAiKeyModal';
import { AddBackendModal } from '@/components/AddBackendModal';
import { AddProxyModal } from '@/components/AddProxyModal';
import type { AiKey, Backend, Proxy } from '@/types/api';

/**
 * Подсказки «Оставьте пустым, чтобы не менять …» под полями секретов (ИИ-ключ / прокси / бэк) —
 * доступность по TD-061 (08-design-system.md §«Подсказка под полем формы связывается с
 * контролом»).
 *
 * **ИЗМЕНЕНИЕ ПОВЕДЕНИЯ (норма).** Раньше подсказка была отдельным `<p>` рядом с полем и в
 * описании контрола не участвовала; теперь она передаётся пропом `hint` примитива и ВХОДИТ в
 * `aria-describedby`. При появлении инлайн-ошибки подсказка **НЕ исчезает** — описание = «id
 * подсказки, затем id ошибки». Ассертить её ОТСУТСТВИЕ при ошибке больше нельзя.
 */

const aiMutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));
const proxyMutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));
const backendMutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));

vi.mock('@/features/ai-keys/hooks', () => ({
  useCreateAiKey: () => ({ mutate: aiMutations.create, isPending: false }),
  useUpdateAiKey: () => ({ mutate: aiMutations.update, isPending: false }),
  useAiKeys: () => ({ data: { items: [] } }),
}));
vi.mock('@/features/proxies/hooks', () => ({
  useCreateProxy: () => ({ mutate: proxyMutations.create, isPending: false }),
  useUpdateProxy: () => ({ mutate: proxyMutations.update, isPending: false }),
}));
vi.mock('@/features/backends/hooks', () => ({
  useCreateBackend: () => ({ mutate: backendMutations.create, isPending: false }),
  useUpdateBackend: () => ({ mutate: backendMutations.update, isPending: false }),
}));
vi.mock('@/features/servers/hooks', () => ({ useServers: () => ({ data: { items: [] } }) }));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const AI_KEY_HINT = 'Оставьте пустым, чтобы не менять ключ';
const PROXY_HINT = 'Оставьте пустым, чтобы не менять пароль';
const BACKEND_HINT = 'Оставьте пустым, чтобы не менять';
const TOO_LONG = 'Не более 512 символов';

beforeEach(() => {
  vi.clearAllMocks();
});

/** Тексты узлов, на которые ссылается `aria-describedby`. Висячий IDREF → падение теста. */
function describedTexts(el: HTMLElement): string[] {
  const attr = el.getAttribute('aria-describedby');
  if (attr === null) return [];
  return attr
    .split(' ')
    .filter(Boolean)
    .map((id) => {
      const node = document.getElementById(id);
      expect(node, `висячий IDREF: узла с id="${id}" нет в DOM`).not.toBeNull();
      return node?.textContent ?? '';
    });
}

function makeAiKey(): AiKey {
  return {
    id: 'key-1',
    name: 'Claude Prod',
    provider: 'anthropic',
    key_masked: 'sk-a…xyz',
    check_status: 'working',
    error_message: null,
    position: 0,
    backend_count: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
  };
}

function makeProxy(): Proxy {
  return {
    id: 'proxy-1',
    name: 'DE Residential',
    proxy_type: 'socks5',
    host: 'proxy.example.com',
    port: 1080,
    username: 'user01',
    has_password: true,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
  };
}

function makeBackend(): Backend {
  return {
    id: 'backend-1',
    code: 'api-eu',
    name: 'API EU',
    domain: 'api.example.com',
    server_id: null,
    server_name: null,
    ai_key_id: null,
    ai_key_name: null,
    has_api_key: true,
    has_admin_api_key: true,
    git: null,
    note: null,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
  };
}

describe('AddAiKeyModal (edit) — подсказка секрета (TD-061)', () => {
  it('подсказка «Оставьте пустым, чтобы не менять ключ» — часть описания поля «Ключ»', () => {
    render(<AddAiKeyModal open onOpenChange={vi.fn()} mode="edit" aiKey={makeAiKey()} />);

    const field = screen.getByLabelText('Ключ');
    expect(field).toHaveAccessibleDescription(AI_KEY_HINT);
    expect(describedTexts(field)).toEqual([AI_KEY_HINT]);
  });

  it('при инлайн-ошибке подсказка НЕ исчезает: описание = «подсказка + ошибка»', async () => {
    const user = userEvent.setup();
    render(<AddAiKeyModal open onOpenChange={vi.fn()} mode="edit" aiKey={makeAiKey()} />);

    // `touched` включается сабмитом; затем задаём заведомо длинный ключ (maxLength не пускает
    // такой ввод пользователем — ставим значение программно, как это делает вставка/автозаполнение).
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    const field = screen.getByLabelText('Ключ');
    fireEvent.change(field, { target: { value: 'k'.repeat(513) } });

    expect(screen.getByText(TOO_LONG)).toBeVisible();
    expect(screen.getByText(AI_KEY_HINT)).toBeVisible();
    expect(describedTexts(field)).toEqual([AI_KEY_HINT, TOO_LONG]);
    expect(field).toHaveAccessibleDescription(`${AI_KEY_HINT} ${TOO_LONG}`);
    expect(field).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('AddProxyModal (edit) — подсказка секрета (TD-061)', () => {
  it('подсказка «Оставьте пустым, чтобы не менять пароль» — часть описания поля «Пароль»', () => {
    render(<AddProxyModal open onOpenChange={vi.fn()} mode="edit" proxy={makeProxy()} />);

    const field = screen.getByLabelText('Пароль');
    expect(field).toHaveAccessibleDescription(PROXY_HINT);
    expect(describedTexts(field)).toEqual([PROXY_HINT]);
  });

  it('при инлайн-ошибке подсказка НЕ исчезает: описание = «подсказка + ошибка»', async () => {
    const user = userEvent.setup();
    render(<AddProxyModal open onOpenChange={vi.fn()} mode="edit" proxy={makeProxy()} />);

    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    const field = screen.getByLabelText('Пароль');
    fireEvent.change(field, { target: { value: 'p'.repeat(513) } });

    expect(screen.getByText(TOO_LONG)).toBeVisible();
    expect(screen.getByText(PROXY_HINT)).toBeVisible();
    expect(describedTexts(field)).toEqual([PROXY_HINT, TOO_LONG]);
    expect(field).toHaveAccessibleDescription(`${PROXY_HINT} ${TOO_LONG}`);
    expect(field).toHaveAttribute('aria-invalid', 'true');
  });
});

/** Секция «Информация» (ADR-040) свёрнута по умолчанию — поля ключей внутри неё. */
async function expandInfoSection(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Информация' }));
}

describe('AddBackendModal — подсказки секретов (TD-061)', () => {
  it('edit: подсказка связана с обоими полями ключей (API KEY и ADMIN API KEY)', async () => {
    const user = userEvent.setup();
    render(<AddBackendModal open onOpenChange={vi.fn()} mode="edit" backend={makeBackend()} />);
    await expandInfoSection(user);

    const apiKey = screen.getByLabelText('API KEY');
    const adminKey = screen.getByLabelText('ADMIN API KEY');

    expect(describedTexts(apiKey)).toEqual([BACKEND_HINT]);
    expect(describedTexts(adminKey)).toEqual([BACKEND_HINT]);
    // Разные поля — РАЗНЫЕ узлы подсказки (у каждого свой id, общий IDREF не переиспользуется).
    expect(apiKey.getAttribute('aria-describedby')).not.toBe(
      adminKey.getAttribute('aria-describedby'),
    );
  });

  it('add: подсказки «Оставьте пустым…» нет ⇒ у полей ключей нет aria-describedby', async () => {
    const user = userEvent.setup();
    render(<AddBackendModal open onOpenChange={vi.fn()} mode="add" />);
    await expandInfoSection(user);

    expect(screen.getByLabelText('API KEY')).not.toHaveAttribute('aria-describedby');
    expect(screen.getByLabelText('ADMIN API KEY')).not.toHaveAttribute('aria-describedby');
    expect(screen.queryByText(BACKEND_HINT)).not.toBeInTheDocument();
  });
});
