import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { useAuthStore } from '../stores/authStore'
import { queueLedger, syncPendingData } from '../sync/syncService'
import InfoNote from './InfoNote'

// Bed matrix + test availability — daily field-worker inputs (in the Daily Entry
// tab). Beds also capture an "occupied until" date so the admin dashboard can
// project future availability. Offline-first (queued + synced like the rest).
const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export default function BedTestEntry() {
  const { t } = useTranslation()
  const { facilityId, token } = useAuthStore()
  const authHdr = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }

  const [beds, setBeds] = useState<{ bed_type: string; total_beds: number; occupied_beds: number; occupied_until: string | null }[]>([])
  const [bedsSaved, setBedsSaved] = useState(false)
  const [tests, setTests] = useState<{ test_id: number; test_name: string | null; available: boolean }[]>([])
  const [testsSaved, setTestsSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!facilityId || !token) return
    setLoading(true)
    Promise.all([
      fetch(`${API}/api/v1/ledger/beds/${facilityId}`, { headers: authHdr })
        .then((r) => (r.ok ? r.json() : null)).then((d) => d && setBeds(d.beds)).catch(() => {}),
      fetch(`${API}/api/v1/ledger/tests/${facilityId}`, { headers: authHdr })
        .then((r) => (r.ok ? r.json() : null)).then((d) => d && setTests(d.tests)).catch(() => {}),
    ]).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facilityId, token])

  const setOccupied = (bedType: string, delta: number) =>
    setBeds((prev) => prev.map((b) => b.bed_type === bedType
      ? { ...b, occupied_beds: Math.max(0, Math.min(b.total_beds, b.occupied_beds + delta)) } : b))

  const setOccupiedUntil = (bedType: string, value: string) =>
    setBeds((prev) => prev.map((b) => b.bed_type === bedType
      ? { ...b, occupied_until: value || null } : b))

  const saveBeds = async () => {
    if (!facilityId) return
    await queueLedger('beds', facilityId, beds)
    setBedsSaved(true); setTimeout(() => setBedsSaved(false), 3000)
    if (navigator.onLine) await syncPendingData()
  }

  const toggleTest = (testId: number) =>
    setTests((prev) => prev.map((tst) => tst.test_id === testId ? { ...tst, available: !tst.available } : tst))

  const saveTests = async () => {
    if (!facilityId) return
    await queueLedger('tests', facilityId, tests)
    setTestsSaved(true); setTimeout(() => setTestsSaved(false), 3000)
    if (navigator.onLine) await syncPendingData()
  }

  return (
    <>
      {/* Bed Matrix */}
      <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-3">
        <h2 className="text-base font-semibold text-gray-800">{t('beds.title')}</h2>
        <InfoNote>{t('info.beds')}</InfoNote>
        {loading && beds.length === 0 ? (
          <p className="text-sm text-gray-400">…</p>
        ) : beds.map((b) => (
          <div key={b.bed_type} className="space-y-1.5 border-b border-gray-50 pb-3 last:border-0">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-800">{b.bed_type}</p>
                <p className="text-xs text-gray-400">{b.occupied_beds} / {b.total_beds} {t('beds.occupied', 'occupied')} · {Math.max(0, b.total_beds - b.occupied_beds)} {t('beds.empty', 'empty')}</p>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={() => setOccupied(b.bed_type, -1)} disabled={b.total_beds === 0}
                  className="w-9 h-9 rounded-lg bg-gray-100 text-gray-700 font-bold text-lg disabled:opacity-30">−</button>
                <span className="w-8 text-center font-bold text-gray-900">{b.occupied_beds}</span>
                <button onClick={() => setOccupied(b.bed_type, 1)} disabled={b.total_beds === 0}
                  className="w-9 h-9 rounded-lg bg-gray-100 text-gray-700 font-bold text-lg disabled:opacity-30">+</button>
              </div>
            </div>
            {b.occupied_beds > 0 && (
              <label className="flex items-center justify-between gap-2 text-xs text-gray-500">
                {t('beds.freeBy', 'Occupied until')}
                <input type="date" value={b.occupied_until ?? ''}
                  onChange={(e) => setOccupiedUntil(b.bed_type, e.target.value)}
                  className="border-2 border-gray-200 rounded-lg px-2 py-1 text-sm focus:outline-none focus:border-teal-500" />
              </label>
            )}
          </div>
        ))}
        <button onClick={saveBeds} className="w-full py-2.5 rounded-xl bg-teal-600 text-white font-semibold hover:bg-teal-700 transition-colors">
          {bedsSaved ? t('beds.saved') : t('beds.save')}
        </button>
      </section>

      {/* Test Availability */}
      <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-3">
        <h2 className="text-base font-semibold text-gray-800">{t('tests.title')}</h2>
        <InfoNote>{t('info.tests')}</InfoNote>
        {loading && tests.length === 0 ? (
          <p className="text-sm text-gray-400">…</p>
        ) : tests.map((tst) => (
          <div key={tst.test_id} className="flex items-center justify-between">
            <span className="text-sm text-gray-800">{tst.test_name}</span>
            <button onClick={() => toggleTest(tst.test_id)}
              className={clsx('px-4 py-1.5 rounded-full text-xs font-bold transition-all',
                tst.available ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
              {tst.available ? t('tests.available') : t('tests.unavailable')}
            </button>
          </div>
        ))}
        <button onClick={saveTests} className="w-full py-2.5 rounded-xl bg-teal-600 text-white font-semibold hover:bg-teal-700 transition-colors">
          {testsSaved ? t('tests.saved') : t('tests.save')}
        </button>
      </section>
    </>
  )
}
