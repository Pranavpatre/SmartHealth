import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '../stores/authStore'
import { getFacility, getFacilityBeds, getFacilityTests, type StockItem } from '../api/facilities'
import { getMyFacilityTransfers, type FacilityTransferItem } from '../api/redistribution'
import { formatNumber, formatDecimal } from '../lib/format'

const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-800',
  WARNING: 'bg-orange-100 text-orange-800',
  INFO: 'bg-blue-100 text-blue-800',
}

function HealthScoreGauge({ score }: { score: number }) {
  const { t } = useTranslation()
  const color = score >= 70 ? 'text-green-700' : score >= 45 ? 'text-yellow-600' : 'text-red-700'
  const bgColor = score >= 70 ? 'bg-green-100' : score >= 45 ? 'bg-yellow-100' : 'bg-red-100'
  const barColor = score >= 70 ? 'bg-green-600' : score >= 45 ? 'bg-yellow-500' : 'bg-red-600'
  const label = score >= 70 ? t('status.good') : score >= 45 ? t('status.at_risk') : t('status.critical')

  return (
    <div className={`${bgColor} rounded-2xl p-6 text-center`}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">{t('detail.health_score')}</p>
      <p className={`text-6xl font-black ${color}`}>{score}</p>
      <p className={`text-sm font-semibold ${color} mt-1`}>{label}</p>
      <div className="mt-4 w-full bg-white rounded-full h-3">
        <div className={`${barColor} h-3 rounded-full transition-all`} style={{ width: `${score}%` }} />
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

function TransferRow({ item, ownFacilityId }: { item: FacilityTransferItem; ownFacilityId: string }) {
  const { t } = useTranslation()
  const isIncoming = item.to_facility === ownFacilityId
  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-gray-50 border border-gray-100">
      <span
        className={`text-xs font-bold px-2 py-0.5 rounded-full whitespace-nowrap ${
          isIncoming ? 'bg-teal-100 text-teal-800' : 'bg-purple-100 text-purple-800'
        }`}
      >
        {isIncoming ? t('myFacility.incoming') : t('myFacility.outgoing')}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900">
          {item.medicine_name ?? '—'} · {formatNumber(item.quantity)} units
        </p>
        <p className="text-xs text-gray-500 mt-0.5">
          {item.from_facility_name ?? '—'} → {item.to_facility_name ?? '—'}
          {item.distance_km != null && ` · ${formatDecimal(item.distance_km, 1)} km`}
        </p>
      </div>
      <span
        className={`text-xs font-medium whitespace-nowrap ${
          item.status === 'APPROVED' ? 'text-green-600' : item.status === 'DEFERRED' ? 'text-gray-500' : 'text-orange-600'
        }`}
      >
        {item.status}
      </span>
    </div>
  )
}

export default function MyFacilityPage() {
  const { t } = useTranslation()
  const facilityId = useAuthStore((s) => s.facilityId)

  const { data: facility, isLoading, error } = useQuery({
    queryKey: ['facility', facilityId],
    queryFn: () => getFacility(facilityId!),
    enabled: !!facilityId,
  })

  const { data: bedMatrix } = useQuery({
    queryKey: ['facility-beds', facilityId],
    queryFn: () => getFacilityBeds(facilityId!),
    enabled: !!facilityId,
  })

  const { data: testChecklist } = useQuery({
    queryKey: ['facility-tests', facilityId],
    queryFn: () => getFacilityTests(facilityId!),
    enabled: !!facilityId,
  })

  const { data: transferPlans = [] } = useQuery({
    queryKey: ['my-facility-transfers', facilityId],
    queryFn: getMyFacilityTransfers,
    enabled: !!facilityId,
  })

  if (!facilityId) {
    return (
      <div className="text-center py-16">
        <p className="text-red-600 font-medium">{t('myFacility.noFacility')}</p>
      </div>
    )
  }

  if (isLoading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">{t('detail.loading')}</div>
  }

  if (error || !facility) {
    return (
      <div className="text-center py-16">
        <p className="text-red-600 font-medium">{t('detail.error')}</p>
      </div>
    )
  }

  const breakdown = Object.fromEntries(
    Object.entries(facility.health_score_breakdown ?? {}).filter(
      ([k, v]) => k.endsWith('_score') && k !== 'overall_score' && typeof v === 'number',
    ),
  )
  const stockSummary = facility.stock_summary ?? []
  const allTransferItems = transferPlans.flatMap((p) => p.items)

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-bold text-gray-900">{facility.name}</h1>
          <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-teal-100 text-teal-800">
            {t('myFacility.roleLabel')}
          </span>
        </div>
        <p className="text-sm text-gray-500">{facility.code} &middot; {facility.facility_type}</p>
        <p className="text-xs text-gray-400 mt-1">{t('myFacility.subtitle')}</p>
      </div>

      {/* Top row: Score + Key stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <HealthScoreGauge score={facility.health_score} />
        <div className="sm:col-span-2 grid grid-cols-2 gap-4">
          {[
            { label: t('detail.stat_beds'), value: facility.bed_capacity ?? '—' },
            { label: t('detail.stat_alerts'), value: facility.active_alerts },
            { label: t('detail.stat_type'), value: facility.facility_type },
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
          <h2 className="font-semibold text-gray-800 mb-4">{t('detail.score_breakdown')}</h2>
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

      {/* Bed Matrix */}
      {bedMatrix && bedMatrix.beds.some((b) => b.total_beds > 0) && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h2 className="font-semibold text-gray-800 mb-3">{t('detail.bed_matrix')}</h2>
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
          <h2 className="font-semibold text-gray-800 mb-3">
            {t('detail.test_availability')}
            {testChecklist.tests.some((test) => !test.available) && (
              <span className="ml-2 text-xs font-bold text-red-700 bg-red-100 px-2 py-0.5 rounded-full">
                {t('detail.unavailable', { count: testChecklist.tests.filter((test) => !test.available).length as unknown as number })}
              </span>
            )}
          </h2>
          <div className="flex flex-wrap gap-2">
            {testChecklist.tests.map((test) => (
              <span
                key={test.test_id}
                className={`text-xs font-medium px-2.5 py-1 rounded-full ${
                  test.available ? 'bg-green-50 text-green-700 border border-green-200'
                                 : 'bg-red-50 text-red-700 border border-red-200'
                }`}
              >
                {test.available ? '✓' : '✗'} {test.test_name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Stock table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <h2 className="font-semibold text-gray-800 mb-3">{t('detail.stock_summary')}</h2>
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

      {/* Redistribution transfers involving this facility (read-only) */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <h2 className="font-semibold text-gray-800">{t('myFacility.transfersTitle')}</h2>
        <p className="text-xs text-gray-500 mb-3">{t('myFacility.transfersDesc')}</p>
        {allTransferItems.length === 0 ? (
          <p className="text-gray-400 text-sm">{t('myFacility.noTransfers')}</p>
        ) : (
          <div className="space-y-2">
            {allTransferItems.map((item) => (
              <TransferRow key={item.id} item={item} ownFacilityId={facilityId} />
            ))}
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
            {facility.recent_alerts.slice(0, 5).map((alert) => (
              <div key={alert.id} className="flex items-start gap-3 p-3 rounded-lg bg-gray-50 border border-gray-100">
                <span className={`mt-0.5 text-xs font-bold px-2 py-0.5 rounded-full whitespace-nowrap ${SEVERITY_BADGE[alert.severity] ?? 'bg-gray-100 text-gray-800'}`}>
                  {alert.severity}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900">{alert.title}</p>
                  <p className="text-xs text-gray-500 mt-0.5">{alert.body}</p>
                </div>
                <span className={`text-xs font-medium whitespace-nowrap ${
                  alert.status === 'OPEN' ? 'text-orange-600' :
                  alert.status === 'RESOLVED' ? 'text-green-600' : 'text-gray-500'
                }`}>
                  {alert.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
