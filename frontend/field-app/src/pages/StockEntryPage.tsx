import { useState, useEffect, useCallback } from 'react'
import { db, type CachedMedicine } from '../db/localDb'
import { useAuthStore } from '../stores/authStore'
import { useVoiceInput, parseSpokenNumber } from '../hooks/useVoiceInput'
import { syncPendingData } from '../sync/syncService'
import clsx from 'clsx'

function generateClientId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

interface MedicineRow extends CachedMedicine {
  qty: number
}

export default function StockEntryPage() {
  const { facilityId } = useAuthStore()
  const [medicines, setMedicines] = useState<MedicineRow[]>([])
  const [loading, setLoading] = useState(true)
  const [pendingCount, setPendingCount] = useState(0)
  const [savedIds, setSavedIds] = useState<Set<number>>(new Set())
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)
  const [voiceTarget, setVoiceTarget] = useState<number | null>(null)

  const { isListening, transcript, error: voiceError, startListening, stopListening, reset: resetVoice } =
    useVoiceInput('hi-IN')

  const loadMedicines = useCallback(async () => {
    const meds = await db.medicines.toArray()
    setMedicines(meds.map((m) => ({ ...m, qty: 0 })))
    setLoading(false)
  }, [])

  const refreshPending = useCallback(async () => {
    const count = await db.pendingStockUpdates.where('synced').equals(0).count()
    setPendingCount(count)
  }, [])

  useEffect(() => {
    loadMedicines()
    refreshPending()
  }, [loadMedicines, refreshPending])

  // Parse voice transcript for stock
  useEffect(() => {
    if (!transcript || voiceTarget === null) return
    const parsed = parseSpokenNumber(transcript)
    if (parsed !== null) {
      setMedicines((prev) =>
        prev.map((m) => (m.id === voiceTarget ? { ...m, qty: parsed } : m)),
      )
    }
    resetVoice()
    setVoiceTarget(null)
  }, [transcript, voiceTarget, resetVoice])

  const handleQtyChange = (id: number, delta: number) => {
    setMedicines((prev) =>
      prev.map((m) => (m.id === id ? { ...m, qty: Math.max(0, m.qty + delta) } : m)),
    )
  }

  const handleQtyInput = (id: number, val: string) => {
    const n = parseInt(val, 10)
    if (!isNaN(n) && n >= 0) {
      setMedicines((prev) => prev.map((m) => (m.id === id ? { ...m, qty: n } : m)))
    }
  }

  const handleSave = async (medicine: MedicineRow) => {
    if (!facilityId || medicine.qty === 0) return
    await db.pendingStockUpdates.add({
      facility_id: facilityId,
      medicine_id: medicine.id,
      quantity_change: medicine.qty,
      reason: 'field_entry',
      recorded_at: new Date().toISOString(),
      client_id: generateClientId(),
      synced: false,
    })
    setSavedIds((prev) => new Set(prev).add(medicine.id))
    setTimeout(() => setSavedIds((prev) => { const s = new Set(prev); s.delete(medicine.id); return s }), 3000)
    setMedicines((prev) => prev.map((m) => (m.id === medicine.id ? { ...m, qty: 0 } : m)))
    refreshPending()
  }

  const handleSync = async () => {
    if (!navigator.onLine) {
      setSyncMsg('No internet connection')
      setTimeout(() => setSyncMsg(null), 3000)
      return
    }
    setSyncing(true)
    const result = await syncPendingData()
    setSyncing(false)
    setSyncMsg(`Synced ${result.synced} record(s)${result.errors > 0 ? `, ${result.errors} failed` : ''}`)
    setTimeout(() => setSyncMsg(null), 4000)
    refreshPending()
  }

  const handleVoiceForMedicine = (id: number) => {
    setVoiceTarget(id)
    startListening()
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500">Loading medicines…</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 p-4 space-y-4 max-w-lg mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-xl font-bold text-teal-600">Stock Update</h1>
        {pendingCount > 0 && (
          <span className="bg-orange-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
            {pendingCount} pending
          </span>
        )}
      </div>

      {voiceError && (
        <div className="bg-red-50 text-red-600 text-sm rounded-xl px-4 py-2">{voiceError}</div>
      )}
      {isListening && voiceTarget !== null && (
        <div className="bg-blue-50 text-blue-600 text-sm rounded-xl px-4 py-2 animate-pulse">
          Listening for quantity…
        </div>
      )}

      {medicines.length === 0 ? (
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 text-center">
          <p className="text-gray-400 text-sm">No medicines cached. Connect to internet to load.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {medicines.map((medicine) => (
            <div
              key={medicine.id}
              className="bg-white rounded-2xl shadow-sm border border-gray-100 p-4 space-y-3"
            >
              <div className="flex items-start justify-between">
                <div>
                  <p className="font-semibold text-gray-800">{medicine.name}</p>
                  <p className="text-xs text-gray-400">
                    Reorder level: {medicine.reorder_level} {medicine.unit}
                  </p>
                </div>
                {savedIds.has(medicine.id) && (
                  <span className="text-green-600 text-sm font-medium">Saved ✓</span>
                )}
              </div>

              <div className="flex items-center gap-2">
                {/* Decrement */}
                <button
                  onClick={() => handleQtyChange(medicine.id, -1)}
                  className="w-10 h-10 rounded-full bg-gray-100 text-gray-700 font-bold text-lg hover:bg-gray-200 transition-colors"
                >
                  −
                </button>

                {/* Input */}
                <input
                  type="number"
                  min="0"
                  value={medicine.qty || ''}
                  onChange={(e) => handleQtyInput(medicine.id, e.target.value)}
                  placeholder="0"
                  className="flex-1 text-center text-xl font-bold border-2 border-gray-200 rounded-xl py-2 focus:outline-none focus:border-teal-500 transition-colors"
                />

                {/* Increment */}
                <button
                  onClick={() => handleQtyChange(medicine.id, 1)}
                  className="w-10 h-10 rounded-full bg-gray-100 text-gray-700 font-bold text-lg hover:bg-gray-200 transition-colors"
                >
                  +
                </button>

                {/* Voice */}
                <button
                  onPointerDown={() => handleVoiceForMedicine(medicine.id)}
                  onPointerUp={stopListening}
                  className={clsx(
                    'w-10 h-10 rounded-full flex items-center justify-center text-lg shadow transition-all',
                    isListening && voiceTarget === medicine.id
                      ? 'bg-red-500 text-white animate-pulse'
                      : 'bg-teal-600 text-white hover:bg-teal-700',
                  )}
                  title="Hold to speak quantity"
                >
                  🎤
                </button>
              </div>

              <button
                onClick={() => handleSave(medicine)}
                disabled={medicine.qty === 0}
                className="w-full py-2.5 rounded-xl bg-teal-600 text-white font-semibold text-sm disabled:opacity-40 hover:bg-teal-700 transition-colors"
              >
                Save Update
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Sync */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-4">
        <button
          onClick={handleSync}
          disabled={syncing || pendingCount === 0}
          className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold disabled:opacity-40 hover:bg-blue-700 transition-colors"
        >
          {syncing ? 'Syncing…' : `Sync Now${pendingCount > 0 ? ` (${pendingCount})` : ''}`}
        </button>
        {syncMsg && <p className="text-sm text-gray-600 mt-2 text-center">{syncMsg}</p>}
      </div>
    </div>
  )
}
