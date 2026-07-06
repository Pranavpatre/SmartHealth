import { useTranslation } from 'react-i18next'

/**
 * Provenance badge: marks a UI surface as backed by real Government-of-India
 * open data (data.gov.in / PMGSY) vs synthetic demo data. Keeps the demo honest.
 */
export default function DataBadge({ variant }: { variant: 'real' | 'simulated' }) {
  const { t } = useTranslation()
  const real = variant === 'real'
  return (
    <span
      title={t(real ? 'badge.real_source' : 'badge.sim_source')}
      className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full border align-middle ${
        real
          ? 'bg-green-50 text-green-700 border-green-200'
          : 'bg-gray-100 text-gray-500 border-gray-200'
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${real ? 'bg-green-500' : 'bg-gray-400'}`} />
      {t(real ? 'badge.real' : 'badge.simulated')}
    </span>
  )
}
