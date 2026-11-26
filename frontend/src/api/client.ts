export class BackendUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BackendUnavailableError';
  }
}

export interface BackendConfig {
  baseUrl: string;
  wsBaseUrl: string;
  token: string;
}

let cachedConfigPromise: Promise<BackendConfig> | null = null;

export function invalidateBackendConfig(): void {
  cachedConfigPromise = null;
}

function isTauriRuntime(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export async function getBackendConfig(options?: { signal?: AbortSignal; forceRefresh?: boolean }): Promise<BackendConfig> {
  if (options?.forceRefresh) {
    invalidateBackendConfig();
  }

  if (!cachedConfigPromise) {
    cachedConfigPromise = (async () => {
      try {
        // 1. Tauri Production / Desktop Mode: Retrieve sidecar dynamic port and token via Rust IPC
        if (isTauriRuntime() && !import.meta.env.DEV) {
          const { invoke } = await import('@tauri-apps/api/core');
          let info: { port: number; token: string; api_base?: string; ws_url?: string };
          try {
            info = await invoke<any>('get_session_info');
          } catch {
            info = await invoke<any>('get_backend_config');
          }
          if (!info || typeof info.port !== 'number' || info.port <= 0) {
            throw new BackendUnavailableError('Invalid or empty session info from Tauri IPC');
          }
          return {
            baseUrl: `http://127.0.0.1:${info.port}`,
            wsBaseUrl: `ws://127.0.0.1:${info.port}`,
            token: info.token || ''
          };
        }

        // 2. Vite Dev (with tauri:dev) or direct browser access: Same-origin relative path (handled by Vite Proxy)
        const wsProto = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = typeof window !== 'undefined' ? window.location.host : 'localhost:8000';
        return {
          baseUrl: '',
          wsBaseUrl: `${wsProto}//${host}`,
          token: ''
        };
      } catch (err) {
        invalidateBackendConfig();
        if (err instanceof BackendUnavailableError) throw err;
        throw new BackendUnavailableError(err instanceof Error ? err.message : String(err));
      }
    })();
  }

  return cachedConfigPromise;
}

export class ApiHttpError extends Error {
  public readonly status: number;

  constructor(status: number, path: string, body: string) {
    super(`API ${status} on ${path}: ${body}`);
    this.name = 'ApiHttpError';
    this.status = status;
  }
}

function isRetryableError(err: unknown, callerSignal?: AbortSignal): boolean {
  if (callerSignal?.aborted) {
    return false;
  }
  if (err instanceof ApiHttpError) {
    return err.status === 429 || err.status >= 500;
  }
  if (err && typeof err === 'object' && (err as { name?: string }).name === 'AbortError') {
    return false;
  }
  return true;
}

export interface ApiOptions extends RequestInit {
  timeoutMs?: number; // default: 10_000ms
  retry?: { max: number; baseMs: number; capMs: number };
}

export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { timeoutMs = 10_000, retry, ...init } = options;
  const cfg = await getBackendConfig();

  const headers = new Headers(init.headers);
  if (cfg.token) {
    headers.set('X-Terminal-Token', cfg.token);
  }
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    controller.abort(new Error(`API request timed out after ${timeoutMs}ms: ${path}`));
  }, timeoutMs);

  const onCallerAbort = () => {
    controller.abort(init.signal?.reason);
  };

  if (init.signal) {
    if (init.signal.aborted) {
      clearTimeout(timeoutId);
      throw init.signal.reason;
    }
    init.signal.addEventListener('abort', onCallerAbort, { once: true });
  }

  try {
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    clearTimeout(timeoutId);
    if (!res.ok) {
      throw new ApiHttpError(res.status, path, await res.text());
    }
    return (await res.json()) as T;
  } catch (err) {
    clearTimeout(timeoutId);
    if (retry && retry.max > 0 && isRetryableError(err, init.signal ?? undefined)) {
      const nextDelay = Math.min(retry.capMs, retry.baseMs);
      await new Promise((r) => setTimeout(r, nextDelay));
      if (init.signal?.aborted) {
        throw init.signal.reason;
      }
      return apiFetch<T>(path, {
        ...options,
        retry: { max: retry.max - 1, baseMs: retry.baseMs * 2, capMs: retry.capMs }
      });
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
    init.signal?.removeEventListener('abort', onCallerAbort);
  }
}

export async function openLiveWebSocket(path = '/ws/live'): Promise<WebSocket> {
  const cfg = await getBackendConfig();
  const sep = path.includes('?') ? '&' : '?';
  const query = cfg.token ? `${sep}token=${encodeURIComponent(cfg.token)}` : '';
  return new WebSocket(`${cfg.wsBaseUrl}${path}${query}`);
}

export type FeedState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface LiveMessage {
  type: string;
  symbol?: string;
  bar?: any;
  seq?: number;
  [key: string]: any;
}

export interface LiveFeedOptions {
  path?: string;
  heartbeatMs?: number;
  staleMs?: number;
  onGap?: (gap: { symbol: string; fromSeq: number; toSeq: number }) => void;
  onStateChange?: (state: FeedState) => void;
}

