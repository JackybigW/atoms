import { createClient } from '@metagptx/web-sdk';

type SDKClient = ReturnType<typeof createClient>;

let _client: SDKClient | null = null;

function getClient(): SDKClient {
  if (!_client) {
    _client = createClient();
  }
  return _client;
}

// Call this after login or logout so the next SDK request picks up the
// current localStorage token instead of the stale one from app init.
export function resetClient(): void {
  _client = null;
}

// Proxy so all existing `client.xxx` call sites work without changes.
// Every property access re-routes through getClient(), which will create
// a fresh axios instance (with the current token) if resetClient() was called.
export const client = new Proxy({} as SDKClient, {
  get(_target, prop: string | symbol) {
    return (getClient() as Record<string | symbol, unknown>)[prop];
  },
});
