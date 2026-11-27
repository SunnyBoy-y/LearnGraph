import { fireEvent, render, screen } from '@testing-library/react'
import QRCode from 'qrcode'
import { afterEach, expect, it, vi } from 'vitest'
import { ConnectQrCode } from './ConnectQrCode'

vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,dGVzdA==') } }))
afterEach(() => vi.unstubAllGlobals())

it('does not generate a loopback code when probing fails and accepts a LAN address', async () => {
  vi.stubGlobal('RTCPeerConnection', undefined)
  render(<ConnectQrCode />)
  expect(await screen.findByText(/未检测到局域网地址/)).toBeInTheDocument()
  expect(QRCode.toDataURL).not.toHaveBeenCalled()
  const input = screen.getByLabelText('电脑的局域网 IP')
  const generate = screen.getByRole('button', { name: '生成二维码' })
  fireEvent.change(input, { target: { value: '127.0.0.1' } })
  fireEvent.click(generate)
  expect(screen.getByRole('alert')).toHaveTextContent('请输入有效的局域网 IPv4 地址')
  fireEvent.change(input, { target: { value: '192.168.1.5' } })
  fireEvent.click(generate)
  expect(await screen.findByRole('img', { name: /扫码连接.*192\.168\.1\.5/ })).toBeInTheDocument()
})
