"use client";

import { API_BASE } from "./api";

/**
 * Thin client for the /business/{resource} API.
 *
 * The server already exposes a writable spec per resource (GET
 * /business/resources) and scopes every row to the session's tenant, so this
 * file deliberately holds no tenant id and no shape of its own.
 */

export type Row = Record<string, unknown> & { id?: string };

async function call<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** The API wraps collections as { resource, count, items }. */
interface Collection {
  resource: string;
  count: number;
  items: Row[];
}

export const listResource = async (resource: string): Promise<Row[] | null> => {
  const body = await call<Collection>(`/business/${resource}`);
  return body ? (body.items ?? []) : null;
};

export const createResource = (resource: string, body: Row) =>
  call<Row>(`/business/${resource}`, { method: "POST", body: JSON.stringify(body) });

export const deleteResource = (resource: string, id: string) =>
  call<unknown>(`/business/${resource}/${id}`, { method: "DELETE" });
