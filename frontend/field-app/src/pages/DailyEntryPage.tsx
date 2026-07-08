import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { format } from 'date-fns'
import { db } from '../db/localDb'
import { useAuthStore } from '../stores/authStore'
import { useVoiceInput, parseSpokenNumber, VOICE_LANG_MAP } from '../hooks/useVoiceInput'
import { syncPendingData, queueLedger } from '../sync/syncService'
import InfoNote from '../components/InfoNote'
import VoiceRecordingBanner from '../components/VoiceRecordingBanner'
import DoctorAttendance from '../components/DoctorAttendance'
import BedTestEntry from '../components/BedTestEntry'
import clsx from 'clsx'

function generateClientId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

// Daily Entry = the manual per-day inputs a field worker records: patient count,
// footfall tally by type, and per-doctor attendance. Facility resources (bed
// matrix, test availability, medicine stock) live in the Stock tab.
export default function DailyEntryPage() {
  const { t } = useTranslation()
  const { facilityId, userId, languagePref } = useAuthStore()
  const today = format(new Date(), 'yyyy-MM-dd')

  // Rapid footfall tally (general / maternal / emergency)
  const [tally, setTally] = useState({ general: 0, maternal: 0, emergency: 0 })
  const [tallySaved, setTallySaved] = useState(false)
  const bump = (k: 'general' | 'maternal' | 'emergency', d: number) =>
    setTally((prev) => ({ ...prev, [k]: Math.max(0, prev[k] + d) }))
  const saveTally = async () => {
    if (!facilityId) return
    await queueLedger('footfall', facilityId, tally)
    setTallySaved(true); setTimeout(() => setTallySaved(false), 3000)
    if (navigator.onLine) await syncPendingData()
    refreshPending()
  }

  useEffect(() => {
    if (!facilityId) return
    const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
    const { token } = useAuthStore.getState()
    fetch(`${API}/api/v1/ledger/footfall/${facilityId}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setTally({ general: d.general, maternal: d.maternal, emergency: d.emergency }))
      .catch(() => {})
  }, [facilityId])

  // Patient count
  const [patientCount, setPatientCount] = useState('')
  const [footfallSaved, setFootfallSaved] = useState(false)
  const [footfallError, setFootfallError] = useState<string | null>(null)

  const [pendingCount, setPendingCount] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  const { isListening, transcript, error: voiceError, startListening, stopListening, reset: resetVoice } =
    useVoiceInput(VOICE_LANG_MAP[languagePref] || 'en-IN')

  useEffect(() => {
    if (!transcript) return
    const parsed = parseSpokenNumber(transcript)
    if (parsed !== null) { setPatientCount(String(parsed)); setFootfallError(null) }
    else setFootfallError(t('patient.errorParse', { transcript }))
    resetVoice()
  }, [transcript, resetVoice]) // eslint-disable-line react-hooks/exhaustive-deps

  const refreshPending = async () => {
    const [f, a, l] = await Promise.all([
      db.pendingFootfall.filter((r) => !r.synced).count(),
      db.pendingAttendance.filter((r) => !r.synced).count(),
      db.pendingLedger.filter((r) => !r.synced).count(),
    ])
    setPendingCount(f + a + l)
  }
  useEffect(() => { refreshPending() }, [])

  const handleSaveFootfall = async () => {
    if (!facilityId || !userId) return
    const count = parseInt(patientCount, 10)
    if (isNaN(count) || count < 0) { setFootfallError(t('patient.errorInvalid')); return }
    setFootfallError(null)
    await db.pendingFootfall.add({
      facility_id: facilityId, date: today, footfall_count: count,
      recorded_at: new Date().toISOString(), client_id: generateClientId(), synced: false,
    })
    setFootfallSaved(true); setTimeout(() => setFootfallSaved(false), 3000)
    setPatientCount('')
    if (navigator.onLine) await syncPendingData()
    refreshPending()
  }

  const handleSync = async () => {
    if (!navigator.onLine) {
      setSyncMsg(t('sync.noInternet')); setTimeout(() => setSyncMsg(null), 3000); return
    }
    setSyncing(true)
    const result = await syncPendingData()
    setSyncing(false)
    setSyncMsg(t('sync.result', { synced: result.synced, errorsSuffix: result.errors > 0 ? `, ${result.errors} failed` : '' }))
    setTimeout(() => setSyncMsg(null), 4000)
    refreshPending()
  }

  return (
    <div className="min-h-screen bg-gray-50 w-full max-w-2xl mx-auto">
      <VoiceRecordingBanner show={isListening} label={t('voice.recording')} />
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-4">
        <h1 className="text-xl font-bold text-teal-600">{t('daily.title')}</h1>
        <div className="flex items-center gap-2">
          {pendingCount > 0 && (
            <span className="bg-orange-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
              {pendingCount} {t('daily.pending')}
            </span>
          )}
          <span className="text-sm text-gray-500">{format(new Date(), 'dd MMM yyyy')}</span>
        </div>
      </div>

      <div className="p-4 pb-20 space-y-4">
        {/* Patient count */}
        <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-4">
          <h2 className="text-base font-semibold text-gray-800">{t('patient.title')}</h2>
          <InfoNote>{t('info.patient')}</InfoNote>
          <div className="flex gap-3 items-center">
            <input type="number" min="0" value={patientCount}
              onChange={(e) => { setPatientCount(e.target.value); setFootfallError(null) }}
              placeholder="0"
              className="flex-1 text-3xl font-bold text-center border-2 border-gray-200 rounded-xl py-3 px-4 focus:outline-none focus:border-teal-500 transition-colors" />
            <button onPointerDown={startListening} onPointerUp={stopListening}
              className={clsx('w-14 h-14 rounded-full flex items-center justify-center text-2xl shadow transition-all',
                isListening ? 'bg-red-500 text-white scale-110 animate-pulse' : 'bg-teal-600 text-white hover:bg-teal-700')}
              aria-label="Voice input">
              {isListening ? '⏹' : '🎤'}
            </button>
          </div>
          {voiceError && <p className="text-sm text-red-500">{voiceError}</p>}
          {footfallError && <p className="text-sm text-red-500">{footfallError}</p>}
          <button onClick={handleSaveFootfall} disabled={!patientCount}
            className="w-full py-3 rounded-xl bg-teal-600 text-white font-semibold disabled:opacity-40 hover:bg-teal-700 transition-colors">
            {footfallSaved ? t('tests.saved') : t('patient.save')}
          </button>
        </section>

        {/* Footfall tally */}
        <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-4">
          <h2 className="text-base font-semibold text-gray-800">{t('footfall.title')}</h2>
          <InfoNote>{t('info.footfall')}</InfoNote>
          {([['general', t('footfall.general')], ['maternal', t('footfall.maternal')], ['emergency', t('footfall.emergency')]] as const).map(([key, label]) => (
            <div key={key} className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-800">{label}</span>
              <div className="flex items-center gap-2">
                <button onClick={() => bump(key, -1)} className="w-9 h-9 rounded-lg bg-gray-100 text-gray-700 font-bold text-lg">−</button>
                <span className="w-10 text-center font-bold text-gray-900 text-lg">{tally[key]}</span>
                <button onClick={() => bump(key, 1)} className="w-9 h-9 rounded-lg bg-teal-100 text-teal-700 font-bold text-lg">+</button>
              </div>
            </div>
          ))}
          <button onClick={saveTally} className="w-full py-2.5 rounded-xl bg-teal-600 text-white font-semibold hover:bg-teal-700 transition-colors">
            {tallySaved ? t('footfall.saved') : `${t('footfall.save')} (${tally.general + tally.maternal + tally.emergency})`}
          </button>
        </section>

        {/* Per-doctor attendance */}
        <DoctorAttendance />

        {/* Bed matrix + test availability — daily field-worker inputs */}
        <BedTestEntry />

        {/* Sync */}
        <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-2">
          <InfoNote>{t('info.sync')}</InfoNote>
          <button onClick={handleSync} disabled={syncing || pendingCount === 0}
            className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold disabled:opacity-40 hover:bg-blue-700 transition-colors">
            {syncing ? t('sync.syncing') : `${t('sync.now')}${pendingCount > 0 ? ` (${pendingCount})` : ''}`}
          </button>
          {syncMsg && <p className="text-sm text-gray-600 mt-2 text-center">{syncMsg}</p>}
        </section>
      </div>
    </div>
  )
}
