import { useEffect, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  browseFacilities,
  getStates,
  getDistricts,
  type FacilityBrowseRow,
} from '../api/facilities'
import { formatNumber } from '../lib/format'

type TrafficFilter = 'ALL' | 'RED' | 'YELLOW' | 'GREEN'
type TypeFilter = 'ALL' | 'PHC' | 'CHC'

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

function scoreColor(score: number | null) {
  if (score == null) return 'text-gray-400'
  if (score >= 70) return 'text-green-700'
  if (score >= 45) return 'text-yellow-700'
  return 'text-red-700'
}

const selectClass =
  'border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-teal-500 focus:border-transparent outline-none min-w-40'

export default function FacilitiesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [stateId, setStateId] = useState<number | undefined>()
  const [districtId, setDistrictId] = useState<number | undefined>()
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('ALL')
  const [trafficFilter, setTrafficFilter] = useState<TrafficFilter>('ALL')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')

  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search), 400)
    return () => clearTimeout(id)
  }, [search])

  const { data: states = [] } = useQuery({ queryKey: ['states'], queryFn: getStates })
  const { data: districts = [] } = useQuery({
    queryKey: ['districts', stateId],
    queryFn: () => getDistricts(stateId),
  })

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['browse', stateId, districtId, typeFilter, trafficFilter, debouncedSearch],
    queryFn: () =>
      browseFacilities({
        state_id: stateId,
        district_id: districtId,
        facility_type: typeFilter,
        status: trafficFilter,
        search: debouncedSearch,
        page_size: 500,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })

  const rows = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">{t('facilities.title')}</h1>
        <span className="text-sm text-gray-500">
          {t('facilities.count', { shown: formatNumber(rows.length), total: formatNumber(total) })}
        </span>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 space-y-4">
        <div className="flex flex-wrap gap-4">
          {/* State */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('facilities.state')}</label>
            <select
              className={selectClass}
              value={stateId ?? ''}
              onChange={(e) => {
                const v = e.target.value ? Number(e.target.value) : undefined
                setStateId(v)
                setDistrictId(undefined)
              }}
            >
              <option value="">{t('facilities.all_states')}</option>
              {states.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
          {/* District */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('facilities.district')}</label>
            <select
              className={selectClass}
              value={districtId ?? ''}
              onChange={(e) => setDistrictId(e.target.value ? Number(e.target.value) : undefined)}
            >
              <option value="">{t('facilities.all_districts')}</option>
              {districts.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </div>
          {/* Search */}
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
        </div>

        <div className="flex flex-wrap gap-6">
          {/* Type */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('facilities.type')}</label>
            <div className="flex gap-2">
              {(['ALL', 'PHC', 'CHC'] as TypeFilter[]).map((val) => (
                <button
                  key={val}
                  onClick={() => setTypeFilter(val)}
                  className={`px-3 py-2 text-xs font-medium rounded-lg border transition-colors ${
                    typeFilter === val
                      ? 'bg-teal-600 text-white border-teal-600'
                      : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  {val === 'ALL' ? t('status.all') : val}
                </button>
              ))}
            </div>
          </div>
          {/* Status */}
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
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-400">{t('facilities.loading')}</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-gray-400">{t('facilities.empty')}</div>
        ) : (
          <div className={`overflow-x-auto ${isFetching ? 'opacity-60 transition-opacity' : ''}`}>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-3">{t('facilities.col_name')}</th>
                  <th className="px-4 py-3">{t('facilities.col_type')}</th>
                  <th className="px-4 py-3">{t('facilities.col_district')}</th>
                  <th className="px-4 py-3">{t('facilities.col_score')}</th>
                  <th className="px-4 py-3">{t('facilities.col_status')}</th>
                  <th className="px-4 py-3 text-right">{t('facilities.col_doctors')}</th>
                  <th className="px-4 py-3 text-right">{t('facilities.col_patients')}</th>
                  <th className="px-4 py-3 text-right">{t('facilities.col_stockout')}</th>
                  <th className="px-4 py-3 text-right">{t('facilities.col_beds')}</th>
                  <th className="px-4 py-3">{t('facilities.col_alerts')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((f: FacilityBrowseRow) => (
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
                    <td className="px-4 py-3 text-gray-600">{f.district_name ?? '—'}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-16 bg-gray-200 rounded-full h-1.5">
                          <div
                            className={`h-1.5 rounded-full ${
                              (f.health_score ?? 0) >= 70 ? 'bg-green-600' : (f.health_score ?? 0) >= 45 ? 'bg-yellow-500' : 'bg-red-600'
                            }`}
                            style={{ width: `${f.health_score ?? 0}%` }}
                          />
                        </div>
                        <span className={`font-bold text-xs ${scoreColor(f.health_score)}`}>
                          {f.health_score != null ? formatNumber(f.health_score) : '—'}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-lg">{f.traffic_light ? TRAFFIC_EMOJI[f.traffic_light] : '—'}</td>
                    <td className="px-4 py-3 text-right text-gray-700">
                      {f.doctors_present != null ? formatNumber(f.doctors_present) : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-700">
                      {f.patients != null ? formatNumber(f.patients) : '—'}
                    </td>
                    <td className={`px-4 py-3 text-right font-semibold ${scoreColor(f.stockout_score)}`}>
                      {f.stockout_score != null ? formatNumber(Math.round(f.stockout_score)) : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-700">
                      {f.beds_occupied != null
                        ? `${formatNumber(f.beds_occupied)}/${formatNumber(f.bed_capacity)}`
                        : formatNumber(f.bed_capacity)}
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
