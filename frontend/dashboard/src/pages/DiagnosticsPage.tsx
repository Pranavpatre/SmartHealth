import { useEffect, useState, useCallback } from 'react'
import { useAuthStore } from '../stores/authStore'
import { apiBaseUrl } from '../api/client'
import {
  getDebug,
  clearDebug,
  subscribeDebug,
  type DebugEntry,
} from '../lib/debugLog'

// Operator-facing debug/diagnostics view. Intentionally English-only and
// outside the i18n parity set — it's a technical tool, not end-user copy.

type Health = { state: 'checking' | 'ok' | 'fail'; latencyMs?: number; detail?: string }

function decodeJwt(token: string | null): Record<string, unknown> | null {
  if (!token) return null
  try {
    const payload = token.split('.')[1]
    return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')))
  } catch {
    return null
  }
}

function fmtTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString(undefined, { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

export default function DiagnosticsPage() {
  const { token, name, role, userId, facilityId } = useAuthStore()
  const [entries, setEntries] = useState<DebugEntry[]>(getDebug())
  const [health, setHealth] = useState<Health>({ state: 'checking' })
  const [filter, setFilter] = useState<'all' | 'errors'>('all')

  useEffect(() => subscribeDebug(() => setEntries([...getDebug()])), [])

  const pingHealth = useCallback(async () => {
    setHealth({ state: 'checking' })
    const start = Date.now()
    try {
      const res = await fetch(`${apiBaseUrl}/health`, { cache: 'no-store' })
      const latencyMs = Date.now() - start
      if (res.ok) setHealth({ state: 'ok', latencyMs })
      else setHealth({ state: 'fail', latencyMs, detail: `HTTP ${res.status}` })
    } catch (e) {
      setHealth({ state: 'fail', detail: (e as Error).message })
    }
  }, [])

  useEffect(() => {
    pingHealth()
  }, [pingHealth])

  const claims = decodeJwt(token)
  const exp = claims?.exp ? new Date((claims.exp as number) * 1000) : null
  const expired = exp ? exp.getTime() < Date.now() : false

  const shown = filter === 'errors' ? entries.filter((e) => e.kind === 'error') : entries

  const copyAll = () => {
    const text = entries
      .map((e) => `${fmtTime(e.ts)} ${e.kind.toUpperCase()} ${e.method || ''} ${e.url || ''} ${e.status ?? ''} ${e.durationMs ?? ''}ms ${e.message || ''}`.trim())
      .join('\n')
    navigator.clipboard?.writeText(text)
  }

  const Row = ({ label, value, ok }: { label: string; value: React.ReactNode; ok?: boolean }) => (
    <div className="flex items-start justify-between gap-4 py-1.5 border-b border-gray-100 last:border-0">
      <span className="text-xs font-medium text-gray-500">{label}</span>
      <span className={`text-xs font-mono text-right break-all ${ok === false ? 'text-red-600' : 'text-gray-800'}`}>{value}</span>
    </div>
  )

  const healthBadge =
    health.state === 'ok' ? (
      <span className="text-green-600">● reachable {health.latencyMs}ms</span>
    ) : health.state === 'fail' ? (
      <span className="text-red-600">● unreachable {health.detail}</span>
    ) : (
      <span className="text-gray-400">● checking…</span>
    )

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-800">Diagnostics</h1>
        <button
          onClick={pingHealth}
          className="text-xs font-semibold px-3 py-1.5 rounded-md border border-gray-200 bg-white text-gray-700 hover:bg-gray-50"
        >
          Re-check
        </button>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Environment / backend */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-2">Backend</h2>
          <Row label="API base URL" value={apiBaseUrl} />
          <Row label="Health" value={healthBadge} ok={health.state !== 'fail'} />
          <Row label="Browser online" value={navigator.onLine ? 'yes' : 'no'} ok={navigator.onLine} />
          <Row label="Build mode" value={import.meta.env.MODE} />
        </div>

        {/* Session */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-2">Session</h2>
          <Row label="Name" value={name || '—'} />
          <Row label="Role" value={role || '—'} />
          <Row label="User ID" value={userId || '—'} />
          <Row label="Facility ID" value={facilityId || '(national/district scope)'} />
          <Row
            label="Token expires"
            value={exp ? `${exp.toLocaleTimeString(undefined, { hour12: false })}${expired ? ' (EXPIRED)' : ''}` : '—'}
            ok={!expired}
          />
        </div>
      </div>

      {/* API log */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-700">
            Recent API activity <span className="text-gray-400 font-normal">({shown.length})</span>
          </h2>
          <div className="flex items-center gap-2">
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as 'all' | 'errors')}
              className="text-xs font-semibold px-2 py-1 rounded-md border border-gray-200 bg-gray-50 text-gray-700"
            >
              <option value="all">All</option>
              <option value="errors">Errors only</option>
            </select>
            <button onClick={copyAll} className="text-xs font-semibold px-2.5 py-1 rounded-md border border-gray-200 bg-white text-gray-700 hover:bg-gray-50">Copy</button>
            <button onClick={clearDebug} className="text-xs font-semibold px-2.5 py-1 rounded-md border border-red-200 bg-white text-red-600 hover:bg-red-50">Clear</button>
          </div>
        </div>

        {shown.length === 0 ? (
          <p className="text-xs text-gray-400 py-6 text-center">No activity recorded yet. Navigate the dashboard and it will appear here.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-gray-400 text-left border-b border-gray-100">
                  <th className="py-1 pr-3 font-medium">Time</th>
                  <th className="py-1 pr-3 font-medium">Method</th>
                  <th className="py-1 pr-3 font-medium">Endpoint</th>
                  <th className="py-1 pr-3 font-medium text-right">Status</th>
                  <th className="py-1 pr-3 font-medium text-right">ms</th>
                  <th className="py-1 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((e) => (
                  <tr key={e.id} className={`border-b border-gray-50 ${e.kind === 'error' ? 'bg-red-50/50' : ''}`}>
                    <td className="py-1 pr-3 text-gray-500 whitespace-nowrap">{fmtTime(e.ts)}</td>
                    <td className="py-1 pr-3 text-gray-700">{e.method || ''}</td>
                    <td className="py-1 pr-3 text-gray-800 break-all">{e.url || ''}</td>
                    <td className={`py-1 pr-3 text-right ${e.status && e.status >= 400 ? 'text-red-600' : 'text-green-600'}`}>{e.status ?? ''}</td>
                    <td className="py-1 pr-3 text-right text-gray-500">{e.durationMs ?? ''}</td>
                    <td className="py-1 text-red-600 break-all">{e.message || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
