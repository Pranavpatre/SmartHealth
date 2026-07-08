import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { db, type CachedMedicine } from '../db/localDb'
import { useAuthStore } from '../stores/authStore'
import { useVoiceInput, parseSpokenNumber, VOICE_LANG_MAP } from '../hooks/useVoiceInput'
import { syncPendingData, fetchAndCacheMedicines } from '../sync/syncService'
import InfoNote from '../components/InfoNote'
import VoiceRecordingBanner from '../components/VoiceRecordingBanner'
import clsx from 'clsx'

function generateClientId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

interface MedicineRow extends CachedMedicine {
  qty: number
}

export default function StockEntryPage() {
  const { t } = useTranslation()
  const { facilityId, languagePref } = useAuthStore()
  const [medicines, setMedicines] = useState<MedicineRow[]>([])
  const [loading, setLoading] = useState(true)
  const [pendingCount, setPendingCount] = useState(0)
  const [savedIds, setSavedIds] = useState<Set<number>>(new Set())
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)
  const [voiceTarget, setVoiceTarget] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [catFilter, setCatFilter] = useState('ALL')

  const { isListening, transcript, error: voiceError, startListening, stopListening, reset: resetVoice } =
    useVoiceInput(VOICE_LANG_MAP[languagePref] || 'en-IN')

  const loadMedicines = useCallback(async () => {
    // Medicines are only ever read from the local cache here — nothing
    // previously populated it, so it stayed empty even when online. Fetch
    // from the server first (a no-op if offline), then read the cache.
    await fetchAndCacheMedicines()
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
    // Offline-first: always queue locally, then flush immediately if online —
    // same contract as the daily-entry saves, so this doesn't sit as
    // "pending" until a manual Sync Now tap.
    if (navigator.onLine) await syncPendingData()
    refreshPending()
  }

  const handleSync = async () => {
    if (!navigator.onLine) {
      setSyncMsg(t('sync.noInternet'))
      setTimeout(() => setSyncMsg(null), 3000)
      return
    }
    setSyncing(true)
    const result = await syncPendingData()
    setSyncing(false)
    setSyncMsg(
      t('sync.result', {
        synced: result.synced,
        errorsSuffix: result.errors > 0 ? `, ${result.errors} failed` : '',
      }),
    )
    setTimeout(() => setSyncMsg(null), 4000)
    refreshPending()
  }

  const handleVoiceForMedicine = (id: number) => {
    setVoiceTarget(id)
    startListening()
  }

  const allCategories = ['ALL', ...Array.from(new Set(medicines.map((m) => m.category))).sort()]
  const filtered = medicines.filter((m) =>
    (catFilter === 'ALL' || m.category === catFilter) &&
    m.name.toLowerCase().includes(search.trim().toLowerCase()),
  )

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500">{t('stock.loading')}</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 p-4 space-y-4 max-w-lg mx-auto">
      <VoiceRecordingBanner show={isListening} label={t('voice.recording')} />
      {/* Header */}
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-xl font-bold text-teal-600">{t('stock.title')}</h1>
        {pendingCount > 0 && (
          <span className="bg-orange-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
            {pendingCount} {t('daily.pending')}
          </span>
        )}
      </div>
      <InfoNote>{t('info.stock')}</InfoNote>

      {voiceError && (
        <div className="bg-red-50 text-red-600 text-sm rounded-xl px-4 py-2">{voiceError}</div>
      )}
      {isListening && voiceTarget !== null && (
        <div className="bg-blue-50 text-blue-600 text-sm rounded-xl px-4 py-2 animate-pulse">
          {t('stock.listening')}
        </div>
      )}

      {medicines.length === 0 ? (
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 text-center">
          <p className="text-gray-400 text-sm">{t('stock.noneCached')}</p>
        </div>
      ) : (
        <>
          {/* Search + category filter (flat table scales better than a long
              scrolling list as the medicine catalogue grows) */}
          <div className="flex gap-2">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('stock.searchPlaceholder')}
              className="flex-1 min-w-0 border-2 border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-teal-500 transition-colors"
            />
            <select
              value={catFilter}
              onChange={(e) => setCatFilter(e.target.value)}
              className="shrink-0 border-2 border-gray-200 rounded-xl px-2 py-2.5 text-sm bg-white focus:outline-none focus:border-teal-500"
            >
              {allCategories.map((c) => (
                <option key={c} value={c}>{c === 'ALL' ? t('status.all', 'All') : t(`category.${c}`, c)}</option>
              ))}
            </select>
          </div>

          {filtered.length === 0 ? (
            <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 text-center">
              <p className="text-gray-400 text-sm">{t('stock.noMatch', { search })}</p>
            </div>
          ) : (
            <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden divide-y divide-gray-50">
              {filtered.map((medicine) => (
                <div key={medicine.id} className="flex items-center gap-1.5 px-3 py-2.5">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate">{medicine.name}</p>
                    <p className="text-xs text-gray-400">
                      {t(`category.${medicine.category}`, medicine.category)} · {t('stock.reorderLevel', { level: medicine.reorder_level, unit: medicine.unit })}
                    </p>
                  </div>
                  <button onClick={() => handleQtyChange(medicine.id, -1)}
                    className="w-8 h-8 shrink-0 rounded-full bg-gray-100 text-gray-700 font-bold hover:bg-gray-200 transition-colors">−</button>
                  <input type="number" min="0" value={medicine.qty || ''}
                    onChange={(e) => handleQtyInput(medicine.id, e.target.value)} placeholder="0"
                    className="w-14 shrink-0 text-center text-sm font-bold border-2 border-gray-200 rounded-lg py-1.5 focus:outline-none focus:border-teal-500 transition-colors" />
                  <button onClick={() => handleQtyChange(medicine.id, 1)}
                    className="w-8 h-8 shrink-0 rounded-full bg-gray-100 text-gray-700 font-bold hover:bg-gray-200 transition-colors">+</button>
                  <button onPointerDown={() => handleVoiceForMedicine(medicine.id)} onPointerUp={stopListening}
                    className={clsx('w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm shadow transition-all',
                      isListening && voiceTarget === medicine.id ? 'bg-red-500 text-white animate-pulse' : 'bg-teal-600 text-white hover:bg-teal-700')}
                    title={t('stock.voiceHint')}>🎤</button>
                  <button onClick={() => handleSave(medicine)} disabled={medicine.qty === 0} title={t('stock.saveUpdate')}
                    className={clsx('w-8 h-8 shrink-0 rounded-full flex items-center justify-center font-bold transition-colors disabled:opacity-30',
                      savedIds.has(medicine.id) ? 'bg-green-100 text-green-700' : 'bg-teal-600 text-white hover:bg-teal-700')}>✓</button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Sync */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-4">
        <button
          onClick={handleSync}
          disabled={syncing || pendingCount === 0}
          className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold disabled:opacity-40 hover:bg-blue-700 transition-colors"
        >
          {syncing ? t('sync.syncing') : `${t('sync.now')}${pendingCount > 0 ? ` (${pendingCount})` : ''}`}
        </button>
        {syncMsg && <p className="text-sm text-gray-600 mt-2 text-center">{syncMsg}</p>}
      </div>
    </div>
  )
}
