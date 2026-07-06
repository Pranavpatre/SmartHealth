import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getFacility, getFacilityAttendance, getFacilityBeds, getFacilityTests, getDemandForecast, type StockItem } from '../api/facilities'
import type { Alert } from '../api/alerts'
import { formatNumber, formatDecimal, formatRelativeTime } from '../lib/format'
import DataBadge from '../components/DataBadge'

const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-800',
  WARNING: 'bg-orange-100 text-orange-800',
  INFO: 'bg-blue-100 text-blue-800',
}

function HealthScoreGauge({ score }: { score: number }) {
  const { t } = useTranslation()
  const color =
    score >= 70 ? 'text-green-700' : score >= 45 ? 'text-yellow-600' : 'text-red-700'
  const bgColor =
    score >= 70 ? 'bg-green-100' : score >= 45 ? 'bg-yellow-100' : 'bg-red-100'
  const barColor =
    score >= 70 ? 'bg-green-600' : score >= 45 ? 'bg-yellow-500' : 'bg-red-600'
  const label =
    score >= 70 ? t('status.good') : score >= 45 ? t('status.at_risk') : t('status.critical')

  return (
    <div className={`${bgColor} rounded-2xl p-6 text-center`}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">{t('detail.health_score')}</p>
      <p className={`text-6xl font-black ${color}`}>{score}</p>
      <p className={`text-sm font-semibold ${color} mt-1`}>{label}</p>
      <div className="mt-4 w-full bg-white rounded-full h-3">
        <div
          className={`${barColor} h-3 rounded-full transition-all`}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  )
}

function StockStatusBadge({ item }: { item: StockItem }) {
  const { t } = useTranslation()
  if (item.days_of_stock <= 7) {
    return <span className="bg-red-100 text-red-800 text-xs font-bold px-2 py-0.5 rounded-full">{t('detail.stock_critical', { days: formatNumber(item.days_of_stock) })}</span>
  }
  if (item.days_of_stock <= 14) {
    return <span className="bg-yellow-100 text-yellow-800 text-xs font-bold px-2 py-0.5 rounded-full">{t('detail.stock_low', { days: formatNumber(item.days_of_stock) })}</span>
  }
  return <span className="bg-green-100 text-green-800 text-xs font-bold px-2 py-0.5 rounded-full">{t('detail.stock_ok', { days: formatNumber(item.days_of_stock) })}</span>
}

