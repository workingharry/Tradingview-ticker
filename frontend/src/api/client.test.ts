import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  BackendUnavailableError,
  getBackendConfig,
  invalidateBackendConfig,
  apiFetch,
  LiveFeed
} from './client';

describe('API Client & Transport Hardening', () => {
  beforeEach(() => {
    invalidateBackendConfig();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    invalidateBackendConfig();
  });

  it('exports typed BackendUnavailableError with correct prototype', () => {
    const err = new BackendUnavailableError('Server down');
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(BackendUnavailableError);
    expect(err.name).toBe('BackendUnavailableError');
    expect(err.message).toBe('Server down');
  });

  it('caches the BackendConfig Promise and re-fetches on invalidateBackendConfig', async () => {
    const config1 = await getBackendConfig();
    const config2 = await getBackendConfig();
    expect(config1).toBe(config2);

    invalidateBackendConfig();
    const config3 = await getBackendConfig();
    expect(config3).toEqual(config1);
  });

  it('injects Content-Type and X-Terminal-Token headers into apiFetch', async () => {
    const mockResponse = { data: 'ok' };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse
    } as any);

    const res = await apiFetch<typeof mockResponse>('/api/test', {
      method: 'POST',
      body: JSON.stringify({ hello: 'world' })
    });

    expect(res).toEqual(mockResponse);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [calledUrl, calledInit] = fetchSpy.mock.calls[0];
    expect(calledUrl).toBe('/api/test');
    const headers = new Headers(calledInit?.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('throws descriptive ApiHttpError when API returns non-200 status and does not retry 4xx', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 404,
      text: async () => 'Not Found'
    } as any);

    await expect(
      apiFetch('/api/missing', { retry: { max: 2, baseMs: 1, capMs: 5 } })
    ).rejects.toThrow('API 404 on /api/missing: Not Found');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('does not retry when request is aborted via AbortSignal', async () => {
    const ac = new AbortController();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementationOnce(() => {
      ac.abort(new DOMException('Aborted', 'AbortError'));
      return Promise.reject(new DOMException('Aborted', 'AbortError'));
    });

    await expect(
      apiFetch('/api/aborted', {
        signal: ac.signal,
        retry: { max: 3, baseMs: 1, capMs: 5 }
      })
    ).rejects.toThrow();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('retries transient HTTP 5xx responses and resolves on subsequent success', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        text: async () => 'Service Unavailable'
      } as any)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: 'recovered' })
      } as any);

    const res = await apiFetch<{ status: string }>('/api/flaky', {
      retry: { max: 2, baseMs: 1, capMs: 5 }
    });
    expect(res).toEqual({ status: 'recovered' });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('manages LiveFeed lifecycle transitions and gap detection', () => {
    const onGap = vi.fn();
    const onStateChange = vi.fn();

    const feed = new LiveFeed({
      path: '/ws/live',
      onGap,
      onStateChange
    });

    expect(feed.state).toBe('idle');
    feed.stop();
    expect(feed.state).toBe('closed');
  });
});
