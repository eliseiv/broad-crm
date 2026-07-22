import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddServerModal } from '@/components/AddServerModal';
import { ApiError } from '@/lib/api';
import type { Server } from '@/types/api';

/**
 * Переключатель «Пароль / SSH-ключ» в форме добавления сервера (ADR-067 §6,
 * 08-design-system.md §Переключатель).
 *
 * Главный кейс — **очистка полей другого режима при переключении**: без неё в тело
 * запроса уехали бы ОБА материала, и сервер вернул бы `422` по правилу «ровно один
 * способ» на ошибку, которой пользователь не совершал.
 *
 * Второй кейс — **источник текста ошибки `422`**: для полей SSH-материала он серверный
 * (формулировки зафиксированы контрактом и уже человекочитаемы), а для `name`/`ip`/
 * `ssh_user` — локальный русский, потому что backend отдаёт там сырой текст pydantic.
 */

const createMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const updateMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

vi.mock('@/features/servers/hooks', () => ({
  useCreateServer: () => createMutation,
  useUpdateServer: () => updateMutation,
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const PRIVATE_KEY = '-----BEGIN OPENSSH PRIVATE KEY-----\nQUJDRA==\n-----END OPENSSH PRIVATE KEY-----';

/** Поле пароля различается по селектору: у радио-опции доступное имя тоже содержит «Пароль». */
const passwordField = () =>
  screen.queryByLabelText('Пароль', { selector: 'input[type="password"]' });

const keyField = () => screen.queryByLabelText('Приватный ключ');
const passphraseField = () => screen.queryByLabelText('Парольная фраза (опц.)');

async function fillCommonFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Название'), 'Server 01');
  await user.type(screen.getByLabelText('IP-адрес'), '10.0.0.10');
  await user.type(screen.getByLabelText('Пользователь'), 'root');
}

const selectKeyMode = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole('radio', { name: 'Способ входа: SSH-ключ' }));

const selectPasswordMode = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole('radio', { name: 'Способ входа: Пароль' }));

describe('AddServerModal — переключатель способа входа (ADR-067 §6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMutation.isPending = false;
    updateMutation.isPending = false;
  });

  it('по умолчанию активен «Пароль»: поле пароля есть, полей ключа нет', () => {
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    expect(screen.getByRole('radio', { name: 'Способ входа: Пароль' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Способ входа: SSH-ключ' })).not.toBeChecked();
    expect(passwordField()).toBeInTheDocument();
    expect(keyField()).not.toBeInTheDocument();
    expect(passphraseField()).not.toBeInTheDocument();
  });

  it('выбор «SSH-ключ» показывает «Приватный ключ» и «Парольная фраза (опц.)», скрывая «Пароль»', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await selectKeyMode(user);

    expect(keyField()).toBeInTheDocument();
    expect(passphraseField()).toBeInTheDocument();
    expect(passwordField()).not.toBeInTheDocument();
  });

  it('ОБЯЗАТЕЛЬНЫЙ: переключение туда-обратно очищает поля другого режима', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    // Пароль набран в парольном режиме…
    await user.type(passwordField()!, 'secret');
    // …переключились на ключ и набрали ключ + фразу…
    await selectKeyMode(user);
    await user.type(keyField()!, PRIVATE_KEY);
    await user.type(passphraseField()!, 'phrase');
    // …и вернулись назад: поля ключа обязаны быть пусты.
    await selectPasswordMode(user);

    expect(passwordField()).toHaveValue('');

    await selectKeyMode(user);
    expect(keyField()).toHaveValue('');
    expect(passphraseField()).toHaveValue('');
  });

  it('после переключения в тело уходит материал РОВНО одного способа', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await fillCommonFields(user);
    await user.type(passwordField()!, 'secret');
    await selectKeyMode(user);
    await user.type(keyField()!, PRIVATE_KEY);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(createMutation.mutate).toHaveBeenCalledTimes(1);
    const payload = createMutation.mutate.mock.calls[0][0];
    expect(payload).toEqual({
      name: 'Server 01',
      ip: '10.0.0.10',
      ssh_user: 'root',
      auth_method: 'key',
      ssh_private_key: PRIVATE_KEY,
    });
    // Ни поля пароля, ни пустой парольной фразы в теле нет.
    expect(payload).not.toHaveProperty('ssh_password');
    expect(payload).not.toHaveProperty('ssh_key_passphrase');
  });

  it('парольный режим шлёт прежнее тело БЕЗ auth_method (обратная совместимость)', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await fillCommonFields(user);
    await user.type(passwordField()!, 'secret');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(createMutation.mutate.mock.calls[0][0]).toEqual({
      name: 'Server 01',
      ip: '10.0.0.10',
      ssh_user: 'root',
      ssh_password: 'secret',
    });
  });

  it('непустая парольная фраза уходит в тело, пустая — нет', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await fillCommonFields(user);
    await selectKeyMode(user);
    await user.type(keyField()!, PRIVATE_KEY);
    await user.type(passphraseField()!, 'phrase');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(createMutation.mutate.mock.calls[0][0]).toMatchObject({
      auth_method: 'key',
      ssh_key_passphrase: 'phrase',
    });
  });

  it('клиентская валидация key-режима требует ключ и не шлёт запрос', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await fillCommonFields(user);
    await selectKeyMode(user);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Вставьте приватный ключ')).toBeInTheDocument();
    expect(createMutation.mutate).not.toHaveBeenCalled();
  });

  it('переключение снимает подсветку полей очищенного режима', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await fillCommonFields(user);
    // Отправка в парольном режиме без пароля — появилась ошибка поля пароля.
    await user.click(screen.getByRole('button', { name: 'Добавить' }));
    expect(screen.getByText('Укажите пароль')).toBeInTheDocument();

    await selectKeyMode(user);

    // Ошибка исчезнувшего поля не должна «висеть» на форме.
    expect(screen.queryByText('Укажите пароль')).not.toBeInTheDocument();
  });

  it('в режиме edit переключателя способа входа нет', () => {
    const server = {
      id: 's1',
      name: 'Server 01',
      ip: '10.0.0.10',
      ssh_user: 'root',
      auth_method: 'password',
      exporter_port: 9100,
      provision_status: 'online',
      position: 0,
    } as unknown as Server;

    render(<AddServerModal open onOpenChange={vi.fn()} mode="edit" server={server} />);

    expect(screen.queryByRole('radio', { name: 'Способ входа: Пароль' })).not.toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Способ входа: SSH-ключ' })).not.toBeInTheDocument();
    expect(keyField()).not.toBeInTheDocument();
  });
});

