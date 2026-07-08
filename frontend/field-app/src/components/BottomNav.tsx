import { NavLink, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '../stores/authStore'

const tabs = [
  { to: '/daily', key: 'nav.daily', icon: '📋' },
  { to: '/stock', key: 'nav.stock', icon: '💊' },
  { to: '/referrals', key: 'nav.referrals', icon: '📤' },
  { to: '/notifications', key: 'nav.alerts', icon: '🔔' },
  { to: '/logs', key: 'nav.logs', icon: '📜' },
]

export default function BottomNav() {
  const { t } = useTranslation()
  const token = useAuthStore((s) => s.token)
  const location = useLocation()

  // Don't show nav on login page or when unauthenticated
  if (!token || location.pathname === '/login') return null

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-white border-t border-gray-200 safe-area-inset-bottom">
      <div className="max-w-lg mx-auto flex">
        {tabs.map(({ to, key, icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `relative flex-1 flex flex-col items-center justify-center gap-0.5 py-2.5 text-xs font-medium transition-colors ${
                isActive
                  ? 'text-teal-600'
                  : 'text-gray-400 hover:text-gray-600'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={`text-xl leading-none transition-transform ${
                    isActive ? 'scale-110' : ''
                  }`}
                  aria-hidden
                >
                  {icon}
                </span>
                <span className={isActive ? 'text-teal-600' : ''}>{t(key)}</span>
                {isActive && (
                  <span className="absolute top-0 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-teal-600 rounded-full" />
                )}
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}
