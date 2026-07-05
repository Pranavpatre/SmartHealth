import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { getFacilities, type Facility } from '../api/facilities'
import { formatNumber } from '../lib/format'

type TrafficFilter = 'ALL' | 'RED' | 'YELLOW' | 'GREEN'

const TRAFFIC_LABEL_KEY: Record<TrafficFilter, string> = {
  ALL: 'status.all',
  RED: 'status.critical',
  YELLOW: 'status.at_risk',
  GREEN: 'status.good',
}

const TRAFFIC_EMOJI: Record<string, string> = {
  GREEN: '\u{1F7E2}',
  YELLOW: '\u{1F7E1}',
  RED: '\u{1F534}',
}

function scoreColor(score: number) {
  if (score >= 70) return 'text-green-700'
  if (score >= 45) return 'text-yellow-700'
  return 'text-red-700'
}

export default function FacilitiesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [trafficFilter, setTrafficFilter] = useState<TrafficFilter>('ALL')

  const { data: facilities = [], isLoading } = useQuery({
    queryKey: ['facilities'],
    queryFn: getFacilities,
    refetchInterval: 60_000,
  })

  const filtered = facilities.filter((f: Facility) => {
    const matchesSearch = f.name.toLowerCase().includes(search.toLowerCase())
    const matchesTraffic = trafficFilter === 'ALL' || f.traffic_light === trafficFilter
    return matchesSearch && matchesTraffic
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">{t('facilities.title')}</h1>
        <span className="text-sm text-gray-500">{t('facilities.count', { shown: formatNumber(filtered.length), total: formatNumber(facilities.length) })}</span>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 flex flex-wrap gap-4">
        <div className="flex-1 min-w-48">
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('facilities.search')}</label>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('facilities.search_placeholder')}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent outline-none"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">{t('facilities.status')}</label>
          <div className="flex gap-2">
            {(['ALL', 'RED', 'YELLOW', 'GREEN'] as TrafficFilter[]).map((val) => (
              <button
                key={val}
                onClick={() => setTrafficFilter(val)}
                className={`px-3 py-2 text-xs font-medium rounded-lg border transition-colors ${
                  trafficFilter === val
                    ? 'bg-teal-600 text-white border-teal-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                }`}
              >
                {val !== 'ALL' && `${TRAFFIC_EMOJI[val]} `}{t(TRAFFIC_LABEL_KEY[val])}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-400">{t('facilities.loading')}</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center text-gray-400">{t('facilities.empty')}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-3">{t('facilities.col_name')}</th>
                  <th className="px-4 py-3">{t('facilities.col_type')}</th>
                  <th className="px-4 py-3">{t('facilities.col_score')}</th>
                  <th className="px-4 py-3">{t('facilities.col_status')}</th>
                  <th className="px-4 py-3">{t('facilities.col_alerts')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filtered.map((f: Facility) => (
                  <tr
                    key={f.id}
                    className="hover:bg-gray-50 cursor-pointer transition-colors"
                    onClick={() => navigate(`/facilities/${f.id}`)}
                  >
                    <td className="px-4 py-3">
                      <span className="font-medium text-gray-900">{f.name}</span>
                      <span className="ml-2 text-xs text-gray-400">{f.code}</span>
                    </td>
                    <td className="px-4 py-3 text-gray-600">{f.facility_type}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-20 bg-gray-200 rounded-full h-1.5">
                          <div
                            className={`h-1.5 rounded-full ${
                              f.health_score >= 70 ? 'bg-green-600' : f.health_score >= 45 ? 'bg-yellow-500' : 'bg-red-600'
                            }`}
                            style={{ width: `${f.health_score}%` }}
                          />
                        </div>
                        <span className={`font-bold text-xs ${scoreColor(f.health_score)}`}>
                          {formatNumber(f.health_score)}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-lg">
                      {TRAFFIC_EMOJI[f.traffic_light] ?? '—'}
                    </td>
                    <td className="px-4 py-3">
                      {f.active_alerts > 0 ? (
                        <span className="bg-red-100 text-red-800 text-xs font-bold px-2 py-0.5 rounded-full">
                          {formatNumber(f.active_alerts)}
                        </span>
                      ) : (
                        <span className="text-gray-400 text-xs">{t('common.none')}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
