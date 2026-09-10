import { vi } from "vitest";

/** Every test starts from "backend unreachable": 404 for everything. */
export const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
  Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }),
);

/** happy-dom has no window.confirm; the delete tests decide per case. */
export const confirmMock = vi.fn((_message?: string) => true);

/** fetch accepts a string, a URL or a Request. The store only ever passes
 *  strings, but a test that stringifies the argument must honour the type. */
export function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

/** Every fetch so far, as [url, init]. */
export function fetchCalls(): [string, RequestInit | undefined][] {
  return fetchMock.mock.calls.map(([input, init]) => [requestUrl(input), init]);
}
