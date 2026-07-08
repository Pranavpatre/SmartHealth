import { useEffect } from 'react'
import { Routes, Route, Navigate, Outlet, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { LANGUAGES } from './i18n'
import { useAuthStore } from './stores/authStore'
import { syncPendingData } from './sync/syncService'
import OfflineBanner from './components/OfflineBanner'
import BottomNav from './components/BottomNav'
import HelpModal from './components/HelpModal'
import LocationBadge from './components/LocationBadge'
import LoginPage from './pages/LoginPage'
import DailyEntryPage from './pages/DailyEntryPage'
import StockEntryPage from './pages/StockEntryPage'
import NotificationsPage from './pages/NotificationsPage'
import LogsPage from './pages/LogsPage'
import HelpPage from './pages/HelpPage'
import ReferralsPage from './pages/ReferralsPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

// Unified top bar: PrediCare branding + on-site check-in + help, the worker's
// name/facility, and the language selector shown as parallel chips (all 10
// languages, matching the admin dashboard) instead of a dropdown.
function TopBar() {
  const { t, i18n } = useTranslation()
  const { name, facilityName, token } = useAuthStore()
  if (!token) return null
  return (
    <header className="bg-white border-b border-gray-200 sticky top-0 z-30">
      <div className="max-w-2xl mx-auto px-4 py-2 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <img src="/favicon.svg" alt="" className="w-7 h-7 rounded-md shrink-0" />
            <div className="leading-tight min-w-0">
              <p className="font-bold text-teal-700 text-sm">PrediCare</p>
              {facilityName && <p className="text-[11px] text-gray-400 truncate">{facilityName}</p>}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <LocationBadge />
            <Link
              to="/help"
              aria-label={t('help.navLabel')}
              className="w-8 h-8 flex items-center justify-center rounded-full border border-gray-200 bg-gray-50 text-gray-600 font-bold text-sm hover:bg-gray-100 transition-colors"
            >
              ?
            </Link>
          </div>
        </div>
        {name && <p className="text-xs text-gray-500">{name}</p>}
        <div className="flex flex-wrap gap-1">
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              onClick={() => i18n.changeLanguage(l.code)}
              className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                i18n.language === l.code
                  ? 'bg-teal-100 text-teal-700 font-semibold'
                  : 'text-gray-500 hover:bg-gray-100'
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>
    </header>
  )
}

function AppLayout() {
  return (
    <div className="pb-16">
      <TopBar />
      <Outlet />
      <HelpModal />
    </div>
  )
}

export default function App() {
  // UI text stays on the default language (English) until the worker manually
  // picks one via the toggle — languagePref is still used for voice input,
  // where matching the worker's actual spoken language is a correctness need
  // rather than a display preference.

  // Auto-flush the offline queue the moment connectivity comes back, so the
  // manual "Sync Now" button is a reassurance/fallback, not a required step.
  useEffect(() => {
    const handleOnline = () => { syncPendingData() }
    window.addEventListener('online', handleOnline)
    return () => window.removeEventListener('online', handleOnline)
  }, [])

  // If the logged-in user has no assigned facility (e.g. an admin/tester), scope
  // the data-entry screens to the GPS-nearest facility so "my location facility"
  // still shows beds/tests/doctors instead of empty sections.
  useEffect(() => {
    const { token, facilityId, activeFacilityId, setActiveFacility } = useAuthStore.getState()
    if (!token || facilityId || activeFacilityId || !navigator.geolocation) return
    const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          const r = await fetch(
            `${API}/api/v1/facilities/nearest?lat=${pos.coords.latitude}&lng=${pos.coords.longitude}&limit=1`,
            { headers: { Authorization: `Bearer ${token}` } },
          )
          if (r.ok) {
            const list = await r.json()
            if (list[0]?.id) setActiveFacility(list[0].id, list[0].name)
          }
        } catch { /* keep empty; nothing to scope to */ }
      },
      () => {},
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 },
    )
  }, [])

  return (
    <>
      <OfflineBanner />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="/daily" replace />} />
          <Route path="daily" element={<DailyEntryPage />} />
          <Route path="stock" element={<StockEntryPage />} />
          <Route path="referrals" element={<ReferralsPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="logs" element={<LogsPage />} />
          <Route path="help" element={<HelpPage />} />
        </Route>
        {/* Catch-all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <BottomNav />
    </>
  )
}
