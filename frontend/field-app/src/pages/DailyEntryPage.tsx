import { useState, useEffect } from 'react'
import { format } from 'date-fns'
import { db } from '../db/localDb'
import { useAuthStore } from '../stores/authStore'
import { useVoiceInput, parseSpokenNumber } from '../hooks/useVoiceInput'
import { syncPendingData } from '../sync/syncService'
import clsx from 'clsx'

function generateClientId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

export default function DailyEntryPage() {
  const { facilityId, userId } = useAuthStore()
  const today = format(new Date(), 'yyyy-MM-dd')

  // Patient count state
  const [patientCount, setPatientCount] = useState<string>('')
  const [footfallSaved, setFootfallSaved] = useState(false)
  const [footfallError, setFootfallError] = useState<string | null>(null)

  // Doctor attendance state
  const [doctorPresent, setDoctorPresent] = useState<boolean>(true)
  const [attendanceSaved, setAttendanceSaved] = useState(false)

  // Pending count
  const [pendingCount, setPendingCount] = useState(0)

  // Sync
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  const { isListening, transcript, error: voiceError, startListening, stopListening, reset: resetVoice } =
    useVoiceInput('hi-IN')

  // Parse voice transcript into patient count
  useEffect(() => {
    if (!transcript) return
    const parsed = parseSpokenNumber(transcript)
    if (parsed !== null) {
      setPatientCount(String(parsed))
      setFootfallError(null)
    } else {
      setFootfallError(`Could not parse "${transcript}" as a number`)
    }
    resetVoice()
  }, [transcript, resetVoice])

  // Load pending count on mount and after saves
  const refreshPending = async () => {
    const [f, a] = await Promise.all([
      db.pendingFootfall.where('synced').equals(0).count(),
      db.pendingAttendance.where('synced').equals(0).count(),
    ])
    setPendingCount(f + a)
  }

  useEffect(() => { refreshPending() }, [])

  const handleSaveFootfall = async () => {
    if (!facilityId || !userId) return
    const count = parseInt(patientCount, 10)
    if (isNaN(count) || count < 0) {
      setFootfallError('Enter a valid patient count')
      return
    }
    setFootfallError(null)
    await db.pendingFootfall.add({
      facility_id: facilityId,
      date: today,
      footfall_count: count,
      recorded_at: new Date().toISOString(),
      client_id: generateClientId(),
      synced: false,
    })
    setFootfallSaved(true)
    setTimeout(() => setFootfallSaved(false), 3000)
    setPatientCount('')
    refreshPending()
  }

  const handleToggleAttendance = async (present: boolean) => {
    if (!facilityId || !userId) return
    setDoctorPresent(present)
    await db.pendingAttendance.add({
      facility_id: facilityId,
      user_id: userId,
      date: today,
      present,
      recorded_at: new Date().toISOString(),
      client_id: generateClientId(),
      synced: false,
    })
    setAttendanceSaved(true)
    setTimeout(() => setAttendanceSaved(false), 3000)
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

  return (
    <div className="min-h-screen bg-gray-50 p-4 space-y-6 max-w-lg mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between pt-2">
        <h1 className="text-xl font-bold text-teal-600">Daily Entry</h1>
        <div className="flex items-center gap-2">
          {pendingCount > 0 && (
            <span className="bg-orange-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
              {pendingCount} pending
            </span>
          )}
          <span className="text-sm text-gray-500">{format(new Date(), 'dd MMM yyyy')}</span>
        </div>
      </div>

      {/* Patient Count Section */}
      <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-4">
        <h2 className="text-base font-semibold text-gray-800">Today's Patient Count</h2>

        <div className="flex gap-3 items-center">
          <input
            type="number"
            min="0"
            value={patientCount}
            onChange={(e) => { setPatientCount(e.target.value); setFootfallError(null) }}
            placeholder="0"
            className="flex-1 text-3xl font-bold text-center border-2 border-gray-200 rounded-xl py-3 px-4 focus:outline-none focus:border-teal-500 transition-colors"
          />
          <button
            onPointerDown={startListening}
            onPointerUp={stopListening}
            className={clsx(
              'w-14 h-14 rounded-full flex items-center justify-center text-2xl shadow transition-all',
              isListening
                ? 'bg-red-500 text-white scale-110 animate-pulse'
                : 'bg-teal-600 text-white hover:bg-teal-700',
            )}
            title={isListening ? 'Listening…' : 'Hold to speak'}
            aria-label="Voice input"
          >
            {isListening ? '⏹' : '🎤'}
          </button>
        </div>

        {voiceError && <p className="text-sm text-red-500">{voiceError}</p>}
        {footfallError && <p className="text-sm text-red-500">{footfallError}</p>}
        {isListening && (
          <p className="text-sm text-blue-500 animate-pulse">Listening… speak the count</p>
        )}

        <button
          onClick={handleSaveFootfall}
          disabled={!patientCount}
          className="w-full py-3 rounded-xl bg-teal-600 text-white font-semibold disabled:opacity-40 hover:bg-teal-700 transition-colors"
        >
          {footfallSaved ? 'Saved ✓' : 'Save Patient Count'}
        </button>
      </section>

      {/* Doctor Attendance Section */}
      <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-4">
        <h2 className="text-base font-semibold text-gray-800">Doctor Attendance</h2>
        <div className="flex gap-3">
          <button
            onClick={() => handleToggleAttendance(true)}
            className={clsx(
              'flex-1 py-3 rounded-xl font-semibold text-sm transition-all',
              doctorPresent
                ? 'bg-green-600 text-white shadow-sm'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200',
            )}
          >
            Present
          </button>
          <button
            onClick={() => handleToggleAttendance(false)}
            className={clsx(
              'flex-1 py-3 rounded-xl font-semibold text-sm transition-all',
              !doctorPresent
                ? 'bg-red-500 text-white shadow-sm'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200',
            )}
          >
            Absent
          </button>
        </div>
        {attendanceSaved && (
          <p className="text-sm text-green-600 font-medium">Attendance saved ✓</p>
        )}
      </section>

      {/* Sync Section */}
      <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
        <button
          onClick={handleSync}
          disabled={syncing || pendingCount === 0}
          className="w-full py-3 rounded-xl bg-blue-600 text-white font-semibold disabled:opacity-40 hover:bg-blue-700 transition-colors"
        >
          {syncing ? 'Syncing…' : `Sync Now${pendingCount > 0 ? ` (${pendingCount})` : ''}`}
        </button>
        {syncMsg && <p className="text-sm text-gray-600 mt-2 text-center">{syncMsg}</p>}
      </section>
    </div>
  )
}
