import { login as webLogin, logout, transport, webSession } from '../../cloud/api'

export { logout, webSession }
const request = async (method, path, data, options) => ({ data: await transport(method, path, data, options) })
export const login = async (email, password) => ({ data: await webLogin(email, password) })
export const getStats = () => request('GET', '/api/admin/stats')
export const getUsers = params => request('GET', '/api/admin/users', undefined, { params })
export const updateUser = (id, data) => request('PUT', `/api/admin/users/${id}`, data)
export const deleteUser = id => request('DELETE', `/api/admin/users/${id}`)
export const getTeams = params => request('GET', '/api/admin/teams', undefined, { params })
export const updateTeam = (id, data) => request('PUT', `/api/admin/teams/${id}`, data)
export const deleteTeam = id => request('DELETE', `/api/admin/teams/${id}`)
