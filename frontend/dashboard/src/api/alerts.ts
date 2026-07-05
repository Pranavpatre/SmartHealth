import { apiClient } from './client'

export interface Alert {
  id: string
  facility_id: string
  facility_name: string
  alert_type: string
  severity: 'INFO' | 'WARNING' | 'CRITICAL'
  medicine_name?: string
  title: string
  body: string
  // Structured translatable alert; dashboard renders these via i18n and falls
  // back to title/body when absent (older rows).
  message_key?: string | null
  message_params?: Record<string, string | number> | null
  days_until_stockout?: number
  confidence?: number
  status: 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED' | 'SNOOZED'
  created_at: string
}

export const getAlerts = async (params?: { status?: string; severity?: string }) => {
  const { data } = await apiClient.get<{ items: Alert[]; total: number }>('/alerts', { params })
  return data
}

export const acknowledgeAlert = async (alertId: string) => {
  const { data } = await apiClient.patch<Alert>(`/alerts/${alertId}/acknowledge`)
  return data
}
