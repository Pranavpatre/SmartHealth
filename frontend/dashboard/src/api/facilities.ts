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
  district_name?: string | null
  real_district_opd_annual?: number | null
  real_district_opd_period?: string | null
  real_district_ipd_annual?: number | null
  real_district_ipd_monthly_avg?: number | null
  real_district_stockout_rate?: number | null
  real_district_fully_immunized_annual?: number | null
  real_district_institutional_deliveries_annual?: number | null
  real_district_hmis_period?: string | null
}

export interface StockItem {
  medicine_id: number
  medicine_name: string
  total_stock: number
  reorder_level: number
  days_of_stock: number
}

export interface MapMarker {
  id: string
  name: string
  lat: number
  lng: number
  traffic_light: 'GREEN' | 'YELLOW' | 'RED' | null
  health_score: number | null
}

// Lightweight markers for EVERY facility in scope (for clustered map).
export const getFacilitiesMap = async () => {
  const { data } = await apiClient.get<MapMarker[]>('/facilities/map')
  return data
}

export interface NearestFacility {
  id: string
  code: string
  name: string
  facility_type: string
  lat: number
  lng: number
  distance_km: number
  traffic_light: 'GREEN' | 'YELLOW' | 'RED' | null
  health_score: number | null
  district_name: string | null
}

export const getNearestFacilities = async (lat: number, lng: number, limit = 10) => {
  const { data } = await apiClient.get<NearestFacility[]>('/facilities/nearest', {
    params: { lat, lng, limit },
  })
  return data
}

export interface AtRiskFacility {
  id: string
  code: string
  name: string
  facility_type: string
  health_score: number | null
  traffic_light: 'GREEN' | 'YELLOW' | 'RED' | null
  active_alerts: number
}

// True national bottom-N by health score (across all facilities in scope).
export const getAtRiskFacilities = async (limit = 5) => {
  const { data } = await apiClient.get<AtRiskFacility[]>('/facilities/at-risk', { params: { limit } })
  return data
}

export const getFacilities = async () => {
  // Backend paginates (default 50); request the full set so the map/list isn't truncated.
  const { data } = await apiClient.get<Facility[]>('/facilities', {
    params: { page: 1, page_size: 1000 },
  })
  return data
}

export const getFacility = async (id: string) => {
  const { data } = await apiClient.get<FacilityDetail>(`/facilities/${id}`)
  return data
}

export interface FacilityStats {
  total: number
  green: number
  yellow: number
  red: number
  unscored: number
  avg_score: number | null
}

export const getFacilityStats = async () => {
  const { data } = await apiClient.get<FacilityStats>('/facilities/stats')
  return data
}

export interface FacilityAttendance {
  facility_id: string
  present_today: number
  total_today: number
  days_since_last_present: number | null
}

export const getFacilityAttendance = async (id: string) => {
  const { data } = await apiClient.get<FacilityAttendance>(`/attendance/facility/${id}`)
  return data
}

export interface BedRow { bed_type: string; total_beds: number; occupied_beds: number }
export interface BedMatrix { facility_id: string; beds: BedRow[]; updated_at: string | null }
export const getFacilityBeds = async (id: string) => {
  const { data } = await apiClient.get<BedMatrix>(`/ledger/beds/${id}`)
  return data
}

export interface TestRow { test_id: number; test_name: string | null; available: boolean }
export interface TestChecklist { facility_id: string; tests: TestRow[] }
export const getFacilityTests = async (id: string) => {
  const { data } = await apiClient.get<TestChecklist>(`/ledger/tests/${id}`)
  return data
}

export interface DemandForecastItem {
  disease: string
  severity: string
  medicine_category: string
  affected_medicines: string[]
  demand_multiplier: number
  reasoning: string
}
export const getDemandForecast = async (id: string) => {
  const { data } = await apiClient.get<DemandForecastItem[]>(`/predict/demand/${id}`)
  return data
}
