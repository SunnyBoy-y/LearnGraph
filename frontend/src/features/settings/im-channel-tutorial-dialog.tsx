import { useEffect, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

export type IMTutorialChannel = 'feishu' | 'wechat'

function StepList({ steps }: { steps: Array<{ title: string; body: React.ReactNode }> }) {
  return (
    <ol className="space-y-2.5">
      {steps.map((step, index) => (
        <li key={step.title} className="flex gap-3 rounded-lg border border-border/60 bg-muted/25 p-3">
          <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/12 text-xs font-semibold text-primary">
            {index + 1}
          </span>
          <div className="min-w-0 space-y-1">
            <p className="text-sm font-medium text-foreground">{step.title}</p>
            <div className="text-xs leading-5 text-muted-foreground">{step.body}</div>
          </div>
        </li>
      ))}
    </ol>
  )
}

export function IMChannelTutorialDialog({
  open,
  initialTab,
  onOpenChange,
}: {
  open: boolean
  initialTab: IMTutorialChannel
  onOpenChange: (open: boolean) => void
}) {
  const [tab, setTab] = useState<IMTutorialChannel>(initialTab)

  useEffect(() => {
    if (open) setTab(initialTab)
  }, [open, initialTab])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>消息渠道接入教程</DialogTitle>
          <DialogDescription>
            查看飞书与个人微信的扫码接入步骤、手动配置方式及常见注意事项。
          </DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={value => setTab(value as IMTutorialChannel)}>
          <TabsList className="mb-3 w-fit">
            <TabsTrigger value="feishu" className="px-3">飞书接入教程</TabsTrigger>
            <TabsTrigger value="wechat" className="px-3">微信接入教程</TabsTrigger>
          </TabsList>

          <TabsContent value="feishu" className="space-y-5">
            <section className="space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">方式一：扫码一键接入（推荐）</h3>
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                  自动建应用与配权限
                </span>
              </div>
              <StepList
                steps={[
                  {
                    title: '生成飞书授权二维码',
                    body: '在「消息渠道 → 飞书」点击「扫码一键接入」，页面会自动申请飞书官方设备授权二维码与确认链接。',
                  },
                  {
                    title: '使用手机飞书扫码或打开授权页',
                    body: '用手机飞书「扫一扫」扫描二维码（或点击二维码下方的链接在桌面浏览器中打开），按提示确认创建个人助手应用。系统会自动开启机器人能力、消息权限及长连接事件。',
                  },
                  {
                    title: '自动保存凭据并完成绑定',
                    body: (
                      <>
                        授权通过后，LearnGraph 会加密保存应用凭据并尝试自动绑定你的飞书账号。若页面显示「待绑定」，点击「生成绑定码」，并在 10 分钟内将{' '}
                        <code className="rounded bg-muted px-1 py-0.5 text-foreground">/bind &lt;绑定码&gt;</code>{' '}
                        私聊发给该机器人。
                      </>
                    ),
                  },
                  {
                    title: '开始私聊提问',
                    body: '在飞书中搜索刚创建的机器人并发送文本问题，回复会自动同步保存到 LearnGraph 的「飞书 · 学习助手」会话，可在网页端继续追问。',
                  },
                ]}
              />
            </section>

            <section className="space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">方式二：手动配置企业自建应用</h3>
                <a
                  href="https://open.feishu.cn/app"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary underline-offset-4 hover:underline"
                >
                  打开飞书开放平台
                  <ExternalLink className="size-3" />
                </a>
              </div>
              <StepList
                steps={[
                  {
                    title: '创建企业自建应用',
                    body: '登录飞书开放平台（open.feishu.cn），进入开发者后台点击「创建企业自建应用」。',
                  },
                  {
                    title: '添加机器人能力与消息权限',
                    body: (
                      <>
                        在「添加应用能力」中启用「机器人」；在「权限管理」中开通单聊收发消息权限（如{' '}
                        <code className="rounded bg-muted px-1 py-0.5 text-foreground">im:message</code>、
                        <code className="rounded bg-muted px-1 py-0.5 text-foreground">im:message.p2p_msg:readonly</code>、
                        <code className="rounded bg-muted px-1 py-0.5 text-foreground">im:message:send_as_bot</code>）。
                      </>
                    ),
                  },
                  {
                    title: '配置长连接事件订阅',
                    body: (
                      <>
                        在「事件与回调 → 事件配置」选择「使用长连接接收事件」（无需配置公网回调 URL），然后添加事件{' '}
                        <code className="rounded bg-muted px-1 py-0.5 text-foreground">im.message.receive_v1</code>。
                      </>
                    ),
                  },
                  {
                    title: '发布应用版本',
                    body: '在「版本管理与发布」创建版本并发布，确认应用可用范围包含你的飞书账号。',
                  },
                  {
                    title: '填入凭据并完成绑定',
                    body: '在「凭证与基础信息」复制 App ID 与 App Secret，回到 LearnGraph 点击「手动配置 App ID / Secret」保存，再生成绑定码发给机器人即可。',
                  },
                ]}
              />
            </section>

            <section className="rounded-lg border border-border/60 bg-muted/20 p-3.5 text-xs leading-5 text-muted-foreground">
              <p className="mb-1.5 font-medium text-foreground">注意事项</p>
              <ul className="list-disc space-y-1 pl-4">
                <li>仅支持个人工作区；群聊消息与图片/文件等非文本消息会被忽略。</li>
                <li>若所在企业限制了一键创建自建应用，请使用「方式二：手动配置」。</li>
                <li>二维码或绑定码均有时效，超时后点击刷新重新生成即可。</li>
                <li>切勿将 App Secret 或绑定码分享给他人；在网页端发出的新消息不会主动推送到飞书。</li>
              </ul>
            </section>
          </TabsContent>

          <TabsContent value="wechat" className="space-y-5">
            <section className="space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">扫码授权接入步骤</h3>
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                  ClawBot / iLink 协议
                </span>
              </div>
              <StepList
                steps={[
                  {
                    title: '获取微信登录二维码',
                    body: '切换到你的个人工作区，在「消息渠道 → 个人微信」点击「扫码登录微信」，系统会向微信 ClawBot（iLink）接口请求一次性登录二维码。',
                  },
                  {
                    title: '手机微信扫码并确认',
                    body: '打开手机微信使用「扫一扫」扫描屏幕上的二维码，页面会实时提示「已扫码」，随后在手机上点击确认授权登录。',
                  },
                  {
                    title: '自动绑定个人工作区',
                    body: '确认后，LearnGraph 会加密保存登录凭据并自动绑定你的微信账号，无需手动配置密钥，也无需再发送绑定码。',
                  },
                  {
                    title: '在微信中直接对话提问',
                    body: '在微信中向已授权的 ClawBot 机器人发送学习问题，LearnGraph 后台会拉取消息、生成回答并回复到微信，同时保留完整的「微信 · 学习助手」网页会话记录。',
                  },
                ]}
              />
            </section>

            <section className="rounded-lg border border-border/60 bg-muted/20 p-3.5 text-xs leading-5 text-muted-foreground">
              <p className="mb-1.5 font-medium text-foreground">注意事项与常见问题</p>
              <ul className="list-disc space-y-1 pl-4">
                <li>仅支持个人工作区下的私聊文本问答，群聊及非文本消息暂不处理。</li>
                <li>单个微信机器人凭据仅允许一个在线监听端拉取消息，避免同时在多个服务重复登录同一机器人。</li>
                <li>若微信侧登录态过期，状态栏会提示「登录已失效」，点击「重新扫码登录」即可恢复，历史网页会话不会丢失。</li>
                <li>长回复会自动分段发送以适配微信单条消息长度限制；你可以随时点击「退出登录」停止监听并清除已保存凭据。</li>
              </ul>
            </section>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
