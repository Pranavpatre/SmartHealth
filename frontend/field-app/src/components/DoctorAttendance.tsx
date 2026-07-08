import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { useAuthStore } from '../stores/authStore'

// Per-doctor attendance: the field worker maintains the doctor roster for their
// PHC/CHC and marks each doctor present/absent for today.
interface Doctor {
  id: string
  name: string
  specialty: string | null
  present_today: boolean
}

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export default function DoctorAttendance() {
  const { t } = useTranslation()
  const { facilityId, token } = useAuthStore()
  const hdr = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }

  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [marks, setMarks] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [saved, setSaved] = useState(false)

  const load = useCallback(async () => {
    if (!facilityId || !token) return
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/v1/doctors/facility/${facilityId}`, { headers: hdr })
      if (r.ok) {
        const d: Doctor[] = await r.json()
        setDoctors(d)
        setMarks(Object.fromEntries(d.map((x) => [x.id, x.present_today])))
      }
    } finally { setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facilityId, token])

  useEffect(() => { load() }, [load])

  const setPresent = (id: string, present: boolean) =>
    setMarks((m) => ({ ...m, [id]: present }))

  const save = async () => {
    if (!facilityId) return
    const body = doctors.map((d) => ({ doctor_id: d.id, present: !!marks[d.id] }))
    const r = await fetch(`${API}/api/v1/doctors/facility/${facilityId}/attendance`, {
      method: 'PUT', headers: hdr, body: JSON.stringify(body),
    })
    if (r.ok) { setSaved(true); setTimeout(() => setSaved(false), 3000) }
  }

  const presentCount = doctors.filter((d) => marks[d.id]).length

  return (
    <section className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-800">{t('attendance.title')}</h2>
        <span className="text-xs text-gray-400">{presentCount}/{doctors.length} {t('attendance.present').toLowerCase()}</span>
      </div>
      <p className="text-xs text-gray-500 -mt-1">{t('attendance.rosterHint')}</p>

      {loading ? (
        <p className="text-sm text-gray-400">…</p>
      ) : doctors.length === 0 ? (
        <p className="text-sm text-gray-400">{t('attendance.noDoctors')}</p>
      ) : (
        <div className="divide-y divide-gray-50">
          {doctors.map((d) => (
            <div key={d.id} className="flex items-center justify-between py-2.5 gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-800 truncate">{d.name}</p>
                {d.specialty && <p className="text-xs text-gray-400">{d.specialty}</p>}
              </div>
              <div className="flex gap-2 shrink-0">
                <button onClick={() => setPresent(d.id, true)}
                  className={clsx('px-3 py-1.5 rounded-lg text-xs font-semibold',
                    marks[d.id] ? 'bg-green-600 text-white' : 'bg-gray-100 text-gray-600')}>
                  {t('attendance.present')}
                </button>
                <button onClick={() => setPresent(d.id, false)}
                  className={clsx('px-3 py-1.5 rounded-lg text-xs font-semibold',
                    marks[d.id] === false ? 'bg-red-500 text-white' : 'bg-gray-100 text-gray-600')}>
                  {t('attendance.absent')}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <button onClick={save} disabled={doctors.length === 0}
        className="w-full py-2.5 rounded-xl bg-teal-600 text-white font-semibold hover:bg-teal-700 disabled:opacity-40 transition-colors">
        {saved ? t('attendance.saved') : t('attendance.save')}
      </button>
    </section>
  )
}
