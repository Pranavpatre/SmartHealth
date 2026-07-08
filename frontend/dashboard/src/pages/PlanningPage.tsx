import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getRefills, getCapacity, getDoctorRedistribution, downloadRefillsCsv } from '../api/planning'
import { getStates, getDistricts } from '../api/facilities'
import { useAuthStore } from '../stores/authStore'
import { formatNumber } from '../lib/format'

const selectClass =
  'border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-teal-500 focus:border-transparent outline-none min-w-40'

const URGENCY: Record<string, string> = {
  HIGH: 'bg-red-100 text-red-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW: 'bg-gray-100 text-gray-600',
}

// Pre-emptive planning: which facilities will run short (seasonally adjusted)
// within the horizon, what to order and by when (downloadable for suppliers),
// plus longer-term beds/doctors gaps.
export default function PlanningPage() {
  const { t } = useTranslation()
  const { role, stateId: uState } = useAuthStore()
  const isNational = role === 'SUPERADMIN'
  const isState = role === 'STATE_ADMIN'
  const isScoped = !isNational && !isState  // district officer → auto-scoped

  const [stateId, setStateId] = useState<number | undefined>(isState ? uState ?? undefined : undefined)
  const [districtId, setDistrictId] = useState<number | undefined>(undefined)
  const [downloading, setDownloading] = useState(false)

  const scope = isScoped ? {} : { state_id: stateId, district_id: districtId }
  const ready = isScoped || stateId != null || districtId != null
  const scopeKey = [isScoped, stateId, districtId]

  const { data: states = [] } = useQuery({ queryKey: ['states'], queryFn: getStates, enabled: isNational })
  const { data: districts = [] } = useQuery({
    queryKey: ['districts', stateId], queryFn: () => getDistricts(stateId), enabled: !isScoped,
  })

  const { data: refills, isLoading } = useQuery({
    queryKey: ['planning-refills', ...scopeKey],
    queryFn: () => getRefills(scope), enabled: ready, refetchInterval: 300_000,
  })
  const { data: capacity = [] } = useQuery({
    queryKey: ['planning-capacity', ...scopeKey],
    queryFn: () => getCapacity(scope), enabled: ready,
  })
  const { data: moves = [] } = useQuery({
    queryKey: ['planning-docmoves', ...scopeKey],
    queryFn: () => getDoctorRedistribution(scope), enabled: ready,
  })

  const items = refills?.items ?? []
  const highCount = items.filter((i) => i.urgency === 'HIGH').length
  const doctorItems = capacity.filter((c) => c.concern === 'DOCTORS')
  const bedItems = capacity.filter((c) => c.concern === 'BEDS')

  const onDownload = async () => {
    setDownloading(true)
    try { await downloadRefillsCsv(scope) } finally { setDownloading(false) }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-bold text-gray-900">{t('planning.title', 'Planning')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {t('planning.subtitle', 'Pre-emptive stock & capacity actionables — order before the shortage.')}
          </p>
        </div>
        <button
          onClick={onDownload}
          disabled={!ready || !items.length || downloading}
          className="bg-teal-600 text-white font-semibold px-4 py-2.5 rounded-lg hover:bg-teal-700 disabled:opacity-40 transition-colors text-sm"
        >
          {downloading ? t('planning.preparing', 'Preparing…') : t('planning.download_csv', '⬇ Download supplier CSV')}
        </button>
      </div>

      {/* Scope pickers (national picks state+district; state admin narrows by district) */}
      {!isScoped && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 flex flex-wrap gap-4">
          {isNational && (
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">{t('facilities.state')}</label>
              <select className={selectClass} value={stateId ?? ''}
                onChange={(e) => { setStateId(e.target.value ? Number(e.target.value) : undefined); setDistrictId(undefined) }}>
                <option value="">{t('facilities.all_states')}</option>
                {states.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('facilities.district')}</label>
            <select className={selectClass} value={districtId ?? ''}
              onChange={(e) => setDistrictId(e.target.value ? Number(e.target.value) : undefined)}>
              <option value="">{t('facilities.all_districts')}</option>
              {districts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
        </div>
      )}

      {!ready ? (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-gray-400 text-sm">
          {t('planning.select_scope', 'Select a state or district to generate the plan.')}
        </div>
      ) : (
        <div className="space-y-3">
          {/* 1) Stock refills — collapsed by default (this list can be long) */}
          <Section
            title={t('planning.refills_title', 'Stock refills needed')}
            count={items.length}
            tone={items.length ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-500'}
            subtitle={refills
              ? `${t('planning.horizon_note', { defaultValue: 'next {{d}} days', d: refills.horizon_days })}${highCount ? ` · ${highCount} ${t('planning.urgent', 'urgent')}` : ''}`
              : undefined}
          >
            {isLoading ? (
              <p className="text-gray-400 text-sm p-2">{t('facilities.loading')}</p>
            ) : items.length === 0 ? (
              <p className="text-gray-400 text-sm p-2">{t('planning.no_refills', 'No refills projected in this window — all facilities are covered.')}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                      <th className="px-3 py-2">{t('planning.col_facility', 'Facility')}</th>
                      <th className="px-3 py-2">{t('planning.col_item', 'Item')}</th>
                      <th className="px-3 py-2 text-right">{t('planning.col_current', 'In stock')}</th>
                      <th className="px-3 py-2 text-right">{t('planning.col_order', 'Order qty')}</th>
                      <th className="px-3 py-2">{t('planning.col_deliver', 'Deliver by')}</th>
                      <th className="px-3 py-2">{t('planning.col_urgency', 'Urgency')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {items.map((i, idx) => (
                      <tr key={`${i.facility_id}-${i.item}-${idx}`} className="hover:bg-gray-50">
                        <td className="px-3 py-2">
                          <span className="font-medium text-gray-900">{i.facility}</span>
                          <span className="block text-xs text-gray-400 truncate max-w-56" title={i.address}>{i.address || i.district}</span>
                        </td>
                        <td className="px-3 py-2 text-gray-700">{i.item} <span className="text-xs text-gray-400">{i.category}</span></td>
                        <td className="px-3 py-2 text-right text-gray-600">{formatNumber(i.current_stock)} {i.unit}</td>
                        <td className="px-3 py-2 text-right font-semibold text-gray-900">{formatNumber(i.order_qty)}</td>
                        <td className="px-3 py-2 text-gray-700">{i.deliver_by}</td>
                        <td className="px-3 py-2"><span className={`text-xs font-bold px-2 py-0.5 rounded-full ${URGENCY[i.urgency]}`}>{i.urgency}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          {/* 2) Doctor requirement + redistribution plan */}
          <Section
            title={t('planning.doctors_title', 'Doctor requirement')}
            count={doctorItems.length}
            tone={doctorItems.length ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'}
            subtitle={moves.length ? `${moves.length} ${t('planning.redistribution_moves', 'redistribution moves')}` : undefined}
          >
            {doctorItems.length === 0 ? (
              <p className="text-gray-400 text-sm p-2">{t('planning.no_doctor_gap', 'No facilities under-staffed on doctors.')}</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {doctorItems.map((c) => (
                  <div key={c.facility_id} className="border border-gray-200 rounded-xl p-3">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-gray-900">{c.facility}</span>
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">{c.metric}</span>
                    </div>
                    <p className="text-sm text-gray-600 mt-1">{c.detail}</p>
                    <p className="text-xs text-gray-400 mt-0.5">{c.address || c.district}</p>
                  </div>
                ))}
              </div>
            )}

            {/* Doctor redistribution plan — move surplus doctors to nearby shortages */}
            <div className="mt-4 pt-3 border-t border-gray-100">
              <h3 className="text-sm font-semibold text-gray-700 mb-2">
                {t('planning.redistribution_title', 'Doctor redistribution plan')}
                <span className="ml-2 text-xs font-normal text-gray-400">{t('planning.redistribution_note', 'surplus → nearby shortage (≤ 50 km)')}</span>
              </h3>
              {moves.length === 0 ? (
                <p className="text-gray-400 text-sm">{t('planning.no_moves', 'No nearby surplus available to redistribute.')}</p>
              ) : (
                <ul className="space-y-1.5">
                  {moves.map((m, idx) => (
                    <li key={idx} className="flex items-center gap-2 text-sm bg-blue-50/60 rounded-lg px-3 py-2">
                      <span className="text-blue-700 font-bold shrink-0">🩺 {m.doctors}</span>
                      <span className="text-gray-700 truncate">
                        <span className="font-medium">{m.from_facility}</span>
                        <span className="text-gray-400"> ({m.from_district})</span>
                        {' → '}
                        <span className="font-medium">{m.to_facility}</span>
                        <span className="text-gray-400"> ({m.to_district})</span>
                      </span>
                      <span className="ml-auto text-xs text-gray-400 shrink-0">{m.distance_km} km</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Section>

          {/* 3) Beds requirement */}
          <Section
            title={t('planning.beds_title', 'Beds requirement')}
            count={bedItems.length}
            tone={bedItems.length ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-500'}
          >
            {bedItems.length === 0 ? (
              <p className="text-gray-400 text-sm p-2">{t('planning.no_bed_gap', 'No facilities near bed capacity.')}</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {bedItems.map((c) => (
                  <div key={c.facility_id} className="border border-gray-200 rounded-xl p-3">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-gray-900">{c.facility}</span>
                      <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-purple-100 text-purple-700">{c.metric}</span>
                    </div>
                    <p className="text-sm text-gray-600 mt-1">{c.detail}</p>
                    <p className="text-xs text-gray-400 mt-0.5">{c.address || c.district}</p>
                  </div>
                ))}
              </div>
            )}
          </Section>
        </div>
      )}
    </div>
  )
}

// Collapsible section — collapsed by default so the (often long) refill list
// doesn't bury the doctor/bed sections. Header shows a count so the user knows
// what's inside without expanding.
function Section({ title, count, tone, subtitle, children }: {
  title: string
  count?: number
  tone?: string
  subtitle?: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-4 py-3 text-left"
      >
        <span className="text-gray-400 text-xs w-3">{open ? '▾' : '▸'}</span>
        <span className="font-semibold text-gray-800">{title}</span>
        {count != null && (
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${tone ?? 'bg-gray-100 text-gray-500'}`}>{count}</span>
        )}
        {subtitle && <span className="text-xs text-gray-400 ml-1">{subtitle}</span>}
      </button>
      {open && <div className="px-4 pb-4 border-t border-gray-100 pt-3">{children}</div>}
    </div>
  )
}
