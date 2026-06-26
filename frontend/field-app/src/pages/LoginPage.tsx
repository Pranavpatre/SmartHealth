import { useState, useRef } from 'react'
import axios from 'axios'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

type Step = 'phone' | 'otp'

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)

  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState(['', '', '', '', '', ''])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const otpRefs = useRef<Array<HTMLInputElement | null>>([])

  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault()
    if (phone.length < 10) { setError('Enter a valid 10-digit phone number'); return }
    setError(null)
    setLoading(true)
    try {
      await axios.post(`${API_URL}/api/v1/auth/request-otp`, { phone })
      setStep('otp')
    } catch (err: unknown) {
      const msg = axios.isAxiosError(err) ? err.response?.data?.detail : 'Failed to send OTP'
      setError(String(msg ?? 'Failed to send OTP'))
    } finally {
      setLoading(false)
    }
  }

  const handleOtpChange = (index: number, value: string) => {
    if (!/^\d?$/.test(value)) return
    const next = [...otp]
    next[index] = value
    setOtp(next)
    if (value && index < 5) otpRefs.current[index + 1]?.focus()
  }

  const handleOtpKeyDown = (index: number, e: React.KeyboardEvent) => {
    if (e.key === 'Backspace' && !otp[index] && index > 0) {
      otpRefs.current[index - 1]?.focus()
    }
  }

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault()
    const code = otp.join('')
    if (code.length < 6) { setError('Enter the 6-digit OTP'); return }
    setError(null)
    setLoading(true)
    try {
      const { data } = await axios.post(`${API_URL}/api/v1/auth/verify-otp`, { phone, otp: code })
      setAuth({
        token: data.access_token,
        facilityId: data.facility_id,
        userId: data.user_id,
        name: data.name,
        facilityName: data.facility_name,
      })
      navigate('/daily', { replace: true })
    } catch (err: unknown) {
      const msg = axios.isAxiosError(err) ? err.response?.data?.detail : 'Invalid OTP'
      setError(String(msg ?? 'Invalid OTP'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-teal-600 to-teal-700 flex flex-col items-center justify-center p-6">
      {/* Logo / Branding */}
      <div className="mb-8 text-center space-y-2">
        <div className="w-16 h-16 bg-white rounded-2xl mx-auto flex items-center justify-center shadow-lg">
          <span className="text-3xl" aria-hidden>🏥</span>
        </div>
        <h1 className="text-2xl font-bold text-white tracking-tight">SmartHealth</h1>
        <p className="text-teal-200 text-sm">PHC Field Worker App</p>
      </div>

      {/* Card */}
      <div className="w-full max-w-sm bg-white rounded-3xl shadow-xl p-6 space-y-6">
        {step === 'phone' ? (
          <form onSubmit={handleSendOtp} className="space-y-5">
            <div className="space-y-1">
              <h2 className="text-lg font-bold text-gray-800">Welcome back</h2>
              <p className="text-sm text-gray-500">Enter your registered phone number</p>
            </div>

            <div className="space-y-2">
              <label htmlFor="phone" className="text-sm font-medium text-gray-700">
                Phone Number
              </label>
              <div className="flex">
                <span className="inline-flex items-center px-3 rounded-l-xl border border-r-0 border-gray-200 bg-gray-50 text-gray-500 text-sm">
                  +91
                </span>
                <input
                  id="phone"
                  type="tel"
                  inputMode="numeric"
                  maxLength={10}
                  value={phone}
                  onChange={(e) => { setPhone(e.target.value.replace(/\D/g, '')); setError(null) }}
                  placeholder="9876543210"
                  className="flex-1 border border-gray-200 rounded-r-xl px-4 py-3 text-lg font-medium focus:outline-none focus:border-teal-500 transition-colors"
                  autoFocus
                />
              </div>
            </div>

            {error && <p className="text-sm text-red-500 text-center">{error}</p>}

            <button
              type="submit"
              disabled={loading || phone.length < 10}
              className="w-full py-3.5 rounded-xl bg-teal-600 text-white font-bold text-base disabled:opacity-40 hover:bg-teal-700 transition-colors"
            >
              {loading ? 'Sending…' : 'Send OTP'}
            </button>
          </form>
        ) : (
          <form onSubmit={handleVerifyOtp} className="space-y-5">
            <div className="space-y-1">
              <h2 className="text-lg font-bold text-gray-800">Enter OTP</h2>
              <p className="text-sm text-gray-500">
                Sent to +91 {phone}{' '}
                <button
                  type="button"
                  onClick={() => { setStep('phone'); setOtp(['','','','','','']); setError(null) }}
                  className="text-teal-600 font-medium hover:underline"
                >
                  Change
                </button>
              </p>
            </div>

            {/* OTP boxes */}
            <div className="flex gap-2 justify-center">
              {otp.map((digit, i) => (
                <input
                  key={i}
                  ref={(el) => { otpRefs.current[i] = el }}
                  type="text"
                  inputMode="numeric"
                  maxLength={1}
                  value={digit}
                  onChange={(e) => handleOtpChange(i, e.target.value)}
                  onKeyDown={(e) => handleOtpKeyDown(i, e)}
                  className="w-11 h-13 text-center text-xl font-bold border-2 border-gray-200 rounded-xl py-3 focus:outline-none focus:border-teal-500 transition-colors"
                />
              ))}
            </div>

            {error && <p className="text-sm text-red-500 text-center">{error}</p>}

            <button
              type="submit"
              disabled={loading || otp.join('').length < 6}
              className="w-full py-3.5 rounded-xl bg-teal-600 text-white font-bold text-base disabled:opacity-40 hover:bg-teal-700 transition-colors"
            >
              {loading ? 'Verifying…' : 'Verify & Login'}
            </button>

            <button
              type="button"
              onClick={handleSendOtp}
              disabled={loading}
              className="w-full text-sm text-teal-600 font-medium hover:underline disabled:opacity-40"
            >
              Resend OTP
            </button>
          </form>
        )}
      </div>

      <p className="mt-6 text-teal-300 text-xs text-center">
        Works offline up to 72 hours
      </p>
    </div>
  )
}
