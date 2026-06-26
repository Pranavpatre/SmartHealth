import { apiClient } from './client'

export interface Alert {
  id: string
  facility_id: string
  facility_name: string
  alert_type: string
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  medicine_name?: string
  title: string
  body: string
  days_until_stockout?: number
  confidence?: number
  status: 'PENDING' | 'ACKNOWLEDGED' | 'RESOLVED'
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
