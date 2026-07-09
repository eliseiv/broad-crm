import { QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { revealServerPassword } from '@/features/servers/api';
import { revealProxyPassword } from '@/features/proxies/api';
import { revealAiKeyValue } from '@/features/ai-keys/api';

/**
 * Reveal-эндпоинты секрета по требованию (ADR-035, 04-api.md `SecretRevealResponse`).
 * `apiRequest` замокан — проверяем путь/метод и то, что значение возвращается прямым
 * вызовом (мутация/direct call), а НЕ через useQuery с кэш-ключом list/detail.
 */
const apiMock = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock('@/lib/api', () => apiMock);

describe('reveal-секрет api (ADR-035)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.apiRequest.mockResolvedValue({ value: 'plaintext-secret' });
  });

  it('revealServerPassword → GET /servers/{id}/ssh-password, возвращает { value }', async () => {
    const res = await revealServerPassword('srv-1');
    expect(apiMock.apiRequest).toHaveBeenCalledTimes(1);
    expect(apiMock.apiRequest.mock.calls[0][0]).toBe('/servers/srv-1/ssh-password');
    // Метод не указан → дефолтный GET (options без method).
    const opts = apiMock.apiRequest.mock.calls[0][1] ?? {};
    expect(opts.method).toBeUndefined();
    expect(res).toEqual({ value: 'plaintext-secret' });
  });

  it('revealProxyPassword → GET /proxies/{id}/password, возвращает { value }', async () => {
    const res = await revealProxyPassword('px-1');
    expect(apiMock.apiRequest.mock.calls[0][0]).toBe('/proxies/px-1/password');
    expect(apiMock.apiRequest.mock.calls[0][1]?.method).toBeUndefined();
    expect(res).toEqual({ value: 'plaintext-secret' });
  });

  it('revealAiKeyValue → GET /ai-keys/{id}/key, возвращает { value }', async () => {
    const res = await revealAiKeyValue('key-1');
    expect(apiMock.apiRequest.mock.calls[0][0]).toBe('/ai-keys/key-1/key');
    expect(apiMock.apiRequest.mock.calls[0][1]?.method).toBeUndefined();
    expect(res).toEqual({ value: 'plaintext-secret' });
  });

  it('прокидывает AbortSignal в apiRequest', async () => {
    const controller = new AbortController();
    await revealServerPassword('srv-1', controller.signal);
    expect(apiMock.apiRequest.mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it('секрет НЕ попадает в глобальный queryClient-кэш (прямой вызов, без useQuery)', async () => {
    const queryClient = new QueryClient();
    const res = await revealServerPassword('srv-1');

    // Прямой вызов вернул значение…
    expect(res.value).toBe('plaintext-secret');
    // …но никакой записи в react-query-кэше не появилось (нет list/detail-ключа с секретом).
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(queryClient.getQueryData(['servers'])).toBeUndefined();
  });
});
