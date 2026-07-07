import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  searchReferrals, getReferralByCode, requestPatientOtp, verifyPatientOtp, addVisitNote,
  type Referral,
} from '../api/referrals'

// Doctor / hospital staff: pull up a referral by name-search, code, or phone+OTP.
// English-only for now (Phase-1 MVP).

type Mode = 'search' | 'code' | 'otp'

function errText(e: unknown) {
  return (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Something went wrong'
}

export default function RetrieveReferralPage() {
  const [params] = useSearchParams()
  const [mode, setMode] = useState<Mode>('search')
  const [q, setQ] = useState('')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [otp, setOtp] = useState('')
  const [otpSent, setOtpSent] = useState(false)
  const [results, setResults] = useState<Referral[]>([])
  const [selected, setSelected] = useState<Referral | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)

  const lookupCode = useCallback(async (c: string) => {
    setError(null); setInfo(null); setLoading(true); setResults([])
    try {
      const r = await getReferralByCode(c.trim())
      if (r.consent_required) { setInfo(r.message || 'Consent required — use phone + OTP.'); setResults([r]); setSelected(null) }
      else { setSelected(r); setResults([r]) }
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }, [])

  // Auto-lookup when opened from a QR/link: /referrals?code=XXXX
  useEffect(() => {
    const c = params.get('code')
    if (c) { setMode('code'); setCode(c); lookupCode(c) }
  }, [params, lookupCode])

  const runSearch = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null); setInfo(null); setLoading(true); setSelected(null)
    try {
      const res = await searchReferrals({ q: q || undefined, phone: phone || undefined })
      setResults(res.results)
      if (!res.results.length) setInfo('No referrals found for this facility.')
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }

  const sendOtp = async () => {
    setError(null); setLoading(true)
    try { await requestPatientOtp(phone); setOtpSent(true); setInfo('OTP sent to the patient’s phone.') }
    catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }
  const checkOtp = async () => {
    setError(null); setInfo(null); setLoading(true)
    try {
      const res = await verifyPatientOtp(phone, otp)
      setResults(res.results); setSelected(res.results[0] || null)
      if (!res.results.length) setInfo('No records for this patient.')
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }

  const tabBtn = (m: Mode, txt: string) => (
    <button onClick={() => { setMode(m); setError(null); setInfo(null) }}
      className={`px-4 py-2 text-sm font-semibold rounded-lg transition-colors ${mode === m ? 'bg-teal-600 text-white' : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50'}`}>
      {txt}
    </button>
  )
  const input = 'border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500'

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Retrieve referral</h1>
      <p className="text-gray-500 text-sm mb-5">Search a referred patient by name, enter their referral code, or use phone + OTP.</p>

      <div className="flex gap-2 mb-4">{tabBtn('search', 'Search by name')}{tabBtn('code', 'Referral code')}{tabBtn('otp', 'Phone + OTP')}</div>

      <div className="bg-white rounded-xl border border-gray-200 p-4 mb-4">
        {mode === 'search' && (
          <form onSubmit={runSearch} className="flex flex-wrap gap-2 items-end">
            <div><label className="block text-xs font-semibold text-gray-600 mb-1">Patient name</label><input className={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Ramesh" /></div>
            <div><label className="block text-xs font-semibold text-gray-600 mb-1">or Phone</label><input className={input} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+9198…" /></div>
            <button type="submit" className="bg-teal-600 text-white font-semibold px-4 py-2 rounded-lg hover:bg-teal-700 text-sm">Search</button>
          </form>
        )}
        {mode === 'code' && (
          <div className="flex gap-2 items-end">
            <div><label className="block text-xs font-semibold text-gray-600 mb-1">Referral code</label><input className={`${input} font-mono uppercase tracking-widest`} value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="ABC123" /></div>
            <button onClick={() => lookupCode(code)} disabled={!code} className="bg-teal-600 text-white font-semibold px-4 py-2 rounded-lg hover:bg-teal-700 disabled:opacity-50 text-sm">Open</button>
          </div>
        )}
        {mode === 'otp' && (
          <div className="flex flex-wrap gap-2 items-end">
            <div><label className="block text-xs font-semibold text-gray-600 mb-1">Patient phone</label><input className={input} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+9198…" /></div>
            {!otpSent ? (
              <button onClick={sendOtp} disabled={!phone} className="bg-teal-600 text-white font-semibold px-4 py-2 rounded-lg hover:bg-teal-700 disabled:opacity-50 text-sm">Send OTP</button>
            ) : (
              <>
                <div><label className="block text-xs font-semibold text-gray-600 mb-1">OTP</label><input className={`${input} font-mono tracking-widest w-28`} value={otp} onChange={(e) => setOtp(e.target.value)} placeholder="000000" /></div>
                <button onClick={checkOtp} disabled={!otp} className="bg-teal-600 text-white font-semibold px-4 py-2 rounded-lg hover:bg-teal-700 disabled:opacity-50 text-sm">Unlock record</button>
              </>
            )}
            <span className="text-xs text-gray-400 self-center">Patient's OTP = their consent to share.</span>
          </div>
        )}
      </div>

      {loading && <p className="text-gray-400 text-sm">Loading…</p>}
      {error && <div className="bg-red-50 text-red-700 text-sm rounded-lg px-4 py-2.5 mb-3">{error}</div>}
      {info && <div className="bg-amber-50 text-amber-700 text-sm rounded-lg px-4 py-2.5 mb-3">{info}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* results list */}
        {results.length > 1 && (
          <div className="lg:col-span-2 space-y-2">
            {results.map((r) => (
              <button key={r.id} onClick={() => !r.consent_required && setSelected(r)}
                className={`w-full text-left bg-white border rounded-lg p-3 ${selected?.id === r.id ? 'border-teal-500 ring-2 ring-teal-200' : 'border-gray-200 hover:border-gray-300'}`}>
                <div className="font-semibold text-gray-900">{r.patient.name}</div>
                <div className="text-xs text-gray-500">{r.from_facility} → {r.to_facility || 'any DH'} · {r.status}</div>
              </button>
            ))}
          </div>
        )}
        {/* detail */}
        <div className={results.length > 1 ? 'lg:col-span-3' : 'lg:col-span-5'}>
          {selected ? <ReferralDetail referral={selected} onNoteAdded={() => lookupCode(selected.code)} /> : null}
        </div>
      </div>
    </div>
  )
}

function ReferralDetail({ referral, onNoteAdded }: { referral: Referral; onNoteAdded: () => void }) {
  const [dx, setDx] = useState('')
  const [action, setAction] = useState('')
  const [follow, setFollow] = useState('')
  const [saving, setSaving] = useState(false)
  const cs = referral.clinical_summary || {}

  const save = async () => {
    setSaving(true)
    try {
      await addVisitNote(referral.id, { diagnosis: dx || undefined, action: action || undefined, follow_up: follow || undefined })
      setDx(''); setAction(''); setFollow(''); onNoteAdded()
    } finally { setSaving(false) }
  }

  const age = referral.patient.year_of_birth ? `${new Date().getFullYear() - referral.patient.year_of_birth}y` : ''

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-900">{referral.patient.name}</h2>
          <p className="text-sm text-gray-500">{[referral.patient.sex, age].filter(Boolean).join(' · ')} · {referral.patient.phone}</p>
        </div>
        <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-teal-50 text-teal-700">{referral.status}</span>
      </div>
      <div className="mt-3 text-sm text-gray-600">{referral.from_facility} → {referral.to_facility || 'any district hospital'} · code <span className="font-mono">{referral.code}</span></div>
      {referral.reason && <div className="mt-3"><div className="text-xs font-semibold text-gray-500">Reason</div><div className="text-sm text-gray-800">{referral.reason}</div></div>}

      {Object.keys(cs).length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-semibold text-gray-500 mb-1">Clinical summary (from PHC)</div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(cs).map(([k, v]) => (
              <span key={k} className="text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1"><b className="text-gray-500">{k.replace(/_/g, ' ')}:</b> {String(v)}</span>
            ))}
          </div>
        </div>
      )}

      {referral.visit_notes && referral.visit_notes.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold text-gray-500 mb-1">History / visit notes</div>
          <div className="space-y-2">
            {referral.visit_notes.map((n) => (
              <div key={n.id} className="text-sm bg-gray-50 border border-gray-100 rounded-lg p-2.5">
                {Object.entries(n.note).map(([k, v]) => <div key={k}><b className="text-gray-500 text-xs">{k.replace(/_/g, ' ')}:</b> {v}</div>)}
                <div className="text-[11px] text-gray-400 mt-1">{n.facility} · {n.created_at ? new Date(n.created_at).toLocaleString() : ''}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-5 border-t border-gray-100 pt-4">
        <div className="text-xs font-semibold text-gray-500 mb-2">Add visit outcome</div>
        <div className="grid grid-cols-3 gap-2">
          <input className="border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="Diagnosis" value={dx} onChange={(e) => setDx(e.target.value)} />
          <input className="border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="Action taken" value={action} onChange={(e) => setAction(e.target.value)} />
          <input className="border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="Follow-up" value={follow} onChange={(e) => setFollow(e.target.value)} />
        </div>
        <button onClick={save} disabled={saving || (!dx && !action && !follow)} className="mt-3 bg-teal-600 text-white font-semibold px-4 py-2 rounded-lg hover:bg-teal-700 disabled:opacity-50 text-sm">
          {saving ? 'Saving…' : 'Save outcome'}
        </button>
      </div>
    </div>
  )
}
