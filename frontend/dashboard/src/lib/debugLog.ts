// Lightweight client-side debug log for the admin dashboard.
//
// The axios interceptors (api/client.ts) push an entry for every API call and
// error into a bounded ring buffer that is mirrored to localStorage, so the
// Diagnostics page can show recent network activity/errors even after a reload.
// This is a debugging aid for operators/demos — not user-facing telemetry.

export type DebugKind = 'request' | 'response' | 'error' | 'info'

export interface DebugEntry {
  id: string
  ts: number
  kind: DebugKind
  method?: string
  url?: string
  status?: number
  durationMs?: number
  message?: string
}

const MAX = 200
const STORAGE_KEY = 'predicare-debug-log'

function load(): DebugEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as DebugEntry[]) : []
  } catch {
    return []
  }
}

let entries: DebugEntry[] = load()
let listeners: Array<() => void> = []

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX)))
  } catch {
    /* quota / disabled storage — keep in memory only */
  }
}

function emit() {
  listeners.forEach((l) => l())
}

export function addDebug(entry: Omit<DebugEntry, 'id' | 'ts'>): void {
  entries.unshift({
    ...entry,
    id: Math.random().toString(36).slice(2),
    ts: Date.now(),
  })
  if (entries.length > MAX) entries = entries.slice(0, MAX)
  persist()
  emit()
}

export function getDebug(): DebugEntry[] {
  return entries
}

export function clearDebug(): void {
  entries = []
  persist()
  emit()
}

export function subscribeDebug(listener: () => void): () => void {
  listeners.push(listener)
  return () => {
    listeners = listeners.filter((l) => l !== listener)
  }
}
