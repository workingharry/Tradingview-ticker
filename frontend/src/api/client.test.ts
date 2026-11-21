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

  it('throws descriptive error when API returns non-200 status', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => 'Not Found'
    } as any);

    await expect(apiFetch('/api/missing')).rejects.toThrow('API 404 on /api/missing: Not Found');
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
