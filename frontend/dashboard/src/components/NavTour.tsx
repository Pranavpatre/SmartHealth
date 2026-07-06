import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '../stores/authStore'

interface TourStep {
  anchor?: string
  icon: string
  titleKey: string
  descKey: string
}

const STEPS: TourStep[] = [
  { icon: '👋', titleKey: 'tour.welcome_title', descKey: 'tour.welcome_desc' },
  { anchor: 'kpi', icon: '📊', titleKey: 'dashboard.kpi_title', descKey: 'dashboard.info_kpi' },
  { anchor: 'map', icon: '🗺️', titleKey: 'dashboard.district_map', descKey: 'dashboard.info_map' },
  { anchor: 'alerts', icon: '🔔', titleKey: 'dashboard.alert_feed', descKey: 'dashboard.info_alerts' },
  { anchor: 'at-risk', icon: '⚠️', titleKey: 'dashboard.bottom_facilities', descKey: 'dashboard.info_at_risk' },
  { anchor: 'nearest', icon: '📍', titleKey: 'nearest.title', descKey: 'nearest.desc' },
  { anchor: 'beds', icon: '🛏️', titleKey: 'beds.title', descKey: 'beds.desc' },
]

// Best-effort BCP-47 tags for the Web Speech API — actual voice availability
// depends on the browser/OS's installed TTS voices for each language, not
// something we control. The visible step text is the source of truth; voice
// is a bonus, never a requirement to understand the tour.
const SPEECH_LANG: Record<string, string> = {
  en: 'en-IN', hi: 'hi-IN', mr: 'mr-IN', gu: 'gu-IN', pa: 'pa-IN',
  ta: 'ta-IN', ml: 'ml-IN', te: 'te-IN', kn: 'kn-IN', bn: 'bn-IN',
}

function speak(text: string, lang: string) {
  try {
    if (!('speechSynthesis' in window)) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = lang
    window.speechSynthesis.speak(utterance)
  } catch {
    /* speechSynthesis unavailable/blocked — silent no-op, visible text still works */
  }
}

export default function NavTour() {
  const { t, i18n } = useTranslation()
  const showNavTour = useAuthStore((s) => s.showNavTour)
  const dismissNavTour = useAuthStore((s) => s.dismissNavTour)
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (showNavTour) setStep(0)
  }, [showNavTour])

  useEffect(() => {
    if (!showNavTour) return
    const current = STEPS[step]
    if (current.anchor) {
      document
        .querySelector(`[data-tour="${current.anchor}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    const lang = SPEECH_LANG[i18n.language] ?? 'en-IN'
    speak(`${t(current.titleKey)}. ${t(current.descKey)}`, lang)
    return () => {
      try { window.speechSynthesis.cancel() } catch { /* noop */ }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, showNavTour, i18n.language])

  if (!showNavTour) return null

  const current = STEPS[step]
  const isLast = step === STEPS.length - 1

  const close = () => {
    try { window.speechSynthesis.cancel() } catch { /* noop */ }
    dismissNavTour()
  }

  return (
    <div className="fixed inset-0 z-[9999] bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 space-y-4">
        <div className="flex items-start gap-3">
          <span className="text-3xl" aria-hidden>{current.icon}</span>
          <div className="flex-1">
            <h2 className="text-lg font-bold text-gray-900">{t(current.titleKey)}</h2>
            <p className="text-sm text-gray-500 mt-1">{t(current.descKey)}</p>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs text-gray-400">
          <span>{t('tour.stepOf', { current: step + 1, total: STEPS.length })}</span>
          <button
            onClick={() => speak(`${t(current.titleKey)}. ${t(current.descKey)}`, SPEECH_LANG[i18n.language] ?? 'en-IN')}
            className="flex items-center gap-1 text-teal-600 font-semibold hover:text-teal-700"
          >
            🔊 {t('tour.listen')}
          </button>
        </div>

        <div className="flex items-center justify-between pt-2 border-t border-gray-100">
          <button onClick={close} className="text-sm text-gray-500 hover:text-gray-700">
            {t('tour.skip')}
          </button>
          <div className="flex gap-2">
            {step > 0 && (
              <button
                onClick={() => setStep((s) => s - 1)}
                className="px-4 py-2 rounded-lg text-sm font-semibold text-gray-700 hover:bg-gray-100"
              >
                {t('tour.prev')}
              </button>
            )}
            <button
              onClick={() => (isLast ? close() : setStep((s) => s + 1))}
              className="px-4 py-2 rounded-lg text-sm font-semibold bg-teal-600 text-white hover:bg-teal-700 transition-colors"
            >
              {isLast ? t('tour.finish') : t('tour.next')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
