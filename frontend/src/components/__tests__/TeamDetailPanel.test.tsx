import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TeamDetailPanel } from '@/components/TeamDetailPanel';
import type { TeamListItem, TeamNumberItem } from '@/types/api';

// Ленивый GET /teams/{id}/numbers через useTeamNumbers — управляем состоянием запроса.
const teamNumbers = vi.hoisted(() => ({ value: null as unknown }));

vi.mock('@/features/sms/hooks', () => ({
  useTeamNumbers: () => teamNumbers.value,
}));

// Минимальная схема TeamNumberItem (ADR-030 §8): только id/phone_number/team —
// detail-панель /teams под гейтом teams:view НЕ получает login/app_name/note/label.
function makeNumber(id: number, over: Partial<TeamNumberItem> = {}): TeamNumberItem {
  return {
    id,
    phone_number: '+15557778888',
    team: { id: 't1', name: 'Продажи' },
    ...over,
  };
}

const TEAM: TeamListItem = {
  id: 't1',
  name: 'Продажи',
  leader_id: 'u1',
  leader_username: 'Никита',
  member_count: 2,
  number_count: 1,
  members: [
    { id: 'u1', username: 'Никита' },
    { id: 'u2', username: 'Мария' },
  ],
  created_at: '2026-07-08T09:00:00Z',
  updated_at: '2026-07-08T09:00:00Z',
};

interface QueryOverrides {
  data?: { numbers: TeamNumberItem[] };
  isLoading?: boolean;
  isError?: boolean;
  isFetching?: boolean;
  refetch?: () => void;
}

function query(over: QueryOverrides = {}) {
  return {
    data: over.data,
    isLoading: over.isLoading ?? false,
    isError: over.isError ?? false,
    isFetching: over.isFetching ?? false,
    refetch: over.refetch ?? vi.fn(),
  };
}

describe('TeamDetailPanel (ленивый список номеров команды)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('loading: показывает индикатор загрузки', () => {
    teamNumbers.value = query({ isLoading: true });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  });

  it('empty: «Номеров нет», когда список пуст', () => {
    teamNumbers.value = query({ data: { numbers: [] } });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    expect(screen.getByText('Номеров нет')).toBeInTheDocument();
  });

  it('error: заглушка + «Повторить» вызывает refetch', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    teamNumbers.value = query({ isError: true, refetch });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);

    expect(screen.getByText('Не удалось загрузить')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Повторить/ }));
    expect(refetch).toHaveBeenCalled();
  });

  it('ready: рендерит номера команды и данные команды (имя/лидер/участники)', () => {
    teamNumbers.value = query({
      data: { numbers: [makeNumber(7, { phone_number: '+15557778888' })] },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);

    expect(screen.getByText('+15557778888')).toBeInTheDocument();
    // Шапка панели: название + участники (из team.members). Лидер «Никита»
    // встречается и в поле «Лидер», и в чипе участника — проверяем через getAllByText.
    expect(screen.getByText('Продажи')).toBeInTheDocument();
    expect(screen.getAllByText('Никита').length).toBeGreaterThan(0);
    expect(screen.getByText('Мария')).toBeInTheDocument();
  });
});
