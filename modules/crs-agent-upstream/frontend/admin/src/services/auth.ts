import api from './api'

export interface LoginParams {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: {
    id: number
    username: string
    role: string
  }
}

export const authService = {
  login: (data: LoginParams) =>
    api.post<LoginResponse>('/admin/auth/login', data),

  getMe: () =>
    api.get('/admin/auth/me'),

  changePassword: (data: { old_password: string; new_password: string }) =>
    api.put('/admin/auth/password', data)
}
