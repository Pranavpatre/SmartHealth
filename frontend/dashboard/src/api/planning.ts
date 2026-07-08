import { apiClient } from './client'

// Pre-emptive planning: seasonally-adjusted refill actionables + long-term
// capacity concerns for the admin's district/state scope.

export interface RefillItem {
  facility_id: string
  facility: string
  code: string
  address: string
  district: string
  item: string
  category: string
  unit: string
  current_stock: number
  required: number
  order_qty: number
  days_of_cover: number
  deliver_by: string
  urgency: 'HIGH' | 'MEDIUM' | 'LOW'
  seasonal_multiplier: number
}

export interface RefillResponse {
  generated_at: string
  horizon_days: number
  target_month: number
  items: RefillItem[]
}

export interface CapacityItem {
  facility_id: string
  facility: string
  code: string
  address: string
  district: string
  concern: 'BEDS' | 'DOCTORS'
  detail: string
  metric: string
}

export interface DoctorMove {
  from_facility: string
  from_district: string
  to_facility: string
  to_district: string
  to_address: string
  doctors: number
  distance_km: number
}

type Scope = { state_id?: number; district_id?: number }

export const getRefills = async (scope: Scope) => {
  const { data } = await apiClient.get<RefillResponse>('/planning/refills', { params: scope })
  return data
}

export const getCapacity = async (scope: Scope) => {
  const { data } = await apiClient.get<CapacityItem[]>('/planning/capacity', { params: scope })
  return data
}

export const getDoctorRedistribution = async (scope: Scope) => {
  const { data } = await apiClient.get<DoctorMove[]>('/planning/doctor-redistribution', { params: scope })
  return data
}

// Fetch the supplier-ready CSV (with addresses) and trigger a browser download.
export const downloadRefillsCsv = async (scope: Scope) => {
  const res = await apiClient.get('/planning/refills.csv', { params: scope, responseType: 'blob' })
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `planning_refills_${new Date().toISOString().slice(0, 10)}.csv`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
