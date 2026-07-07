import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { QRCodeSVG } from 'qrcode.react'
import { createReferral, type CreateReferralResult } from '../api/referrals'

// PHC/CHC: create a digital referral and hand the patient a QR + code.

export default function ReferPatientPage() {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [sex, setSex] = useState('')
  const [yob, setYob] = useState('')
  const [reason, setReason] = useState('')
  const [bp, setBp] = useState('')
  const [dx, setDx] = useState('')
  const [meds, setMeds] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<CreateReferralResult | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setSubmitting(true)
    try {
      const clinical: Record<string, string> = {}
      if (bp) clinical.bp = bp
      if (dx) clinical.provisional_dx = dx
      if (meds) clinical.meds_given = meds
      const res = await createReferral({
        patient: { name, phone, sex: sex || undefined, year_of_birth: yob ? Number(yob) : undefined },
        reason: reason || undefined,
        clinical_summary: Object.keys(clinical).length ? clinical : undefined,
      })
      setResult(res)
    } catch (err) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || t('referral.create_failed'))
    } finally {
      setSubmitting(false)
    }
  }

  const reset = () => {
    setResult(null); setName(''); setPhone(''); setSex(''); setYob(''); setReason(''); setBp(''); setDx(''); setMeds('')
  }

  if (result) {
    const qrValue = `${window.location.origin}${result.retrieve_path}`
    return (
      <div className="max-w-xl mx-auto">
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8 text-center">
          <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-green-50 flex items-center justify-center">
            <svg className="w-8 h-8 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
          </div>
          <h1 className="text-2xl font-bold text-gray-900">{t('referral.created')}</h1>
          <p className="text-gray-500 mt-1">{result.patient.name} → {result.to_facility || t('referral.any_dh')}</p>

          <div className="my-6 flex justify-center">
            <div className="p-4 bg-white border border-gray-200 rounded-xl">
              <QRCodeSVG value={qrValue} size={180} level="M" />
            </div>
          </div>

          <div className="text-sm text-gray-500">{t('referral.code_label')}</div>
          <div className="text-3xl font-mono font-bold tracking-widest text-teal-700">{result.code}</div>

          <div className={`mt-5 text-sm rounded-lg px-4 py-2.5 ${result.whatsapp_delivered ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'}`}>
            {result.whatsapp_delivered ? `✓ ${t('referral.wa_sent')}` : t('referral.wa_not_config')}
          </div>
          <p className="text-xs text-gray-400 mt-2">{t('referral.share_note')} ({new Date(result.expires_at).toLocaleDateString()})</p>

          <button onClick={reset} className="mt-6 bg-teal-600 text-white font-semibold px-5 py-2.5 rounded-lg hover:bg-teal-700 transition-colors">
            {t('referral.refer_another')}
          </button>
        </div>
      </div>
    )
  }

  const input = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-500'
  const label = 'block text-xs font-semibold text-gray-600 mb-1'

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">{t('referral.refer_title')}</h1>
      <p className="text-gray-500 text-sm mb-6">{t('referral.refer_subtitle')}</p>
      <form onSubmit={submit} className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 space-y-4">
        {error && <div className="bg-red-50 text-red-700 text-sm rounded-lg px-4 py-2.5">{error}</div>}
        <div className="grid grid-cols-2 gap-4">
          <div className="col-span-2"><label className={label}>{t('referral.patient_name')} *</label><input className={input} value={name} onChange={(e) => setName(e.target.value)} required /></div>
          <div><label className={label}>{t('referral.phone')} *</label><input className={input} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+9198…" required /></div>
          <div className="grid grid-cols-2 gap-2">
            <div><label className={label}>{t('referral.sex')}</label>
              <select className={input} value={sex} onChange={(e) => setSex(e.target.value)}><option value="">—</option><option value="M">M</option><option value="F">F</option><option value="O">O</option></select>
            </div>
            <div><label className={label}>{t('referral.birth_year')}</label><input className={input} value={yob} onChange={(e) => setYob(e.target.value)} inputMode="numeric" placeholder="1980" /></div>
          </div>
        </div>
        <div><label className={label}>{t('referral.reason')}</label><input className={input} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t('referral.reason_ph')} /></div>
        <div className="grid grid-cols-3 gap-3">
          <div><label className={label}>{t('referral.vitals_bp')}</label><input className={input} value={bp} onChange={(e) => setBp(e.target.value)} placeholder="150/95" /></div>
          <div><label className={label}>{t('referral.prov_dx')}</label><input className={input} value={dx} onChange={(e) => setDx(e.target.value)} /></div>
          <div><label className={label}>{t('referral.meds_given')}</label><input className={input} value={meds} onChange={(e) => setMeds(e.target.value)} /></div>
        </div>
        <button type="submit" disabled={submitting || !name || !phone} className="w-full bg-teal-600 text-white font-semibold py-2.5 rounded-lg hover:bg-teal-700 disabled:opacity-50 transition-colors">
          {submitting ? t('referral.creating') : t('referral.create')}
        </button>
      </form>
    </div>
  )
}
