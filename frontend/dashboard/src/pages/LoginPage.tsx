import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { apiClient } from '../api/client'
import { useAuthStore } from '../stores/authStore'

export default function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState('')
  const [step, setStep] = useState<'phone' | 'otp'>('phone')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function requestOtp() {
    setLoading(true)
    setError('')
    try {
      await apiClient.post('/auth/otp/request', { phone })
      setStep('otp')
    } catch {
      setError(t('login.err_send'))
    } finally {
      setLoading(false)
    }
  }

  async function verifyOtp() {
    setLoading(true)
    setError('')
    try {
      const { data } = await apiClient.post<{
        access_token: string
        refresh_token: string
        user_id: string
        role: string
        name: string
        facility_id: string | null
        facility_name: string | null
      }>('/auth/otp/verify', { phone, otp })
      setAuth({
        token: data.access_token,
        refreshToken: data.refresh_token,
        userId: data.user_id,
        role: data.role,
        name: data.name,
        facilityId: data.facility_id,
        facilityName: data.facility_name,
      })
      // PHC_ADMIN is scoped to a single facility — land them straight on
      // their facility view rather than the district-wide dashboard, which
      // their role can't actually query.
      navigate(data.role === 'PHC_ADMIN' ? '/my-facility' : '/dashboard')
    } catch {
      setError(t('login.err_invalid'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 bg-teal-50 rounded-full mb-4">
            <svg className="w-8 h-8 text-teal-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-teal-700">PrediCare</h1>
          <p className="text-gray-500 text-sm mt-1">{t('login.tagline')}</p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 rounded-lg mb-4 text-center">
            {error}
          </div>
        )}

        {step === 'phone' ? (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('login.phone_label')}
              </label>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && phone && requestOtp()}
                placeholder={t('login.phone_placeholder')}
                className="w-full border border-gray-300 rounded-lg px-4 py-2.5 text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent outline-none transition-shadow"
              />
            </div>
            <button
              onClick={requestOtp}
              disabled={loading || !phone}
              className="w-full bg-teal-600 text-white font-semibold py-2.5 rounded-lg hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? t('login.sending') : t('login.send_otp')}
            </button>
            <p className="text-xs text-center text-gray-400">
              {t('login.otp_help')}
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-gray-600 text-center">
              {t('login.enter_otp', { phone })}
            </p>
            <input
              type="text"
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
              onKeyDown={(e) => e.key === 'Enter' && otp.length >= 6 && verifyOtp()}
              placeholder={t('login.otp_placeholder')}
              maxLength={6}
              className="w-full border border-gray-300 rounded-lg px-4 py-2.5 text-sm text-center tracking-[0.5em] font-mono focus:ring-2 focus:ring-teal-500 focus:border-transparent outline-none transition-shadow"
            />
            <button
              onClick={verifyOtp}
              disabled={loading || otp.length < 6}
              className="w-full bg-teal-600 text-white font-semibold py-2.5 rounded-lg hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? t('login.verifying') : t('login.login')}
            </button>
            <button
              onClick={() => { setStep('phone'); setOtp(''); setError('') }}
              className="w-full text-sm text-gray-500 hover:text-gray-700 transition-colors"
            >
              {t('login.change_number')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
