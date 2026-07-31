import { useState, type ComponentType } from "react";
import { BookOpen, Bug, Check, Copy, ExternalLink, Scale } from "lucide-react";
import { toast } from "sonner";

import {
  PageFrame,
  PageIntro,
  SectionHeading,
  Surface,
} from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";

export const GITHUB_REPO_URL = "https://github.com/SunnyBoy-y/LearnGraph";

const DOCS_URL = "https://sunnyboy-y.github.io/LearnGraph/";
const ISSUES_URL = `${GITHUB_REPO_URL}/issues`;
const LICENSE_URL = `${GITHUB_REPO_URL}/blob/main/LICENSE`;

/** lucide 1.x dropped brand glyphs, so the GitHub mark ships inline here. */
function GithubMark({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="currentColor"
      viewBox="0 0 16 16"
    >
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-2.91-.88-2.91-3.09 0-.66.23-1.2.61-1.62-.06-.15-.27-.77.06-1.6 0 0 .61-.19 2.01.75a5.6 5.6 0 0 1 1.5-.2c.51 0 1.02.07 1.5.2 1.4-.95 2.01-.75 2.01-.75.33.83.12 1.45.06 1.6.38.42.61.96.61 1.62 0 2.22-1.14 2.89-2.92 3.09.29.25.55.74.55 1.5 0 1.07-.01 1.94-.01 2.21 0 .21.15.46.55.38A7.99 7.99 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

const links: Array<{
  label: string;
  hint: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
}> = [
  {
    label: "开发者文档",
    hint: "架构、开发流程与部署说明",
    href: DOCS_URL,
    icon: BookOpen,
  },
  {
    label: "提交问题",
    hint: "反馈 Bug 或提出功能建议",
    href: ISSUES_URL,
    icon: Bug,
  },
  {
    label: "开源许可",
    hint: "MIT License",
    href: LICENSE_URL,
    icon: Scale,
  },
];

export function AboutPage() {
  const [copied, setCopied] = useState(false);

  const copyRepoUrl = async () => {
    try {
      await navigator.clipboard.writeText(GITHUB_REPO_URL);
      setCopied(true);
      toast.success("已复制仓库地址");
      window.setTimeout(() => setCopied(false), 1_600);
    } catch {
      toast.error("无法复制，请手动选择链接");
    }
  };

  return (
    <PageFrame>
      <PageIntro
        description="LearnGraph 是一个开源项目：从一个真实目标出发，获得一张随学习持续生长的知识路线图。"
        eyebrow="About"
        title="关于 LearnGraph"
      />

      <Surface className="p-5">
        <SectionHeading
          description="源码、路线图与版本发布都在 GitHub 上公开"
          title="GitHub 仓库"
        />
        <div className="mt-4 flex flex-col gap-3 rounded-xl border bg-muted/30 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-foreground text-background">
              <GithubMark className="size-5" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium">SunnyBoy-y/LearnGraph</p>
              <a
                className="block truncate font-mono text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                href={GITHUB_REPO_URL}
                rel="noreferrer noopener"
                target="_blank"
              >
                {GITHUB_REPO_URL}
              </a>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button onClick={copyRepoUrl} size="sm" type="button" variant="outline">
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copied ? "已复制" : "复制地址"}
            </Button>
            <Button asChild size="sm">
              <a href={GITHUB_REPO_URL} rel="noreferrer noopener" target="_blank">
                <GithubMark className="size-3.5" />
                在 GitHub 打开
              </a>
            </Button>
          </div>
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading description="文档、反馈与许可信息" title="更多链接" />
        <ul className="mt-4 grid gap-2 sm:grid-cols-2">
          {links.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.href}>
                <a
                  className="flex items-center gap-3 rounded-xl border p-3 transition-colors hover:bg-muted/60"
                  href={item.href}
                  rel="noreferrer noopener"
                  target="_blank"
                >
                  <Icon className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {item.label}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {item.hint}
                    </span>
                  </span>
                  <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />
                </a>
              </li>
            );
          })}
        </ul>
      </Surface>
    </PageFrame>
  );
}
