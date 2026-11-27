import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

function isLocalHost(host: string) {
  return ['localhost', 'localhost.localdomain', '0.0.0.0', '[::]', '[::1]'].includes(host) || /^127\./.test(host)
}

export function isLanIPv4(host: string) {
  return /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host) &&
    /^\d{1,3}(\.\d{1,3}){3}$/.test(host) && host.split('.').every(part => Number(part) <= 255 && String(Number(part)) === part)
}

function detectLanIPv4(onResult: (address: string) => void) {
  let peer: RTCPeerConnection | undefined
  let done = false
  const finish = (address = '') => {
    if (done) return
    done = true
    window.clearTimeout(timeout)
    peer?.close()
    onResult(address)
  }
  const timeout = window.setTimeout(() => finish(), 2500)
  try {
    peer = new RTCPeerConnection({ iceServers: [] })
    const connection = peer
    connection.onicecandidate = ({ candidate }) => {
      if (!candidate) return finish()
      const address = candidate.address || candidate.candidate.split(' ')[4]
      if (address && isLanIPv4(address)) finish(address)
    }
    connection.createDataChannel('lg-probe')
    void connection.createOffer().then(offer => {
      if (!done) return connection.setLocalDescription(offer)
    }).catch(() => finish())
  } catch {
    finish()
  }
  return () => {
    window.clearTimeout(timeout)
    if (!done) peer?.close()
    done = true
  }
}

export function ConnectQrCode({ size = 160 }: { size?: number }) {
  const { hostname, origin, protocol, port } = window.location
  const local = isLocalHost(hostname)
  const [detected, setDetected] = useState<string | null>(null)
  const [manual, setManual] = useState('')
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [image, setImage] = useState<{ target: string; url: string; failed?: boolean }>()
  const address = manual || detected
  const target = local ? (address ? `${protocol}//${address}${port ? `:${port}` : ''}` : '') : origin

  useEffect(() => {
    if (local) return detectLanIPv4(setDetected)
  }, [local])

  useEffect(() => {
    if (!target) return
    let cancelled = false
    QRCode.toDataURL(target, { width: size * 2, margin: 1, errorCorrectionLevel: 'M' }).then(url => {
      if (!cancelled) setImage({ target, url })
    }).catch(() => {
      if (!cancelled) setImage({ target, url: '', failed: true })
    })
    return () => { cancelled = true }
  }, [target, size])

  return (
    <div className="flex flex-col items-center gap-3 text-sm">
      {target ? (
        <>
          {image?.target === target && image.url ? <img alt={`扫码连接 ${target}`} className="rounded-lg border" width={size} height={size} src={image.url} />
            : <p role="status">{image?.target === target && image.failed ? '二维码生成失败，请手动输入下面的地址。' : '正在生成二维码…'}</p>}
          <p className="text-center text-xs text-muted-foreground">在 LearnGraph 手机 App 内扫码，或手动输入服务器地址。</p>
          <code className="select-all break-all text-xs">{target}</code>
        </>
      ) : <p role="status" className="text-center text-muted-foreground">{detected === null ? '正在查找电脑的局域网地址…' : '未检测到局域网地址。请填写电脑的局域网 IP，并让手机接入同一网络。'}</p>}
      {local && <form className="grid w-full gap-2" onSubmit={event => {
        event.preventDefault()
        const ip = draft.trim()
        if (!isLanIPv4(ip)) { setError('请输入有效的局域网 IPv4 地址，例如 192.168.1.5。'); return }
        setManual(ip)
        setError('')
      }}>
        <Label htmlFor="phone-connect-ip">电脑的局域网 IP</Label>
        <div className="flex gap-2">
          <Input id="phone-connect-ip" placeholder="192.168.1.5" value={draft} onChange={event => { setDraft(event.target.value); setError('') }} aria-invalid={Boolean(error)} />
          <Button type="submit" variant="outline">生成二维码</Button>
        </div>
        {error && <p role="alert" className="text-destructive">{error}</p>}
      </form>}
    </div>
  )
}
