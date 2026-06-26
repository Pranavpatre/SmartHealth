import Dexie, { type EntityTable } from 'dexie'

interface PendingStockUpdate {
  id?: number
  facility_id: string
  medicine_id: number
  quantity_change: number
  reason: string
  recorded_at: string
  client_id: string
  synced: boolean
}

interface PendingFootfall {
  id?: number
  facility_id: string
  date: string
  footfall_count: number
  recorded_at: string
  client_id: string
  synced: boolean
}

interface PendingAttendance {
  id?: number
  facility_id: string
  user_id: string
  date: string
  present: boolean
  recorded_at: string
  client_id: string
  synced: boolean
}

interface CachedMedicine {
  id: number
  name: string
  reorder_level: number
  unit: string
}

interface CachedNotification {
  id: string
  title: string
  body: string
  created_at: string
  read: boolean
}

class SmartHealthDB extends Dexie {
  pendingStockUpdates!: EntityTable<PendingStockUpdate, 'id'>
  pendingFootfall!: EntityTable<PendingFootfall, 'id'>
  pendingAttendance!: EntityTable<PendingAttendance, 'id'>
  medicines!: EntityTable<CachedMedicine, 'id'>
  notifications!: EntityTable<CachedNotification, 'id'>

  constructor() {
    super('smarthealth')
    this.version(1).stores({
      pendingStockUpdates: '++id, facility_id, synced',
      pendingFootfall: '++id, facility_id, date, synced',
      pendingAttendance: '++id, facility_id, date, synced',
      medicines: 'id',
      notifications: 'id, read',
    })
  }
}

export const db = new SmartHealthDB()
export type { PendingStockUpdate, PendingFootfall, PendingAttendance, CachedMedicine }
