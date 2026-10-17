import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { fetchDeploymentProfile, type DeploymentProfile } from '@/api/deployment'
import { useAuth } from './auth-context-value'
import { LoginPage } from './login-page'

vi.mock('@/api/deployment', () => ({ fetchDeploymentProfile: vi.fn() }))
vi.mock('./auth-context-value', () => ({ useAuth: vi.fn() }))
vi.mock('@/components/graph/knowledge-graph', () => ({ KnowledgeGraph: () => null }))
vi.mock('@/features/mobile/ConnectQrCode', () => ({ ConnectQrCode: () => null }))

const demoLogin = vi.fn()
const profile: DeploymentProfile = {
  deployment_profile: 'self_hosted_team', single_user: false,
  registration_enabled: true, demo_login_enabled: false, sandbox_enabled: true,
}

function PasswordDestination() {
  const location = useLocation()
  return <output>{JSON.stringify(location.state)}</output>
}

function renderLogin() {
  render(<MemoryRouter initialEntries={['/auth/login']}><Routes>
    <Route path="/auth/login" element={<LoginPage />} />
    <Route path="/auth/change-password" element={<PasswordDestination />} />
  </Routes></MemoryRouter>)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubEnv('VITE_ENABLE_DEMO_LOGIN', 'true')
  vi.mocked(useAuth).mockReturnValue({ demoLogin } as ReturnType<typeof useAuth>)
})

afterEach(() => vi.unstubAllEnvs())

it('keeps demo hidden until the server explicitly enables it', async () => {
  let resolve!: (value: DeploymentProfile) => void
  vi.mocked(fetchDeploymentProfile).mockReturnValue(new Promise(done => { resolve = done }))
  renderLogin()
  expect(screen.queryByRole('button', { name: '试用 Demo' })).not.toBeInTheDocument()
  await act(async () => resolve(profile))
  expect(screen.queryByRole('button', { name: '试用 Demo' })).not.toBeInTheDocument()
})

it('keeps demo hidden when deployment settings cannot be loaded', async () => {
  vi.mocked(fetchDeploymentProfile).mockRejectedValue(new Error('offline'))
  renderLogin()
  await waitFor(() => expect(fetchDeploymentProfile).toHaveBeenCalled())
  expect(screen.queryByRole('button', { name: '试用 Demo' })).not.toBeInTheDocument()
})

it('enters password change without embedding a demo password in route state', async () => {
  vi.mocked(fetchDeploymentProfile).mockResolvedValue({ ...profile, demo_login_enabled: true })
  demoLogin.mockResolvedValue({ workspaceId: 'workspace', mustChangePassword: true })
  renderLogin()
  await userEvent.click(await screen.findByRole('button', { name: '试用 Demo' }))
  expect(demoLogin).toHaveBeenCalledWith()
  expect(screen.getByRole('status')).toHaveTextContent('null')
})
