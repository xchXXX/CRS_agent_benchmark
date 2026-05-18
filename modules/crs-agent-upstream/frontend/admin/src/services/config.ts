import api from './api'

export const configService = {
  getAll: () =>
    api.get('/admin/config/list'),

  getByCategory: (category: string) =>
    api.get(`/admin/config/category/${category}`),

  update: (configs: Array<{ key: string; value: string; type: string }>) =>
    api.put('/admin/config/update', { configs }),

  refresh: () =>
    api.post('/admin/config/refresh'),

  clearLogFile: () =>
    api.delete('/admin/config/log-file')
}
