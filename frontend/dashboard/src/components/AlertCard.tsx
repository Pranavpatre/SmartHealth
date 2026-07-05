import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { acknowledgeAlert, type Alert } from '../api/alerts'
import { approvePlan, deferPlan } from '../api/redistribution'
import { formatNumber } from '../lib/format'
import clsx from 'clsx'

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: 'border-red-500 bg-red-50',
  HIGH: 'border-orange-400 bg-orange-50',
  MEDIUM: 'border-yellow-400 bg-yellow-50',
  LOW: 'border-blue-300 bg-blue-50',
}

const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-800',
  WARNING: 'bg-orange-100 text-orange-800',
  INFO: 'bg-blue-100 text-blue-800',
}

interface Props {
  alert: Alert
  planId?: string
}

export default function AlertCard({ alert, planId }: Props) {
  const qc = useQueryClient()
  const { t } = useTranslation()

  // Render the alert from its structured message_key + params (localizable),
  // formatting numeric params to the active locale's numerals and translating
  // the anomaly direction word. Falls back to the English title/body for older
  // rows that predate structured alerts.
  const localized = (field: 'title' | 'body') => {
    const fallback = field === 'title' ? alert.title : alert.body
    if (!alert.message_key) return fallback
    const raw = alert.message_params ?? {}
    const params: Record<string, string> = {}
    for (const [k, v] of Object.entries(raw)) {
      params[k] = typeof v === 'number' ? formatNumber(v) : String(v)
    }
    if (raw.direction != null) params.direction = t(`alert.dir_${raw.direction}`)
    return t(`${alert.message_key}.${field}`, { ...params, defaultValue: fallback })
  }

  const ackMutation = useMutation({
    mutationFn: () => acknowledgeAlert(alert.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const approveMutation = useMutation({
    mutationFn: () => approvePlan(planId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['redistribution-plans'] })
      qc.invalidateQueries({ queryKey: ['facilities'] })
    },
  })

  const deferMutation = useMutation({
    mutationFn: () => deferPlan(planId!, 'Deferred from dashboard'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['redistribution-plans'] }),
  })

  return (
    <div className={clsx('border-l-4 rounded-lg p-4 mb-3 shadow-sm', SEVERITY_COLOR[alert.severity] ?? 'border-gray-300 bg-white')}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className={clsx('text-xs font-bold px-2 py-0.5 rounded-full', SEVERITY_BADGE[alert.severity])}>
              {alert.severity}
            </span>
            <span className="text-xs text-gray-500 truncate">{alert.facility_name}</span>
          </div>
          <p className="font-semibold text-sm text-gray-900">{localized('title')}</p>
          <p className="text-xs text-gray-600 mt-1">{localized('body')}</p>
          {alert.days_until_stockout != null && (
            <p className="text-xs font-medium text-red-700 mt-1">
              {t('alert.days_until_stockout', { days: formatNumber(alert.days_until_stockout) })}
              {alert.confidence != null &&
                ` · ${t('alert.confidence', { pct: formatNumber(Math.round(alert.confidence * 100)) })}`}
            </p>
          )}
        </div>
      </div>
      {alert.status === 'OPEN' && (
        <div className="flex gap-2 mt-3">
          {planId && (
            <button
              onClick={() => approveMutation.mutate()}
              disabled={approveMutation.isPending}
              className="flex-1 bg-teal-600 text-white text-xs font-semibold py-1.5 px-3 rounded-md hover:bg-teal-700 disabled:opacity-50 transition-colors"
            >
              {approveMutation.isPending ? '...' : t('alert.approve_transfer')}
            </button>
          )}
          {planId ? (
            <button
              onClick={() => deferMutation.mutate()}
              disabled={deferMutation.isPending}
              className="flex-1 bg-white text-gray-700 text-xs font-semibold py-1.5 px-3 rounded-md border border-gray-300 hover:bg-gray-50 disabled:opacity-50 transition-colors"
            >
              {t('alert.defer')}
            </button>
          ) : (
            <button
              onClick={() => ackMutation.mutate()}
              disabled={ackMutation.isPending}
              className="flex-1 bg-white text-gray-700 text-xs font-semibold py-1.5 px-3 rounded-md border border-gray-300 hover:bg-gray-50 disabled:opacity-50 transition-colors"
            >
              {ackMutation.isPending ? '...' : t('alert.acknowledge')}
            </button>
          )}
        </div>
      )}
      {alert.status !== 'OPEN' && (
        <span className="inline-block mt-2 text-xs text-green-700 font-medium">{alert.status}</span>
      )}
    </div>
  )
}
