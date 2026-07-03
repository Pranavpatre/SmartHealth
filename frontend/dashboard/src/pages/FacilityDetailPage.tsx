import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getFacility, getFacilityAttendance, getFacilityBeds, getFacilityTests, getDemandForecast, type StockItem } from '../api/facilities'
import type { Alert } from '../api/alerts'
import { formatDistanceToNow } from 'date-fns'

const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-800',
  WARNING: 'bg-orange-100 text-orange-800',
  INFO: 'bg-blue-100 text-blue-800',
}

function HealthScoreGauge({ score }: { score: number }) {
  const color =
    score >= 70 ? 'text-green-700' : score >= 45 ? 'text-yellow-600' : 'text-red-700'
  const bgColor =
    score >= 70 ? 'bg-green-100' : score >= 45 ? 'bg-yellow-100' : 'bg-red-100'
  const barColor =
    score >= 70 ? 'bg-green-600' : score >= 45 ? 'bg-yellow-500' : 'bg-red-600'
  const label =
    score >= 70 ? 'Good' : score >= 45 ? 'At Risk' : 'Critical'

  return (
    <div className={`${bgColor} rounded-2xl p-6 text-center`}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">Health Score</p>
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
  if (item.days_of_stock <= 7) {
    return <span className="bg-red-100 text-red-800 text-xs font-bold px-2 py-0.5 rounded-full">Critical ({item.days_of_stock}d)</span>
  }
  if (item.days_of_stock <= 14) {
    return <span className="bg-yellow-100 text-yellow-800 text-xs font-bold px-2 py-0.5 rounded-full">Low ({item.days_of_stock}d)</span>
  }
  return <span className="bg-green-100 text-green-800 text-xs font-bold px-2 py-0.5 rounded-full">OK ({item.days_of_stock}d)</span>
}

export default function FacilityDetailPage() {
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
        Loading facility data...
      </div>
    )
  }

  if (error || !facility) {
    return (
      <div className="text-center py-16">
        <p className="text-red-600 font-medium">Failed to load facility.</p>
        <button onClick={() => navigate('/facilities')} className="mt-4 text-teal-600 underline text-sm">
          Back to Facilities
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
          Back
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
            { label: 'Bed Capacity', value: facility.bed_capacity ?? '—' },
            { label: 'Active Alerts', value: facility.active_alerts },
            { label: 'Facility Type', value: facility.facility_type },
            { label: 'Coordinates', value: hasCoords ? `${facility.lat!.toFixed(4)}, ${facility.lng!.toFixed(4)}` : '—' },
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
          <h2 className="font-semibold text-gray-800 mb-4">Score Breakdown</h2>
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
          <h2 className="font-semibold text-gray-800 mb-3">Staff Attendance (Geofenced)</h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <p className="text-xs text-gray-500">On-site today</p>
              <p className="text-2xl font-bold text-teal-700 mt-1">{attendance.present_today}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-500">Total check-ins today</p>
              <p className="text-2xl font-bold text-gray-700 mt-1">{attendance.total_today}</p>
            </div>
            <div className="text-center">
              <p className="text-xs text-gray-500">Days since on-site</p>
              <p className={`text-2xl font-bold mt-1 ${
                (attendance.days_since_last_present ?? 0) >= 3 ? 'text-red-700' : 'text-green-700'
              }`}>
                {attendance.days_since_last_present ?? '—'}
              </p>
            </div>
          </div>
          {(attendance.days_since_last_present ?? 0) >= 3 && (
            <p className="mt-3 text-sm text-red-700 font-medium">
              ⚠ Zero on-site attendance for {attendance.days_since_last_present}+ days — escalated.
            </p>
          )}
        </div>
      )}

      {/* Epidemiological demand forecast */}
      {demandForecast.length > 0 && (
        <div className="bg-amber-50 rounded-xl border border-amber-200 p-4">
          <h2 className="font-semibold text-amber-900 mb-1">
            🦟 Seasonal Demand Forecast
          </h2>
          <p className="text-xs text-amber-700 mb-3">
            Active outbreaks in this district are expected to spike demand for these categories.
          </p>
          <div className="space-y-2">
            {demandForecast.map((f, i) => (
              <div key={i} className="flex items-start gap-3 bg-white/60 rounded-lg p-2.5 border border-amber-100">
                <span className="text-sm font-bold text-amber-800 whitespace-nowrap">
                  +{Math.round((f.demand_multiplier - 1) * 100)}%
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
          <p className="text-xs font-medium text-teal-700 uppercase tracking-wide mb-2">
            Real district data — HMIS {facility.real_district_hmis_period || facility.real_district_opd_period} · {facility.district_name} district (data.gov.in)
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {facility.real_district_opd_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{facility.real_district_opd_annual.toLocaleString()}</p>
                <p className="text-xs text-teal-700">OPD visits / yr</p>
              </div>
            )}
            {facility.real_district_ipd_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{facility.real_district_ipd_annual.toLocaleString()}</p>
                <p className="text-xs text-teal-700">
                  IPD admissions / yr
                  {facility.real_district_ipd_monthly_avg != null &&
                    ` · ~${Math.round(facility.real_district_ipd_monthly_avg).toLocaleString()}/mo`}
                </p>
              </div>
            )}
            {facility.real_district_institutional_deliveries_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{facility.real_district_institutional_deliveries_annual.toLocaleString()}</p>
                <p className="text-xs text-teal-700">Institutional deliveries / yr (public)</p>
              </div>
            )}
            {facility.real_district_fully_immunized_annual != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{facility.real_district_fully_immunized_annual.toLocaleString()}</p>
                <p className="text-xs text-teal-700">Children fully immunised / yr</p>
              </div>
            )}
            {facility.real_district_stockout_rate != null && (
              <div>
                <p className="text-xl font-bold text-teal-900">{facility.real_district_stockout_rate.toFixed(2)}</p>
                <p className="text-xs text-teal-700">Essential-drug stock-out signal</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Bed Matrix */}
      {bedMatrix && bedMatrix.beds.some((b) => b.total_beds > 0) && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h2 className="font-semibold text-gray-800 mb-3">Bed Matrix</h2>
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
                    {b.total_beds > 0 ? `${Math.round(occ * 100)}% occupied` : 'n/a'}
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
          <h2 className="font-semibold text-gray-800 mb-3">
            Diagnostic Test Availability
            {testChecklist.tests.some((t) => !t.available) && (
              <span className="ml-2 text-xs font-bold text-red-700 bg-red-100 px-2 py-0.5 rounded-full">
                {testChecklist.tests.filter((t) => !t.available).length} unavailable
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
        <h2 className="font-semibold text-gray-800 mb-3">Stock Summary</h2>
        {stockSummary.length === 0 ? (
          <p className="text-gray-400 text-sm">No stock data available.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  <th className="px-3 py-2">Medicine</th>
                  <th className="px-3 py-2">Current Stock</th>
                  <th className="px-3 py-2">Reorder Level</th>
                  <th className="px-3 py-2">Days of Stock</th>
                  <th className="px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {stockSummary.map((item: StockItem) => (
                  <tr key={item.medicine_id} className="hover:bg-gray-50">
                    <td className="px-3 py-2.5 font-medium text-gray-900">{item.medicine_name}</td>
                    <td className="px-3 py-2.5 text-gray-700">{item.total_stock.toLocaleString()}</td>
                    <td className="px-3 py-2.5 text-gray-500">{item.reorder_level.toLocaleString()}</td>
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
        <h2 className="font-semibold text-gray-800 mb-3">Recent Alerts</h2>
        {facility.recent_alerts.length === 0 ? (
          <p className="text-green-700 text-sm font-medium">No recent alerts for this facility.</p>
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
                    {formatDistanceToNow(new Date(alert.created_at), { addSuffix: true })}
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
