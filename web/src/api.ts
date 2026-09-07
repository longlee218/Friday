// One place that knows how to reach the API, so a route change is one edit.
//
// No client library and no generated code: every response here is plain JSON
// and `api-types.ts` says what shape it has. What keeps those types honest is
// `tests/test_web_contract.py`, on the Python side, because that is the side
// that can call the real converters.

import type {
  Board,
  ChannelContext,
  Flow,
  Memory,
  Room,
  Spend,
  TaskCalls,
} from "./api-types";

async function get<T>(path: string): Promise<T> {
  const answer = await fetch(path);
  if (!answer.ok) throw new Error(`${answer.status} from ${path}`);
  return (await answer.json()) as T;
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const answer = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!answer.ok) {
    // The API says something useful in `detail` — a 409 on a channel that
    // already has a file, a 422 naming the key whose value was not text.
    // Swallowing it would leave the page saying "something went wrong".
    const said = await answer.json().catch(() => null);
    throw new Error(said?.detail ?? `${answer.status} from ${path}`);
  }
  return (await answer.json()) as T;
}

export const api = {
  board: () => get<Board>("/api/board"),
  spend: () => get<Spend>("/api/spend"),
  taskCalls: (id: number) => get<TaskCalls>(`/api/tasks/${id}/calls`),
  conversations: () => get<Room[]>("/api/conversations"),
  messagesIn: (limit = 200) => get<Board["messages"]>(`/api/messages?limit=${limit}`),
  memories: (channelId: string) =>
    get<Memory[]>(`/api/channels/${encodeURIComponent(channelId)}/memories`),
  rename: (conversation: string, name: string) =>
    send<{ name: string | null }>(
      // Not encoded: a conversation id carries a `/` when it names a thread,
      // and the route's `:path` segment is what accepts it.
      `/api/conversations/${conversation}/name`,
      "PUT",
      { name },
    ),
  flow: (provider: string, id: string) =>
    get<Flow>(`/api/messages/${provider}/${encodeURIComponent(id)}/flow`),
  channels: () => get<string[]>("/api/channels"),
  context: (id: string) =>
    get<ChannelContext>(`/api/channels/${encodeURIComponent(id)}/context`),
  createContext: (id: string) =>
    send<{ created: boolean }>(
      `/api/channels/${encodeURIComponent(id)}/context`,
      "POST",
    ),
  setOverrides: (id: string, overrides: Record<string, string>) =>
    send<{ saved: boolean }>(
      `/api/channels/${encodeURIComponent(id)}/context/overrides`,
      "PUT",
      { overrides },
    ),
  reload: () =>
    send<{ reloaded: string[]; problems: string[] }>(
      "/api/context/reload",
      "POST",
    ),
};
