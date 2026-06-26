import axios from 'axios'
import { db } from '../db/localDb'
import { useAuthStore } from '../stores/authStore'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export async function syncPendingData(): Promise<{ synced: number; errors: number }> {
  const token = useAuthStore.getState().token
  if (!token || !navigator.onLine) return { synced: 0, errors: 0 }

  const client = axios.create({
    baseURL: `${API_URL}/api/v1`,
    headers: { Authorization: `Bearer ${token}` },
  })

  const [stockUpdates, footfall, attendance] = await Promise.all([
    db.pendingStockUpdates.where('synced').equals(0).toArray(),
    db.pendingFootfall.where('synced').equals(0).toArray(),
    db.pendingAttendance.where('synced').equals(0).toArray(),
  ])

  if (!stockUpdates.length && !footfall.length && !attendance.length) {
    return { synced: 0, errors: 0 }
  }

  let synced = 0
  let errors = 0

  try {
    const payload = {
      stock_updates: stockUpdates.map(({ id: _id, synced: _s, ...rest }) => rest),
      footfall: footfall.map(({ id: _id, synced: _s, ...rest }) => rest),
      attendance: attendance.map(({ id: _id, synced: _s, ...rest }) => rest),
      last_sync_at: new Date().toISOString(),
    }

    const { data } = await client.post('/sync/push', payload)
    synced = data.accepted ?? stockUpdates.length + footfall.length + attendance.length

    // Mark as synced in local DB
    await Promise.all([
      ...stockUpdates.map((r) =>
        db.pendingStockUpdates.update(r.id!, { synced: true as unknown as boolean }),
      ),
      ...footfall.map((r) =>
        db.pendingFootfall.update(r.id!, { synced: true as unknown as boolean }),
      ),
      ...attendance.map((r) =>
        db.pendingAttendance.update(r.id!, { synced: true as unknown as boolean }),
      ),
    ])
  } catch {
    errors = stockUpdates.length + footfall.length + attendance.length
  }

  return { synced, errors }
}

export async function fetchAndCacheMedicines(): Promise<void> {
  const token = useAuthStore.getState().token
  if (!token || !navigator.onLine) return
  try {
    const { data } = await axios.get(`${API_URL}/api/v1/medicines`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const medicines = Array.isArray(data) ? data : (data?.medicines ?? [])
    await db.medicines.bulkPut(medicines)
  } catch {
    /* silent — offline or server unavailable */
  }
}

export async function fetchAndCacheNotifications(): Promise<void> {
  const token = useAuthStore.getState().token
  if (!token || !navigator.onLine) return
  try {
    const { data } = await axios.get(`${API_URL}/api/v1/notifications`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const notifications = Array.isArray(data) ? data : (data?.notifications ?? [])
    // Preserve local read status
    const existing = await db.notifications.toArray()
    const readSet = new Set(existing.filter((n) => n.read).map((n) => n.id))
    const merged = notifications.map((n: { id: string; read: boolean }) => ({
      ...n,
      read: readSet.has(n.id) ? true : n.read,
    }))
    await db.notifications.bulkPut(merged)
  } catch {
    /* silent */
  }
}
