import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { BackendsDetailSection } from '@/components/BackendsDetailSection';
import type { BackendRefListResponse } from '@/types/api';

/**
 * Сворачиваемая секция «Бэки» detail-view сервера/ключа (ADR-040): свёрнута по умолчанию
 * (только счётчик `backend_count`), при раскрытии родитель включает ленивый reverse-lookup —
 * секция сама отображает loading / empty / error / список Код-Название-Домен. Компонент чисто
 * презентационный: результат хука передаётся пропом `query` (сеть/react-query не трогаем).
 */

type Query = Parameters<typeof BackendsDetailSection>[0]['query'];

function makeQuery(over: Partial<Query> = {}): Query {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
    ...over,
  };
}

const list: BackendRefListResponse = {
  backends: [
    { code: 'api-eu', name: 'API EU', domain: 'https://api.eu/' },
    { code: 'api-us', name: 'API US', domain: 'https://api.us/' },
  ],
};

describe('BackendsDetailSection (ADR-040 reverse-lookup)', () => {
  it('свёрнута: показывает «Бэков: N», клик по заголовку вызывает onToggle без запроса', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(
      <BackendsDetailSection
        count={4}
        id="s-backends"
        open={false}
        onToggle={onToggle}
        query={makeQuery()}
      />,
    );

    expect(screen.getByText('Бэков: 4')).toBeInTheDocument();
    // Свёрнута — содержимое (loading/список) не в DOM.
    expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Бэки/ }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('раскрыта + loading → «Загрузка…»', () => {
    render(
      <BackendsDetailSection
        count={2}
        id="s"
        open
        onToggle={vi.fn()}
        query={makeQuery({ isLoading: true })}
      />,
    );
    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  });

  it('раскрыта + пустой список → «Бэков нет»', () => {
    render(
      <BackendsDetailSection
        count={0}
        id="s"
        open
        onToggle={vi.fn()}
        query={makeQuery({ data: { backends: [] } })}
      />,
    );
    expect(screen.getByText('Бэков нет')).toBeInTheDocument();
  });

  it('раскрыта + ошибка → сообщение и «Повторить» дергает refetch', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    render(
      <BackendsDetailSection
        count={2}
        id="s"
        open
        onToggle={vi.fn()}
        query={makeQuery({ isError: true, refetch })}
      />,
    );

    expect(screen.getByText('Не удалось загрузить')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Повторить/ }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('раскрыта + данные → строки Код/Название/Домен', () => {
    render(
      <BackendsDetailSection
        count={2}
        id="s"
        open
        onToggle={vi.fn()}
        query={makeQuery({ data: list })}
      />,
    );

    expect(screen.getByText('api-eu')).toBeInTheDocument();
    expect(screen.getByText('API EU')).toBeInTheDocument();
    expect(screen.getByText('https://api.eu/')).toBeInTheDocument();
    expect(screen.getByText('api-us')).toBeInTheDocument();
    expect(screen.getByText('https://api.us/')).toBeInTheDocument();
  });
});
