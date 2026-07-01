import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppLayout } from '@/components/AppLayout';
import { useAuthStore } from '@/store/auth';

function renderAt(initial: string) {
  function wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return render(
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/servers" element={<div>Контент серверов</div>} />
        <Route path="/ai-keys" element={<div>Контент ключей</div>} />
      </Route>
    </Routes>,
    { wrapper },
  );
}

describe('AppLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().setSession('jwt-token', 'admin');
  });

  it('renders both navigation tabs', () => {
    renderAt('/servers');
    expect(screen.getByRole('link', { name: 'Серверы' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ИИ - ключи' })).toBeInTheDocument();
  });

  it('marks the current route tab active', () => {
    renderAt('/servers');
    const serversTab = screen.getByRole('link', { name: 'Серверы' });
    const keysTab = screen.getByRole('link', { name: 'ИИ - ключи' });
    // Активная вкладка подсвечивается акцентом; неактивная — вторичным цветом.
    expect(serversTab.className).toContain('text-accent');
    expect(keysTab.className).toContain('text-text-secondary');
  });

  it('marks the ai-keys tab active on that route', () => {
    renderAt('/ai-keys');
    expect(screen.getByRole('link', { name: 'ИИ - ключи' }).className).toContain('text-accent');
    expect(screen.getByRole('link', { name: 'Серверы' }).className).toContain(
      'text-text-secondary',
    );
  });

  it('renders the child page through the Outlet', () => {
    renderAt('/servers');
    expect(screen.getByText('Контент серверов')).toBeInTheDocument();
    expect(screen.queryByText('Контент ключей')).not.toBeInTheDocument();
  });

  it('navigates to ai-keys when the tab is clicked', async () => {
    const user = userEvent.setup();
    renderAt('/servers');

    await user.click(screen.getByRole('link', { name: 'ИИ - ключи' }));

    expect(screen.getByText('Контент ключей')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ИИ - ключи' }).className).toContain('text-accent');
  });

  it('shows the username and clears session on logout', async () => {
    const user = userEvent.setup();
    renderAt('/servers');

    expect(screen.getByText('admin')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /выйти/i }));

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
