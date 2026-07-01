import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AiKeysPage } from '@/pages/AiKeysPage';
import type { AiKey } from '@/types/api';

const aiKeysHook = vi.hoisted(() => ({
  value: {
    data: undefined as { items: AiKey[] } | undefined,
    isLoading: false,
    isError: false,
    error: null as unknown,
    refetch: vi.fn(),
    isFetching: false,
  },
}));

vi.mock('@/features/ai-keys/hooks', () => ({
  aiKeysKey: ['ai-keys'],
  useAiKeys: () => aiKeysHook.value,
  useReorderAiKeys: () => ({ mutate: vi.fn(), isPending: false }),
  useAiKeyStatus: () => ({ data: undefined }),
  useDeleteAiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateAiKey: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeKey(overrides: Partial<AiKey> & Pick<AiKey, 'id'>): AiKey {
  return {
    name: 'Key',
    provider: 'openai',
    key_masked: 'sk-p…bA3T',
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-01T10:15:00Z',
    created_at: '2026-07-01T09:00:00Z',
    ...overrides,
  };
}

describe('AiKeysPage grouping', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    aiKeysHook.value = {
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    };
  });

  it('renders OpenAI and Anthropic sections in fixed order', () => {
    aiKeysHook.value = {
      ...aiKeysHook.value,
      data: {
        items: [
          makeKey({ id: 'a1', name: 'Claude', provider: 'anthropic' }),
          makeKey({ id: 'o1', name: 'GPT One', provider: 'openai' }),
          makeKey({ id: 'o2', name: 'GPT Two', provider: 'openai' }),
        ],
      },
    };
    render(<AiKeysPage />, { wrapper });

    const sectionHeadings = screen
      .getAllByRole('heading', { level: 2 })
      .map((h) => h.textContent);
    // Секции всегда в фиксированном порядке: OpenAI, затем Anthropic.
    expect(sectionHeadings).toEqual(['OpenAI', 'Anthropic']);
  });

  it('hides a provider section that has no keys', () => {
    aiKeysHook.value = {
      ...aiKeysHook.value,
      data: { items: [makeKey({ id: 'o1', name: 'GPT One', provider: 'openai' })] },
    };
    render(<AiKeysPage />, { wrapper });

    expect(screen.getByRole('heading', { level: 2, name: 'OpenAI' })).toBeInTheDocument();
    // Пустая секция Anthropic не рендерится.
    expect(screen.queryByRole('heading', { level: 2, name: 'Anthropic' })).not.toBeInTheDocument();
  });

  it('shows a global empty state when there are no keys', () => {
    aiKeysHook.value = { ...aiKeysHook.value, data: { items: [] } };
    render(<AiKeysPage />, { wrapper });

    expect(screen.getByText('Пока нет ключей')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2 })).not.toBeInTheDocument();
  });
});