export class LiveFeed {
  private _state: FeedState = 'idle';
  private _options: LiveFeedOptions;
  private _ws: WebSocket | null = null;
  private _subscribers: Set<(m: LiveMessage) => void> = new Set();
  private _abortController: AbortController | null = null;
  private _reconnectTimer: any = null;
  private _staleTimer: any = null;
  private _retryCount = 0;
  private _lastSeqBySymbol: Map<string, number> = new Map();
  private _disposed = false;

  constructor(options: LiveFeedOptions = {}) {
    this._options = {
      path: '/ws/live',
      heartbeatMs: 15_000,
      staleMs: 30_000,
      ...options
    };
  }

  public get state(): FeedState {
    return this._state;
  }

  public start(signal?: AbortSignal): void {
    if (this._disposed || this._state === 'open' || this._state === 'connecting') return;
    this._disposed = false;
    this._abortController = new AbortController();

    if (signal) {
      signal.addEventListener('abort', () => this.stop());
    }

    this._connect();
  }

  public stop(): void {
    this._disposed = true;
    this._clearTimers();
    if (this._abortController) {
      this._abortController.abort();
      this._abortController = null;
    }
    if (this._ws) {
      this._ws.onopen = null;
      this._ws.onmessage = null;
      this._ws.onerror = null;
      this._ws.onclose = null;
      try {
        this._ws.close();
      } catch {
        // ignore
      }
      this._ws = null;
    }
    this._setState('closed');
  }

  public subscribe(cb: (m: LiveMessage) => void): () => void {
    this._subscribers.add(cb);
    return () => {
      this._subscribers.delete(cb);
    };
  }

  private _setState(state: FeedState): void {
    if (this._state !== state) {
      this._state = state;
      this._options.onStateChange?.(state);
    }
  }

  private async _connect(): Promise<void> {
    if (this._disposed) return;
    this._setState(this._retryCount > 0 ? 'reconnecting' : 'connecting');

    try {
      const cfg = await getBackendConfig();
      if (this._disposed) return;

      const path = this._options.path || '/ws/live';
      const sep = path.includes('?') ? '&' : '?';
      const query = cfg.token ? `${sep}token=${encodeURIComponent(cfg.token)}` : '';
      const url = `${cfg.wsBaseUrl}${path}${query}`;

      const ws = new WebSocket(url);
      this._ws = ws;

      ws.onopen = () => {
        if (this._disposed) {
          ws.close();
          return;
        }
        this._retryCount = 0;
        this._setState('open');
        this._resetStaleTimer();
      };

      ws.onmessage = (event) => {
        this._resetStaleTimer();
        try {
          const msg = JSON.parse(event.data) as LiveMessage;

          // Check sequence gap
          if (typeof msg.seq === 'number' && msg.symbol) {
            const lastSeq = this._lastSeqBySymbol.get(msg.symbol);
            if (lastSeq !== undefined && msg.seq > lastSeq + 1) {
              this._options.onGap?.({
                symbol: msg.symbol,
                fromSeq: lastSeq,
                toSeq: msg.seq
              });
            }
            this._lastSeqBySymbol.set(msg.symbol, msg.seq);
          }

          for (const cb of Array.from(this._subscribers)) {
            try {
              cb(msg);
            } catch (err) {
              console.error('[LiveFeed] Subscriber error', err);
            }
          }
        } catch (e) {
          console.warn('[LiveFeed] Failed to parse message', e);
        }
      };

      ws.onerror = (err) => {
        console.warn('[LiveFeed] WebSocket error', err);
      };

      ws.onclose = () => {
        this._ws = null;
        if (!this._disposed) {
          this._scheduleReconnect();
        }
      };
    } catch (err) {
      console.warn('[LiveFeed] Connection init failed', err);
      if (!this._disposed) {
        this._scheduleReconnect();
      }
    }
  }

  private _resetStaleTimer(): void {
    if (this._staleTimer) clearTimeout(this._staleTimer);
    const staleMs = this._options.staleMs || 30_000;
    this._staleTimer = setTimeout(() => {
      console.warn('[LiveFeed] Stale connection detected, forcing reconnect');
      if (this._ws) {
        try { this._ws.close(); } catch { /* ignore */ }
      }
    }, staleMs);
  }

  private _clearTimers(): void {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    if (this._staleTimer) {
      clearTimeout(this._staleTimer);
      this._staleTimer = null;
    }
  }

  private _scheduleReconnect(): void {
    this._clearTimers();
    this._setState('reconnecting');
    this._retryCount++;
    const baseMs = 1000;
    const maxMs = 15000;
    const delay = Math.min(maxMs, baseMs * Math.pow(1.5, Math.min(this._retryCount, 8)));
    const jitter = delay * (0.8 + Math.random() * 0.4);

    this._reconnectTimer = setTimeout(() => {
      if (!this._disposed) {
        this._connect();
      }
    }, jitter);
  }
}
