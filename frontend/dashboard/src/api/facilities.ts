import { apiClient } from './client'

export interface Facility {
  id: string
  code: string
  name: string
  facility_type: string
  lat: number
  lng: number
  health_score: number
  traffic_light: 'GREEN' | 'YELLOW' | 'RED'
  active_alerts: number
}

export interface FacilityDetail extends Facility {
  bed_capacity: number
  stock_summary: StockItem[]
  recent_alerts: import('./alerts').Alert[]
  health_score_breakdown: Record<string, number>
}

export interface StockItem {
  medicine_id: number
  medicine_name: string
  total_stock: number
  reorder_level: number
  days_of_stock: number
}

export const getFacilities = async () => {
  const { data } = await apiClient.get<Facility[]>('/facilities')
  return data
}

export const getFacility = async (id: string) => {
  const { data } = await apiClient.get<FacilityDetail>(`/facilities/${id}`)
  return data
}
