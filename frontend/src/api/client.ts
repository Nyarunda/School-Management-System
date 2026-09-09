const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");
const TOKEN_KEY = "school-erp-token";
const TENANT_KEY = "school-erp-tenant";

export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };
export type FieldErrors = Record<string, string[]>;

export class ApiError extends Error {
  constructor(public status: number, public data: unknown) {
    super(readError(data) || `Request failed (${status})`);
  }
  get fieldErrors(): FieldErrors {
    if (!this.data || typeof this.data !== "object" || Array.isArray(this.data)) return {};
    return Object.fromEntries(Object.entries(this.data as Record<string, unknown>).filter(([key]) => key !== "detail").map(([key,value]) => [key, Array.isArray(value) ? value.map(String) : [String(value)]]));
  }
}

export function readError(data: unknown): string {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return "";
  const record = data as Record<string, unknown>;
  if (typeof record.detail === "string") return record.detail;
  if (Array.isArray(record.detail)) return record.detail.join(" ");
  return Object.entries(record).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(" ") : String(value)}`).join(" · ");
}

export const authStore = {
  token: () => localStorage.getItem(TOKEN_KEY), tenant: () => localStorage.getItem(TENANT_KEY),
  setToken: (value: string | null) => value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY),
  setTenant: (value: string | null) => value ? localStorage.setItem(TENANT_KEY, value) : localStorage.removeItem(TENANT_KEY),
};

export type ApiRequestOptions = RequestInit & { tenant?: string | false; params?: Record<string, string | number | boolean | undefined> };

function buildUrl(path: string, params?: ApiRequestOptions["params"]): URL {
  const url = new URL(`${API_BASE}/${path.replace(/^\//, "")}`, window.location.origin);
  Object.entries(params ?? {}).forEach(([key, value]) => value !== undefined && url.searchParams.set(key, String(value)));
  return url;
}

function buildHeaders(options: ApiRequestOptions): Headers {
  const headers = new Headers(options.headers);
  const token = authStore.token();
  if (token) headers.set("Authorization", `Token ${token}`);
  const tenant = options.tenant === false ? null : options.tenant ?? authStore.tenant();
  if (tenant) headers.set("X-Tenant-Slug", tenant);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  return headers;
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const url = buildUrl(path, options.params);
  const headers = buildHeaders(options);
  const response = await fetch(url.pathname + url.search, { ...options, headers });
  if (response.status === 204) return undefined as T;
  const data: unknown = (response.headers.get("content-type") ?? "").includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new ApiError(response.status, data);
  return data as T;
}
export const api = apiRequest;

function filenameFromContentDisposition(value: string | null): string {
  const match = value?.match(/filename="?([^";]+)"?/i);
  return match?.[1] ?? "download";
}

/** For streamed, authenticated binary responses (document/report file downloads) --
 * apiRequest can't be reused here since it always parses the body as JSON/text. */
export async function apiDownload(path: string, options: ApiRequestOptions = {}): Promise<{ blob: Blob; filename: string }> {
  const url = buildUrl(path, options.params);
  const headers = buildHeaders(options);
  const response = await fetch(url.pathname + url.search, { ...options, headers });
  if (!response.ok) {
    const data: unknown = (response.headers.get("content-type") ?? "").includes("json") ? await response.json() : await response.text();
    throw new ApiError(response.status, data);
  }
  const blob = await response.blob();
  return { blob, filename: filenameFromContentDisposition(response.headers.get("content-disposition")) };
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
