import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getStateInfrastructure, getNationalSummary } from '../api/overview'
import { formatNumber } from '../lib/format'
import DataBadge from './DataBadge'

export default function NationalBeds() {
  const { t } = useTranslation()
  const { data: states = [], isLoading } = useQuery({
    queryKey: ['state-infrastructure'],
    queryFn: getStateInfrastructure,
  })
  const { data: summary } = useQuery({
    queryKey: ['national-summary'],
    queryFn: getNationalSummary,
  })

  if (isLoading) {
    return <div className="text-gray-400 text-sm p-4">{t('beds.loading')}</div>
  }
  if (states.length === 0) return null

  const tiles = summary
    ? [
        { key: 'beds.phc_beds', value: summary.phc_beds },
        { key: 'beds.chc_beds', value: summary.chc_beds },
        { key: 'beds.sdh_beds', value: summary.sub_district_beds },
        { key: 'beds.dh_beds', value: summary.district_hospital_beds },
        { key: 'beds.mc_beds', value: summary.medical_college_beds },
        { key: 'beds.total_beds', value: summary.total_beds },
      ]
    : []

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="font-semibold text-gray-800 flex items-center gap-2">{t('beds.title')} <DataBadge variant="real" /></h2>
        <span className="text-xs text-gray-400">
          {t('beds.source')}
          {summary?.as_on_date ? ` · ${t('beds.as_on', { date: summary.as_on_date })}` : ''}
        </span>
      </div>

      {/* National summary tiles */}
      {tiles.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
          {tiles.map((tile) => (
            <div key={tile.key} className="bg-teal-50 border border-teal-100 rounded-lg p-3 text-center">
              <p className="text-[11px] font-medium text-teal-700 uppercase tracking-wide">{t(tile.key)}</p>
              <p className="text-lg font-bold text-teal-900 mt-0.5">{formatNumber(tile.value)}</p>
            </div>
          ))}
        </div>
      )}

      {/* Per-state table (sorted by total beds desc, from the API) */}
      <div className="overflow-x-auto max-h-[360px] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200 sticky top-0">
            <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
              <th className="px-3 py-2">{t('beds.col_state')}</th>
              <th className="px-3 py-2 text-right">{t('beds.col_phc')}</th>
              <th className="px-3 py-2 text-right">{t('beds.col_chc')}</th>
              <th className="px-3 py-2 text-right">{t('beds.col_sdh')}</th>
              <th className="px-3 py-2 text-right">{t('beds.col_dh')}</th>
              <th className="px-3 py-2 text-right">{t('beds.col_mc')}</th>
              <th className="px-3 py-2 text-right font-bold">{t('beds.col_total')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {states.map((s) => (
              <tr key={s.state_ut} className="hover:bg-gray-50">
                <td className="px-3 py-2 font-medium text-gray-900">
                  {t(`state.${s.state_ut}`, { defaultValue: s.state_ut })}
                </td>
                <td className="px-3 py-2 text-right text-gray-600">{formatNumber(s.phc_beds)}</td>
                <td className="px-3 py-2 text-right text-gray-600">{formatNumber(s.chc_beds)}</td>
                <td className="px-3 py-2 text-right text-gray-600">{formatNumber(s.sub_district_beds)}</td>
                <td className="px-3 py-2 text-right text-gray-600">{formatNumber(s.district_hospital_beds)}</td>
                <td className="px-3 py-2 text-right text-gray-600">{formatNumber(s.medical_college_beds)}</td>
                <td className="px-3 py-2 text-right font-bold text-gray-900">{formatNumber(s.total_beds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
