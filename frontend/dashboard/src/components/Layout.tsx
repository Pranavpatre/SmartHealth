import { Outlet, NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '../stores/authStore'

const LANGUAGES = [
  { code: 'en', label: 'EN' },
  { code: 'hi', label: 'हि' },
  { code: 'mr', label: 'म' },
  { code: 'gu', label: 'ગુ' },
  { code: 'ta', label: 'த' },
  { code: 'te', label: 'తె' },
  { code: 'kn', label: 'ಕ' },
  { code: 'bn', label: 'বা' },
]

export default function Layout() {
  const { t, i18n } = useTranslation()
  const { name, logout } = useAuthStore()

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `px-4 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive ? 'bg-teal-600 text-white' : 'text-gray-700 hover:bg-gray-100'
    }`

  return (
    <div className="min-h-screen flex flex-col">
      <nav className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <span className="font-bold text-teal-700 text-lg tracking-tight">SmartHealth</span>
          <NavLink to="/dashboard" className={navClass}>{t('nav.dashboard')}</NavLink>
          <NavLink to="/facilities" className={navClass}>{t('nav.facilities')}</NavLink>
          <NavLink to="/redistribution" className={navClass}>{t('nav.redistribution')}</NavLink>
          <NavLink to="/assistant" className={navClass}>{t('nav.assistant')}</NavLink>
        </div>
        <div className="flex items-center gap-3">
          {/* Language switcher */}
          <div className="flex items-center gap-1">
            {LANGUAGES.map((lang) => (
              <button
                key={lang.code}
                onClick={() => i18n.changeLanguage(lang.code)}
                className={`text-xs px-2 py-1 rounded transition-colors ${
                  i18n.language === lang.code
                    ? 'bg-teal-100 text-teal-700 font-semibold'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
              >
                {lang.label}
              </button>
            ))}
          </div>
          <span className="text-sm text-gray-600 border-l border-gray-200 pl-3">{name}</span>
          <button
            onClick={logout}
            className="text-sm text-red-600 hover:text-red-800 font-medium transition-colors"
          >
            {t('nav.logout')}
          </button>
        </div>
      </nav>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
