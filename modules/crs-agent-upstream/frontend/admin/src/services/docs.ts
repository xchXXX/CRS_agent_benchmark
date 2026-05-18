import api from './api'

export const docsService = {
  getList: (params: { page: number; page_size: number; keyword?: string }) =>
    api.get('/admin/docs/list', { params }),

  getDetail: (id: string) =>
    api.get(`/admin/docs/${id}`),

  delete: (id: string) =>
    api.delete(`/admin/docs/${id}`),

  batchDelete: (file_ids: string[]) =>
    api.post('/admin/docs/batch-delete', { file_ids }),

  add: (data: {
    file_path: string
    filename: string
    ref_file_id: number
    parent_id: number
    file_type?: string
  }) =>
    api.post('/admin/docs/add', data),

  importPreview: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.post('/admin/docs/import/preview', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000
    })
  },

  importStart: (data: {
    upload_id: string
    file_path_col: string
    filename_col: string
    ref_file_id_col: string
    parent_id_col: string
    skip_existing?: boolean
  }) =>
    api.post('/admin/docs/import/start', data, { timeout: 60000 }),

  importStatus: (task_id: string) =>
    api.get(`/admin/docs/import/status/${task_id}`),

  deleteStart: (data: {
    upload_id: string
    file_path_col: string
    filename_col: string
    ref_file_id_col: string
    parent_id_col: string
  }) =>
    api.post('/admin/docs/delete/start', data, { timeout: 60000 }),

  deleteStatus: (task_id: string) =>
    api.get(`/admin/docs/delete/status/${task_id}`),
}
