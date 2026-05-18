import api from './api'

export interface DimFacet {
  facet_key: string
  facet_name: string
  question?: string
  priority: number
  db_field?: string
  parent_facet_key?: string
  match_mode: string
  specificity: number
  is_active: boolean
}

export interface DimValue {
  id: number
  facet_key: string
  value: string
  match_patterns?: string
  parent_value_id?: number
  parent_value?: string
  is_active: boolean
  sort_order: number
}

export interface DimStats {
  facet_count: number
  value_count: number
  value_by_facet: Record<string, number>
  cache_loaded: boolean
}

export const dimensionService = {
  // 获取维度定义列表
  getFacets: (includeInactive = false) =>
    api.get<DimFacet[]>('/admin/dimension/facets', {
      params: { include_inactive: includeInactive }
    }),

  // 获取单个维度定义
  getFacet: (facetKey: string) =>
    api.get<DimFacet>(`/admin/dimension/facets/${facetKey}`),

  // 更新维度定义
  updateFacet: (facetKey: string, data: Partial<DimFacet>) =>
    api.put(`/admin/dimension/facets/${facetKey}`, data),

  // 获取维度值列表
  getValues: (facetKey?: string, includeInactive = false) =>
    api.get<DimValue[]>('/admin/dimension/values', {
      params: { facet_key: facetKey, include_inactive: includeInactive }
    }),

  // 获取单个维度值
  getValue: (id: number) =>
    api.get<DimValue>(`/admin/dimension/values/${id}`),

  // 创建维度值
  createValue: (data: {
    facet_key: string
    value: string
    match_patterns?: string
    parent_value_id?: number
    sort_order?: number
  }) => api.post('/admin/dimension/values', data),

  // 更新维度值
  updateValue: (id: number, data: {
    value?: string
    match_patterns?: string
    parent_value_id?: number
    is_active?: boolean
    sort_order?: number
  }) => api.put(`/admin/dimension/values/${id}`, data),

  // 删除维度值
  deleteValue: (id: number, hardDelete = false) =>
    api.delete(`/admin/dimension/values/${id}`, {
      params: { hard_delete: hardDelete }
    }),

  // 刷新缓存
  refreshCache: () => api.post('/admin/dimension/refresh'),

  // 获取统计信息
  getStats: () => api.get<DimStats>('/admin/dimension/stats'),

  // 从 docs 表同步维度值
  syncFromDocs: (facetKey: string, dryRun = true) =>
    api.post('/admin/dimension/sync-from-docs', null, {
      params: { facet_key: facetKey, dry_run: dryRun }
    })
}
