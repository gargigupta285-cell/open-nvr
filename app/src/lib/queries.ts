/**
 * Copyright (c) 2026 OpenNVR
 * This file is part of OpenNVR.
 *
 * OpenNVR is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * OpenNVR is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with OpenNVR.  If not, see <https://www.gnu.org/licenses/>.
 */

// Shared react-query hooks. Views that need the same server data must use
// these instead of calling apiService in a useEffect, so concurrent mounts
// share one request and one cache entry.

import { QueryClient, useQuery } from '@tanstack/react-query'
import { apiService } from './apiService'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

export type CameraItem = {
  id: number
  name: string
  ip_address: string
  is_active: boolean
  status?: string | null
}

export type CameraListResp = { cameras: CameraItem[]; total: number }

export function useCameras(params: { limit?: number; active_only?: boolean } = { limit: 100, active_only: true }) {
  return useQuery({
    queryKey: ['cameras', params],
    queryFn: async () => {
      const { data } = await apiService.getCameras(params)
      return data as CameraListResp
    },
  })
}

export function useRecordingsByDate() {
  return useQuery({
    queryKey: ['recordings-by-date'],
    queryFn: async () => {
      const { data } = await apiService.getRecordingsByDate()
      return data as {
        cameras?: { camera_name?: string; recordings?: { date: string; total_duration?: number }[] }[]
        total_recordings?: number
      }
    },
  })
}

export function useSuricataStats(limit = 5000) {
  return useQuery({
    queryKey: ['suricata-stats', limit],
    queryFn: async () => {
      const { data } = await apiService.getSuricataStats({ limit })
      return data as { by_severity?: Record<string, number> }
    },
  })
}