export default function FacilityDetailPage() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { data: facility, isLoading, error } = useQuery({
    queryKey: ['facility', id],
    queryFn: () => getFacility(id!),
    enabled: !!id,
  })

  const { data: attendance } = useQuery({
    queryKey: ['facility-attendance', id],
    queryFn: () => getFacilityAttendance(id!),
    enabled: !!id,
  })

  const { data: bedMatrix } = useQuery({
    queryKey: ['facility-beds', id],
    queryFn: () => getFacilityBeds(id!),
    enabled: !!id,
  })

  const { data: testChecklist } = useQuery({
    queryKey: ['facility-tests', id],
    queryFn: () => getFacilityTests(id!),
    enabled: !!id,
  })

  const { data: demandForecast = [] } = useQuery({
    queryKey: ['facility-demand', id],
    queryFn: () => getDemandForecast(id!),
    enabled: !!id,
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        {t('detail.loading')}
      </div>
    )
  }

  if (error || !facility) {
    return (
      <div className="text-center py-16">
        <p className="text-red-600 font-medium">{t('detail.error')}</p>
        <button onClick={() => navigate('/facilities')} className="mt-4 text-teal-600 underline text-sm">
          {t('detail.back_to_facilities')}
        </button>
      </div>
    )
  }

  // Only show numeric per-dimension scores (backend also sends time/status/overall).
  const breakdown = Object.fromEntries(
    Object.entries(facility.health_score_breakdown ?? {}).filter(
      ([k, v]) => k.endsWith('_score') && k !== 'overall_score' && typeof v === 'number',
    ),
  )
  const stockSummary = facility.stock_summary ?? []
  const hasCoords = facility.lat != null && facility.lng != null

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate('/facilities')}
          className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          {t('common.back')}
        </button>
        <div>
          <h1 className="text-xl font-bold text-gray-900">{facility.name}</h1>
          <p className="text-sm text-gray-500">{facility.code} &middot; {facility.facility_type}</p>
        </div>
      </div>

      {/* Top row: Score + Key stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <HealthScoreGauge score={facility.health_score} />

        <div className="sm:col-span-2 grid grid-cols-2 gap-4">
          {[
            { label: t('detail.stat_beds'), value: facility.bed_capacity ?? '—' },
            { label: t('detail.stat_alerts'), value: facility.active_alerts },
            { label: t('detail.stat_type'), value: facility.facility_type },
            { label: t('detail.stat_coords'), value: hasCoords ? `${formatDecimal(facility.lat!, 4)}, ${formatDecimal(facility.lng!, 4)}` : '—' },
          ].map((stat) => (
            <div key={stat.label} className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{stat.label}</p>
              <p className="text-lg font-bold text-gray-900 mt-1">{stat.value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Score breakdown */}
      {Object.keys(breakdown).length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h2 className="font-semibold text-gray-800 mb-4 flex items-center gap-2">{t('detail.score_breakdown')} <DataBadge variant="simulated" /></h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {Object.entries(breakdown).map(([key, val]) => (
              <div key={key} className="text-center">
                <p className="text-xs text-gray-500 capitalize">{key.replace(/_/g, ' ')}</p>
                <p className="text-2xl font-bold text-teal-700 mt-1">{val}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Staff attendance (geofenced) */}
      {attendance && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h2 className="font-semibold text-gray-800 mb-3 flex items-center gap-2">{t('detail.attendance_title')} <DataBadge variant="simulated" /></h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <p className="text-xs text-gray-500">{t('detail.onsite_today')}</p>
              <p className="text-2xl font-bold text-teal-700 mt-1">{attendance.present_today}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-500">{t('detail.checkins_today')}</p>
              <p className="text-2xl font-bold text-gray-700 mt-1">{attendance.total_today}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-500">{t('detail.days_since_onsite')}</p>
              <p className={`text-2xl font-bold mt-1 ${
                (attendance.days_since_last_present ?? 0) >= 3 ? 'text-red-700' : 'text-green-700'
              }`}>
                {attendance.days_since_last_present ?? '—'}
              </p>
            </div>
          </div>
          {(attendance.days_since_last_present ?? 0) >= 3 && (
            <p className="mt-3 text-sm text-red-700 font-medium">
              {t('detail.zero_attendance', { days: formatNumber(attendance.days_since_last_present) })}
            </p>
          )}
        </div>
      )}

      {/* Epidemiological demand forecast */}
      {demandForecast.length > 0 && (
        <div className="bg-amber-50 rounded-xl border border-amber-200 p-4">
          <h2 className="font-semibold text-amber-900 mb-1 flex items-center gap-2">
            {t('detail.forecast_title')} <DataBadge variant="simulated" />
          </h2>
          <p className="text-xs text-amber-700 mb-3">
            {t('detail.forecast_desc')}
          </p>
          <div className="space-y-2">
            {demandForecast.map((f, i) => (
              <div key={i} className="flex items-start gap-3 bg-white/60 rounded-lg p-2.5 border border-amber-100">
                <span className="text-sm font-bold text-amber-800 whitespace-nowrap">
                  +{formatNumber(Math.round((f.demand_multiplier - 1) * 100))}%
                </span>
                <div>
                  <p className="text-sm font-medium text-gray-900">
                    {f.medicine_category} — {f.disease} <span className="text-xs text-gray-500">({f.severity})</span>
                  </p>
                  <p className="text-xs text-gray-500">{f.affected_medicines.join(', ')}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Real district HMIS metrics (data.gov.in) */}
      {(facility.real_district_opd_annual != null ||
        facility.real_district_ipd_annual != null ||
        facility.real_district_stockout_rate != null ||
        facility.real_district_fully_immunized_annual != null ||
        facility.real_district_institutional_deliveries_annual != null) && (
        <div className="bg-teal-50 rounded-xl border border-teal-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <p className="text-xs font-medium text-teal-700 uppercase tracking-wide">
              {t('detail.hmis_caption', {
                period: facility.real_district_hmis_period || facility.real_district_opd_period,
                district: facility.district_name,
              })}
            </p>
            <DataBadge variant="real" />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {facility.real_district_opd_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{formatNumber(facility.real_district_opd_annual)}</p>
                <p className="text-xs text-teal-700">{t('detail.opd_year')}</p>
              </div>
            )}
            {facility.real_district_ipd_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{formatNumber(facility.real_district_ipd_annual)}</p>
                <p className="text-xs text-teal-700">
                  {t('detail.ipd_year')}
                  {facility.real_district_ipd_monthly_avg != null &&
                    ` · ~${formatNumber(Math.round(facility.real_district_ipd_monthly_avg))}${t('detail.per_month')}`}
                </p>
              </div>
            )}
            {facility.real_district_institutional_deliveries_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{formatNumber(facility.real_district_institutional_deliveries_annual)}</p>
                <p className="text-xs text-teal-700">{t('detail.deliveries_year')}</p>
              </div>
            )}
            {facility.real_district_fully_immunized_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{formatNumber(facility.real_district_fully_immunized_annual)}</p>
                <p className="text-xs text-teal-700">{t('detail.immunised_year')}</p>
              </div>
            )}
            {facility.real_district_stockout_rate != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{formatDecimal(facility.real_district_stockout_rate, 2)}</p>
                <p className="text-xs text-teal-700">{t('detail.stockout_signal')}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Bed Matrix */}
      {bedMatrix && bedMatrix.beds.some((b) => b.total_beds > 0) && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h2 className="font-semibold text-gray-800 mb-3 flex items-center gap-2">{t('detail.bed_matrix')} <DataBadge variant="simulated" /></h2>
          <div className="grid grid-cols-3 gap-4">
            {bedMatrix.beds.map((b) => {
              const occ = b.total_beds > 0 ? b.occupied_beds / b.total_beds : 0
              return (
                <div key={b.bed_type} className="border border-gray-100 rounded-lg p-3 text-center">
                  <p className="text-xs font-medium text-gray-500 uppercase">{b.bed_type}</p>
                  <p className="text-2xl font-bold text-gray-900 mt-1">
                    {b.occupied_beds}<span className="text-base text-gray-400">/{b.total_beds}</span>
                  </p>
                  <p className={`text-xs font-medium mt-1 ${occ >= 0.9 ? 'text-red-600' : occ >= 0.7 ? 'text-yellow-600' : 'text-green-600'}`}>
                    {b.total_beds > 0 ? t('detail.occupied', { pct: formatNumber(Math.round(occ * 100)) }) : t('common.na')}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Test Availability */}
      {testChecklist && testChecklist.tests.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h2 className="font-semibold text-gray-800 mb-3 flex items-center gap-2">
            {t('detail.test_availability')}
            <DataBadge variant="simulated" />
            {testChecklist.tests.some((test) => !test.available) && (
              <span className="ml-2 text-xs font-bold text-red-700 bg-red-100 px-2 py-0.5 rounded-full">
                {t('detail.unavailable', { count: formatNumber(testChecklist.tests.filter((test) => !test.available).length) as unknown as number })}
              </span>
            )}
          </h2>
          <div className="flex flex-wrap gap-2">
            {testChecklist.tests.map((t) => (
              <span
                key={t.test_id}
                className={`text-xs font-medium px-2.5 py-1 rounded-full ${
                  t.available ? 'bg-green-50 text-green-700 border border-green-200'
                              : 'bg-red-50 text-red-700 border border-red-200'
                }`}
              >
                {t.available ? '✓' : '✗'} {t.test_name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Stock table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <h2 className="font-semibold text-gray-800 mb-3 flex items-center gap-2">{t('detail.stock_summary')} <DataBadge variant="simulated" /></h2>
        {stockSummary.length === 0 ? (
          <p className="text-gray-400 text-sm">{t('detail.no_stock')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  <th className="px-3 py-2">{t('detail.col_medicine')}</th>
                  <th className="px-3 py-2">{t('detail.col_current_stock')}</th>
                  <th className="px-3 py-2">{t('detail.col_reorder')}</th>
                  <th className="px-3 py-2">{t('detail.col_days_stock')}</th>
                  <th className="px-3 py-2">{t('detail.col_status')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {stockSummary.map((item: StockItem) => (
                  <tr key={item.medicine_id} className="hover:bg-gray-50">
                    <td className="px-3 py-2.5 font-medium text-gray-900">{item.medicine_name}</td>
                    <td className="px-3 py-2.5 text-gray-700">{formatNumber(item.total_stock)}</td>
                    <td className="px-3 py-2.5 text-gray-500">{formatNumber(item.reorder_level)}</td>
                    <td className="px-3 py-2.5">
                      <span className={`font-semibold ${
                        item.days_of_stock <= 7 ? 'text-red-700' :
                        item.days_of_stock <= 14 ? 'text-yellow-700' : 'text-green-700'
                      }`}>
                        {item.days_of_stock}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <StockStatusBadge item={item} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent alerts */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <h2 className="font-semibold text-gray-800 mb-3">{t('detail.recent_alerts')}</h2>
        {facility.recent_alerts.length === 0 ? (
          <p className="text-green-700 text-sm font-medium">{t('detail.no_recent_alerts')}</p>
        ) : (
          <div className="space-y-2">
            {facility.recent_alerts.slice(0, 5).map((alert: Alert) => (
              <div key={alert.id} className="flex items-start gap-3 p-3 rounded-lg bg-gray-50 border border-gray-100">
                <span className={`mt-0.5 text-xs font-bold px-2 py-0.5 rounded-full whitespace-nowrap ${SEVERITY_BADGE[alert.severity] ?? 'bg-gray-100 text-gray-800'}`}>
                  {alert.severity}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900">{alert.title}</p>
                  <p className="text-xs text-gray-500 mt-0.5">{alert.body}</p>
                </div>
                <div className="text-right shrink-0">
                  <span className={`text-xs font-medium ${
                    alert.status === 'OPEN' ? 'text-orange-600' :
                    alert.status === 'RESOLVED' ? 'text-green-600' : 'text-gray-500'
                  }`}>
                    {alert.status}
                  </span>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {formatRelativeTime(new Date(alert.created_at))}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