describe('AddServerModal — источник текста ошибки 422 (ADR-067 §6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMutation.isPending = false;
  });

  async function submitKeyModeWith422(details: { field: string; message: string }[]) {
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(422, 'validation_error', 'Проверьте поля', details)),
    );
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);
    await fillCommonFields(user);
    await selectKeyMode(user);
    await user.type(keyField()!, PRIVATE_KEY);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));
    return user;
  }

  it('422 с field=ssh_private_key подсвечивает «Приватный ключ» СЕРВЕРНЫМ текстом', async () => {
    await submitKeyModeWith422([
      { field: 'ssh_private_key', message: 'Тип ключа не поддерживается' },
    ]);

    // Текст — ровно серверный: формулировки материала зафиксированы контрактом.
    expect(screen.getByText('Тип ключа не поддерживается')).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith('Проверьте корректность полей');
  });

  it('422 с field=ssh_key_passphrase подсвечивает «Парольная фраза» СЕРВЕРНЫМ текстом', async () => {
    await submitKeyModeWith422([
      { field: 'ssh_key_passphrase', message: 'Неверная парольная фраза' },
    ]);

    expect(screen.getByText('Неверная парольная фраза')).toBeInTheDocument();
  });

  it('422 «уберите парольную фразу» показывается на поле фразы дословно', async () => {
    await submitKeyModeWith422([
      { field: 'ssh_key_passphrase', message: 'Ключ не защищён парольной фразой — уберите её' },
    ]);

    expect(
      screen.getByText('Ключ не защищён парольной фразой — уберите её'),
    ).toBeInTheDocument();
  });

  it('ОБЯЗАТЕЛЬНЫЙ: для ip показывается ЛОКАЛЬНЫЙ русский текст, а не сырой pydantic (случай «:1»)', async () => {
    // `:1` проходит клиентскую IPv6-регулярку, но отвергается серверным IPvAnyAddress —
    // ветка достижима, и именно в ней backend отдаёт сырой английский текст pydantic.
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(
        new ApiError(422, 'validation_error', 'Проверьте поля', [
          { field: 'ip', message: 'value is not a valid IPv4 or IPv6 address' },
        ]),
      ),
    );
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), 'Server 01');
    await user.type(screen.getByLabelText('IP-адрес'), ':1');
    await user.type(screen.getByLabelText('Пользователь'), 'root');
    await user.type(passwordField()!, 'secret');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // Запрос ушёл (клиентская регулярка `:1` пропустила) — иначе кейс был бы недостижим.
    expect(createMutation.mutate).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Некорректный IP-адрес')).toBeInTheDocument();
    expect(
      screen.queryByText('value is not a valid IPv4 or IPv6 address'),
    ).not.toBeInTheDocument();
  });

  it('для name и ssh_user сырой текст pydantic тоже заменяется локальным', async () => {
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(
        new ApiError(422, 'validation_error', 'Проверьте поля', [
          { field: 'name', message: 'String should have at most 64 characters' },
          { field: 'ssh_user', message: 'String should have at most 64 characters' },
        ]),
      ),
    );
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await fillCommonFields(user);
    await user.type(passwordField()!, 'secret');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Некорректное название (1–64 символа)')).toBeInTheDocument();
    expect(screen.getByText('Некорректный пользователь (1–64 символа)')).toBeInTheDocument();
    expect(
      screen.queryByText('String should have at most 64 characters'),
    ).not.toBeInTheDocument();
  });

  it('422 с field=ssh_password подсвечивает поле пароля серверным текстом', async () => {
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(
        new ApiError(422, 'validation_error', 'Проверьте поля', [
          { field: 'ssh_password', message: 'Поле недопустимо при входе по ключу — уберите его' },
        ]),
      ),
    );
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await fillCommonFields(user);
    await user.type(passwordField()!, 'secret');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(
      screen.getByText('Поле недопустимо при входе по ключу — уберите его'),
    ).toBeInTheDocument();
  });
});
