interface BackendConfig {
  baseUrl: string;
  wsBaseUrl: string;
  token: string;
}

let cachedConfig: BackendConfig | null = null;

function isTauriRuntime(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export async function getBackendConfig(): Promise<BackendConfig> {
  if (cachedConfig) return cachedConfig;

  // 1. Tauri Production / Desktop Mode: Retrieve sidecar dynamic port and token via Rust IPC
  if (isTauriRuntime() && !import.meta.env.DEV) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      // Support both get_session_info (existing lib.rs) and get_backend_config
      let info: { port: number; token: string; api_base?: string; ws_url?: string };
      try {
        info = await invoke<any>('get_session_info');
      } catch {
        info = await invoke<any>('get_backend_config');
      }
      cachedConfig = {
        baseUrl: `http://127.0.0.1:${info.port}`,
        wsBaseUrl: `ws://127.0.0.1:${info.port}`,
        token: info.token || ''
      };
      return cachedConfig;
    } catch (e) {
      console.warn('[getBackendConfig] Rust IPC invocation failed, falling back to window host', e);
    }
  }

  // 2. Vite Dev (with tauri:dev) or direct browser access: Same-origin relative path (handled by Vite Proxy)
  const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  cachedConfig = {
    baseUrl: '',
    wsBaseUrl: `${wsProto}//${window.location.host}`,
    token: ''
  };
  return cachedConfig;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const cfg = await getBackendConfig();
  const headers = new Headers(init.headers);
  if (cfg.token) {
    headers.set('X-Terminal-Token', cfg.token);
  }
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new Error(`API ${res.status} on ${path}: ${await res.text()}`);
  }
  return (await res.json()) as T;
}

export async function openLiveWebSocket(path = '/ws/live'): Promise<WebSocket> {
  const cfg = await getBackendConfig();
  const sep = path.includes('?') ? '&' : '?';
  const query = cfg.token ? `${sep}token=${encodeURIComponent(cfg.token)}` : '';
  return new WebSocket(`${cfg.wsBaseUrl}${path}${query}`);
}
