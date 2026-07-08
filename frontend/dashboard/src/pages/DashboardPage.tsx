import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getFacilities, getFacilityStats, getFacilitiesMap, getAtRiskFacilities } from '../api/facilities'
import { getAlerts } from '../api/alerts'
import { apiClient } from '../api/client'
import { useAuthStore } from '../stores/authStore'
import FacilityMap from '../components/FacilityMap'
import NationalBeds from '../components/NationalBeds'
import AlertCard from '../components/AlertCard'
import DataBadge from '../components/DataBadge'
import InfoNote from '../components/InfoNote'
import { useAlertWebSocket } from '../hooks/useWebSocket'
import { useTranslation } from 'react-i18next'
import { formatNumber } from '../lib/format'

export default function DashboardPage() {
  const { t } = useTranslation()
  useAlertWebSocket()

  // Follow the officer's ACTUAL location instead of a static assigned district:
  // once per session, resolve GPS → nearest facility's district on the server,
  // persist it, and re-scope the dashboard. Silent if GPS is denied/unavailable.
  const qc = useQueryClient()
  const { role, districtId, setLocation } = useAuthStore()
  useEffect(() => {
    if (role !== 'DISTRICT_OFFICER') return
    if (sessionStorage.getItem('geo_scoped') || !navigator.geolocation) return
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          const { data } = await apiClient.post('/auth/me/location', {
            lat: pos.coords.latitude, lng: pos.coords.longitude,
          })
          sessionStorage.setItem('geo_scoped', '1')
          if (data.district_id && data.district_id !== districtId) {
            setLocation({
              districtId: data.district_id, districtName: data.district_name,
              stateId: data.state_id, stateName: data.state_name,
            })
            qc.invalidateQueries()
          }
        } catch { /* keep assigned scope */ }
      },
      () => {},
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 },
    )
  }, [role, districtId, setLocation, qc])

  const { data: facilities = [], isLoading: facilitiesLoading } = useQuery({
    queryKey: ['facilities'],
    queryFn: getFacilities,
    refetchInterval: 60_000,
  })

  const { data: alertsData, isLoading: alertsLoading } = useQuery({
    queryKey: ['alerts', 'OPEN'],
    queryFn: () => getAlerts({ status: 'OPEN' }),
    refetchInterval: 30_000,
  })

  // Critical count needs the server-side total, not the current page of items.
  const { data: criticalData } = useQuery({
    queryKey: ['alerts', 'OPEN', 'CRITICAL'],
    queryFn: () => getAlerts({ status: 'OPEN', severity: 'CRITICAL' }),
    refetchInterval: 30_000,
  })

  // Facility totals across ALL facilities in scope (not just the fetched map page).
  const { data: stats } = useQuery({
    queryKey: ['facility-stats'],
    queryFn: getFacilityStats,
    refetchInterval: 60_000,
  })

  // Lightweight markers for EVERY facility in scope — clustered on the map.
  const { data: mapMarkers = [] } = useQuery({
    queryKey: ['facilities-map'],
    queryFn: getFacilitiesMap,
  })

  // True national bottom-5 (lowest health score across all facilities in scope).
  const { data: atRisk = [], isLoading: atRiskLoading } = useQuery({
    queryKey: ['at-risk'],
    queryFn: () => getAtRiskFacilities(5),
    refetchInterval: 60_000,
  })

  const alerts = alertsData?.items ?? []
  const activeCount = alertsData?.total ?? 0        // total open, not just this page
  const criticalCount = criticalData?.total ?? 0
  const facilityCount = stats?.total ?? facilities.length
  // "Facilities at Risk" (Yellow + Red) replaces the old single average-score
  // tile — an aggregate mean was hard to act on, whereas a count of facilities
  // actually needing attention maps directly to something a district officer
  // can go do something about.
  const atRiskCount = stats
    ? stats.yellow + stats.red
    : facilities.filter((f) => f.health_score < 70).length

  const scoreColor = (score: number) =>
    score >= 70 ? 'text-green-700' : score >= 45 ? 'text-yellow-700' : 'text-red-700'

  return (
    <div className="space-y-6">
      {/* KPI strip */}
      <div data-tour="kpi">
        <h2 className="font-semibold text-gray-800">{t('dashboard.kpi_title')}</h2>
        <InfoNote>{t('dashboard.info_kpi')}</InfoNote>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            {
              label: t('kpi.active_alerts'),
              desc: t('kpi.active_alerts_desc'),
              value: activeCount,
              color: activeCount > 0 ? 'text-red-700' : 'text-green-700',
            },
            {
              label: t('kpi.critical'),
              desc: t('kpi.critical_desc'),
              value: criticalCount,
              color: criticalCount > 0 ? 'text-red-700' : 'text-gray-700',
            },
            {
              label: t('kpi.at_risk'),
              desc: t('kpi.at_risk_desc'),
              value: formatNumber(atRiskCount),
              color: atRiskCount > 0 ? 'text-yellow-700' : 'text-green-700',
            },
            {
              label: t('kpi.facilities'),
              desc: t('kpi.facilities_desc'),
              value: formatNumber(facilityCount),
              color: 'text-teal-700',
            },
          ].map((kpi) => (
            <div key={kpi.label} className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{kpi.label}</p>
              <p className={`text-2xl font-bold mt-1 ${kpi.color}`}>{kpi.value}</p>
              <p className="text-xs text-gray-400 mt-1">{kpi.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Map + Alert feed */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div data-tour="map" className="lg:col-span-2 bg-white rounded-xl border border-gray-200 shadow-sm p-4">
          <h2 className="font-semibold text-gray-800 mb-1">{t('dashboard.district_map')}</h2>
          <InfoNote>{t('dashboard.info_map')}</InfoNote>
          {facilitiesLoading ? (
            <div className="h-96 flex items-center justify-center text-gray-400">{t('dashboard.loading_map')}</div>
          ) : (
            <FacilityMap markers={mapMarkers} />
          )}
          <div className="flex gap-4 mt-3 text-xs text-gray-500">
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full inline-block bg-green-700"></span> {t('dashboard.legend_good')}
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full inline-block bg-yellow-700"></span> {t('dashboard.legend_at_risk')}
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full inline-block bg-red-700"></span> {t('dashboard.legend_critical')}
            </span>
          </div>
        </div>

        <div data-tour="alerts" className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 overflow-y-auto max-h-[520px]">
          <h2 className="font-semibold text-gray-800 mb-1 flex items-center gap-2">
            {t('dashboard.alert_feed')}
            {alerts.length > 0 && (
              <span className="bg-red-100 text-red-800 text-xs font-bold px-2 py-0.5 rounded-full">
                {formatNumber(alerts.length)}
              </span>
            )}
            <DataBadge variant="simulated" />
          </h2>
          <InfoNote>{t('dashboard.info_alerts')}</InfoNote>
          {alertsLoading && <p className="text-gray-400 text-sm">{t('dashboard.loading_alerts')}</p>}
          {!alertsLoading && alerts.length === 0 && (
            <p className="text-green-700 text-sm font-medium">{t('dashboard.no_alerts')}</p>
          )}
          {alerts
            .slice()
            .sort((a, b) => {
              const order: Record<string, number> = { CRITICAL: 0, WARNING: 1, INFO: 2 }
              return (order[a.severity] ?? 4) - (order[b.severity] ?? 4)
            })
            .map((alert) => (
              <AlertCard key={alert.id} alert={alert} />
            ))}
        </div>
      </div>

      {/* Bottom-5 facilities */}
      <div data-tour="at-risk" className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <h2 className="font-semibold text-gray-800 mb-1">{t('dashboard.bottom_facilities')}</h2>
        <InfoNote>{t('dashboard.info_at_risk')}</InfoNote>
        {atRiskLoading ? (
          <p className="text-gray-400 text-sm">{t('common.loading')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide border-b border-gray-100">
                  <th className="pb-2 pr-4">{t('dashboard.col_facility')}</th>
                  <th className="pb-2 pr-4">{t('dashboard.col_type')}</th>
                  <th className="pb-2 pr-4">{t('dashboard.col_score')}</th>
                  <th className="pb-2 pr-4">{t('dashboard.col_status')}</th>
                  <th className="pb-2">{t('dashboard.col_alerts')}</th>
                </tr>
              </thead>
              <tbody>
                {atRisk
                  .map((f) => (
                    <tr key={f.id} className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors">
                      <td className="py-2.5 pr-4 font-medium text-gray-900">{f.name}</td>
                      <td className="py-2.5 pr-4 text-gray-500">{f.facility_type}</td>
                      <td className="py-2.5 pr-4">
                        <span className={`font-bold ${scoreColor(f.health_score ?? 0)}`}>
                          {formatNumber(f.health_score)}
                        </span>
                        <span className="text-gray-400 text-xs">/100</span>
                      </td>
                      <td className="py-2.5 pr-4 text-lg">
                        {f.traffic_light === 'GREEN' ? (
                          <span title={t('status.good')}>&#128994;</span>
                        ) : f.traffic_light === 'YELLOW' ? (
                          <span title={t('status.at_risk')}>&#128993;</span>
                        ) : (
                          <span title={t('status.critical')}>&#128308;</span>
                        )}
                      </td>
                      <td className="py-2.5">
                        {f.active_alerts > 0 ? (
                          <span className="bg-red-100 text-red-800 text-xs font-bold px-2 py-0.5 rounded-full">
                            {formatNumber(f.active_alerts)}
                        </span>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* State-UT bed infrastructure (real data.gov.in) */}
      <div data-tour="beds">
        <NationalBeds />
      </div>
    </div>
  )
}
