import { apiClient } from './client'

export interface RedistributionPlan {
  id: string
  status: 'PENDING' | 'APPROVED' | 'DEFERRED'
  total_items: number
  estimated_saving_inr: number
  created_at: string
  line_items: LineItem[]
}

export interface LineItem {
  id: string
  from_facility_name: string
  to_facility_name: string
  medicine_name: string
  quantity: number
  distance_km: number
  urgency: string
  estimated_saving_inr: number
}

export const getPlans = async () => {
  const { data } = await apiClient.get<RedistributionPlan[]>('/redistribution/plans')
  return data
}

export const approvePlan = async (planId: string) => {
  const { data } = await apiClient.post<RedistributionPlan>(`/redistribution/plans/${planId}/approve`)
  return data
}

export const deferPlan = async (planId: string, reason: string) => {
  const { data } = await apiClient.post<RedistributionPlan>(`/redistribution/plans/${planId}/defer`, { reason })
  return data
}

export const createPlan = async () => {
  const { data } = await apiClient.post<RedistributionPlan>('/redistribution/plans')
  return data
}
