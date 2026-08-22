/**
 * 手机扫码连接二维码：
 *
 * 在电脑浏览器（登录页/设置页）显示「供手机连接」的服务器地址二维码，
 * 手机 APK 连接页点「扫描服务器二维码」扫描后自动填充地址。
 * 仅在非 APK 环境展示（APK 内已在连接页有原生扫码入口）。
 *
 * 地址判定：
 *  - 当前页面是 localhost / 127.0.0.1（用户在电脑本机调试自建服务）：
 *    手机扫码无法连接 localhost，自动探测本机局域网 IPv4 并替换 host，
 *    保留协议与端口 → 如 http://192.168.1.5:18000
 *  - 否则（已用局域网 IP / 域名 / HTTPS 反代访问）：直接用当前地址。
 */

import { useEffect, useMemo, useState } from 'react'
import QRCode from 'qrcode'

function isLoopbackHost(hostname: string): boolean {
  const h = hostname.trim().toLowerCase()
  return (
    h === 'localhost' ||
    h === '0.0.0.0' ||
    h === '[::1]' ||
    h === '::1' ||
    h.startsWith('127.') ||
    h === 'localhost.localdomain'
  )
}

/** 通过 WebRTC 候选地址枚举本机局域网 IPv4（仅私网段），探测失败返回 null */
function detectLanIPv4(timeoutMs = 2500): Promise<string | null> {
  return new Promise((resolve) => {
    try {
      const pc = new RTCPeerConnection({ iceServers: [] })
      let settled = false
      const done = (ip: string | null) => {
        if (settled) return
        settled = true
        try {
          pc.close()
        } catch {
          /* ignore */
        }
        resolve(ip)
      }
      pc.createDataChannel('lg-probe')
      pc.createOffer()
        .then((offer) => pc.setLocalDescription(offer))
        .catch(() => done(null))

      const PRIVATE_RE = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/
      pc.onicecandidate = (event) => {
        const candidate = event.candidate?.candidate
        if (!candidate) {
          done(null)
          return
        }
        const match = /([0-9]{1,3}(\.[0-9]{1,3}){3})/.exec(candidate)
        const ip = match?.[1]
        if (ip && PRIVATE_RE.test(ip)) done(ip)
      }
      window.setTimeout(() => done(null), timeoutMs)
    } catch {
      resolve(null)
    }
  })
}

export function ConnectQrCode({ size = 160 }: { size?: number }) {
  const [dataUrl, setDataUrl] = useState('')
  // 解析后的真实二维码内容（host 可能被替换为局域网 IP）
  const [target, setTarget] = useState('')
  const [converted, setConverted] = useState(false)

  const locationInfo = useMemo(() => {
    try {
      return {
        origin: window.location.origin || '',
        hostname: window.location.hostname || '',
        protocol: window.location.protocol || 'http:',
        port: window.location.port || '',
      }
    } catch {
      return { origin: '', hostname: '', protocol: 'http:', port: '' }
    }
  }, [])

  // 决定二维码目标：localhost → 本机 IP；否则当前地址
  useEffect(() => {
    if (!locationInfo.origin) return
    let cancelled = false

    const buildQr = (host: string) => {
      const port = locationInfo.port ? `:${locationInfo.port}` : ''
      return `${locationInfo.protocol}//${host}${port}`
    }

    const resolveTarget = async () => {
      if (!isLoopbackHost(locationInfo.hostname)) {
        // 非 loopback：直接当前 origin
        return buildQr(locationInfo.hostname)
      }
      // localhost：探测本机局域网 IP
      const lanIp = await detectLanIPv4()
      if (!cancelled) {
        if (lanIp) {
          setConverted(true)
          setTarget(buildQr(lanIp))
        } else {
          setConverted(false)
          setTarget(buildQr(locationInfo.hostname)) // 回退，仍给 origin
        }
      }
    }

    resolveTarget()
    return () => {
      cancelled = true
    }
  }, [locationInfo])

  // 生成二维码
  useEffect(() => {
    if (!target) return
    let cancelled = false
    QRCode.toDataURL(target, {
      width: size * 2,
      margin: 1,
      errorCorrectionLevel: 'M',
    })
      .then((url) => {
        if (!cancelled) setDataUrl(url)
      })
      .catch(() => {
        // 生成失败静默
      })
    return () => {
      cancelled = true
    }
  }, [target, size])

  if (!target) return null

  return (
    <div className="flex flex-col items-center gap-2">
      {dataUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          alt={`扫码连接 ${target}`}
          className="rounded-lg border"
          style={{ width: size, height: size }}
          src={dataUrl}
        />
      ) : (
        <div
          aria-hidden="true"
          className="rounded-lg border"
          style={{ width: size, height: size }}
        />
      )}
      <p className="text-center text-xs leading-5 text-muted-foreground">
        用 LearnGraph 手机 App 扫描
        <br />
        {converted ? (
          <>
            已自动转换为局域网地址
            <br />
            <code className="font-mono text-[11px] text-foreground/70">{target}</code>
          </>
        ) : (
          '即可自动填入服务器地址'
        )}
      </p>
    </div>
  )
}