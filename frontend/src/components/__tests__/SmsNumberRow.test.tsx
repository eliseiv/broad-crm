import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SmsNumberRow } from '@/components/SmsNumberRow';
import type { SmsNumber, TeamListItem } from '@/types/api';

const mutations = vi.hoisted(() => ({
  update: vi.fn(),
  transfer: vi.fn(),
  del: vi.fn(),
}));

vi.mock('@/features/sms/hooks', () => ({
  useUpdateSmsNumber: () => ({ mutate: mutations.update, isPending: false }),
  useTransferSmsNumber: () => ({ mutate: mutations.transfer, isPending: false }),
  useDeleteSmsNumber: () => ({ mutate: mutations.del, isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeNumber(id: number, over: Partial<SmsNumber> = {}): SmsNumber {
  return {
    id,
    phone_number: '+15550001',
    label: null,
    team: null,
    login: null,
    app_name: null,
    note: null,
    is_active: true,
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
    ...over,
  };
}

function makeTeam(id: string, name: string): TeamListItem {
  return {
    id,
    name,
    leader_id: null,
    leader_username: null,
    member_count: 0,
    number_count: 0,
    members: [],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  };
}

const TEAMS = [makeTeam('t1', 'Продажи'), makeTeam('t2', 'Маркетинг')];

interface RowOverrides {
  number?: SmsNumber;
  teams?: TeamListItem[];
  canEdit?: boolean;
  canTransfer?: boolean;
  canDelete?: boolean;
}

// SmsNumberRow — это <tr>; оборачиваем в table/tbody (валидный DOM без warning'ов).
function renderRow(over: RowOverrides = {}) {
  const row: ReactElement = (
    <SmsNumberRow
      number={over.number ?? makeNumber(1)}
      teams={over.teams ?? TEAMS}
      canEdit={over.canEdit ?? true}
      canTransfer={over.canTransfer ?? true}
      canDelete={over.canDelete ?? true}
    />
  );
  return render(
    <table>
      <tbody>{row}</tbody>
    </table>,
  );
}

describe('SmsNumberRow', () => {
  beforeEach(() => vi.clearAllMocks());

  it('инлайн-правка: Pencil раскрывает input, Check сохраняет с новым значением', async () => {
    const user = userEvent.setup();
    renderRow({ number: makeNumber(1, { login: 'old' }) });

    await user.click(screen.getByRole('button', { name: 'Изменить: Логин' }));
    const input = screen.getByLabelText('Логин');
    await user.clear(input);
    await user.type(input, 'newlogin');
    await user.click(screen.getByRole('button', { name: 'Сохранить: Логин' }));

    expect(mutations.update).toHaveBeenCalledWith(
      { id: 1, payload: { login: 'newlogin' } },
      expect.anything(),
    );
  });

  it('инлайн-правка presence: очистка и сохранение шлёт пустую строку (затирание NULL)', async () => {
    const user = userEvent.setup();
    renderRow({ number: makeNumber(1, { login: 'old' }) });

    await user.click(screen.getByRole('button', { name: 'Изменить: Логин' }));
    await user.clear(screen.getByLabelText('Логин'));
    await user.click(screen.getByRole('button', { name: 'Сохранить: Логин' }));

    expect(mutations.update).toHaveBeenCalledWith(
      { id: 1, payload: { login: '' } },
      expect.anything(),
    );
  });

  it('инлайн-правка: X отменяет — input исчезает, mutate не вызван', async () => {
    const user = userEvent.setup();
    renderRow({ number: makeNumber(1, { login: 'old' }) });

    await user.click(screen.getByRole('button', { name: 'Изменить: Логин' }));
    await user.type(screen.getByLabelText('Логин'), 'zzz');
    await user.click(screen.getByRole('button', { name: 'Отмена: Логин' }));

    expect(screen.queryByLabelText('Логин')).not.toBeInTheDocument();
    expect(mutations.update).not.toHaveBeenCalled();
  });

  it('удаление: кнопка открывает confirm-модалку, подтверждение вызывает delete с id', async () => {
    const user = userEvent.setup();
    renderRow({ number: makeNumber(1, { phone_number: '+15550001' }) });

    await user.click(screen.getByRole('button', { name: 'Удалить номер +15550001' }));
    const dialog = await screen.findByRole('dialog', { name: 'Удалить номер?' });
    await user.click(within(dialog).getByRole('button', { name: 'Удалить' }));

    expect(mutations.del).toHaveBeenCalledWith(1, expect.anything());
  });

  it('гейтинг: без canEdit карандаши правки не рендерятся', () => {
    renderRow({ canEdit: false });
    expect(screen.queryByRole('button', { name: 'Изменить: Логин' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Изменить: Приложение' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Изменить: Примечание' })).not.toBeInTheDocument();
  });

  it('гейтинг: без canTransfer Select команды и кнопка «Перенести» отсутствуют', () => {
    renderRow({ canTransfer: false, number: makeNumber(1, { phone_number: '+15550001' }) });
    expect(screen.queryByLabelText('Команда номера +15550001')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Перенести/ })).not.toBeInTheDocument();
  });

  it('гейтинг: без canDelete кнопка «Удалить» отсутствует', () => {
    renderRow({ canDelete: false, number: makeNumber(1, { phone_number: '+15550001' }) });
    expect(
      screen.queryByRole('button', { name: 'Удалить номер +15550001' }),
    ).not.toBeInTheDocument();
  });

  it('перенос: выбор команды активирует «Перенести» и вызывает transfer с team_id', async () => {
    const user = userEvent.setup();
    renderRow({ number: makeNumber(1, { phone_number: '+15550001' }) });

    const btn = screen.getByRole('button', { name: /Перенести/ });
    // Без изменения команды (target === current) кнопка отключена.
    expect(btn).toBeDisabled();

    await user.selectOptions(screen.getByLabelText('Команда номера +15550001'), 't1');
    expect(btn).toBeEnabled();

    await user.click(btn);
    expect(mutations.transfer).toHaveBeenCalledWith(
      { id: 1, payload: { team_id: 't1' } },
      expect.anything(),
    );
  });

  it('перенос: выбор «Без команды» у номера с командой шлёт team_id=null (снятие)', async () => {
    const user = userEvent.setup();
    renderRow({
      number: makeNumber(1, { phone_number: '+15550001', team: { id: 't1', name: 'Продажи' } }),
    });

    await user.selectOptions(screen.getByLabelText('Команда номера +15550001'), '');
    await user.click(screen.getByRole('button', { name: /Перенести/ }));

    expect(mutations.transfer).toHaveBeenCalledWith(
      { id: 1, payload: { team_id: null } },
      expect.anything(),
    );
  });
});
