import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, BookOpen, ChevronDown, FlaskConical, LoaderCircle, Medal, MessageCircle, Play } from "lucide-react";
import { toast } from "sonner";

import { apiClient } from "@/api/client";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { IncrementalMarkdown } from "@/components/ai-elements/incremental-markdown";
import { Button } from "@/components/ui/button";
import { workspaceQueryKey } from "@/lib/query-keys";
import { useReducedMotionPreference } from "@/lib/motion-preferences";
import type {
  ActivitySpec,
  ActivityState,
  LearningBuildPreview,
  LearningPageData,
  Enrollment,
  Manifest,
} from "./node-learning-api";
import { learningApi } from "./node-learning-api";
import { ActivityCard } from "./node-learning-page";
import { normalizeLearningMarkdown } from "./learning-markdown";

type LearningManifest = Manifest | LearningBuildPreview;

/**
 * Keep the replay short enough that a cached package still feels immediate,
 * while making the first paint visibly progressive. The chunk size scales with
 * the section so a very long lesson does not spend tens of seconds typing.
 */
const TYPEWRITER_INTERVAL_MS = 22;
const TYPEWRITER_TICKS = 160;

function useTypewriterText(
  text: string,
  active: boolean,
  replayKey: string,
  deferUntilActive: boolean,
) {
  const reducedMotion = useReducedMotionPreference();
  const [visibleLength, setVisibleLength] = useState(() =>
    active || deferUntilActive ? 0 : text.length,
  );
  const startedRef = useRef(active);
  const replayRef = useRef(replayKey);

  useEffect(() => {
    const replayChanged = replayRef.current !== replayKey;
    if (replayChanged) {
      replayRef.current = replayKey;
      startedRef.current = false;
      setVisibleLength(active ? 0 : text.length);
    }

    if (!active) {
      // A graph-open package is mounted collapsed. Keep its Markdown empty
      // until the first expansion so a large cached lesson is not parsed twice
      // before it becomes visible. Once it has started, collapsing preserves
      // the completed text and reopening does not replay it.
      setVisibleLength(deferUntilActive && !startedRef.current ? 0 : text.length);
      return;
    }

    if (reducedMotion) {
      setVisibleLength(text.length);
      startedRef.current = true;
      return;
    }
    if (typeof window === "undefined") {
      setVisibleLength(text.length);
      startedRef.current = true;
      return;
    }

    const shouldStart = !startedRef.current;
    startedRef.current = true;
    if (shouldStart) setVisibleLength(0);
    if (!text.length) return;

    const chunk = Math.max(1, Math.ceil(text.length / TYPEWRITER_TICKS));
    let timer = 0;
    timer = window.setInterval(() => {
      setVisibleLength((current) => {
        const next = Math.min(text.length, current + chunk);
        if (next >= text.length) window.clearInterval(timer);
        return next;
      });
    }, TYPEWRITER_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [active, deferUntilActive, reducedMotion, replayKey, text]);

  return text.slice(0, visibleLength);
}

function TypewriterMarkdown({
  text,
  replayKey,
  active,
  deferUntilActive,
}: {
  text: string;
  replayKey: string;
  active: boolean;
  deferUntilActive: boolean;
}) {
  const visibleText = useTypewriterText(
    text,
    active,
    replayKey,
    deferUntilActive,
  );
  return (
    <IncrementalMarkdown
      codeHighlight="plain"
      text={visibleText}
    />
  );
}

function LoadingBlock({ label }: { label: string }) {
  return (
    <div className="learning-canvas-placeholder" role="status" aria-live="polite">
      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
      <span>{label}正在加载中…</span>
    </div>
  );
}

function PreviewImage({ fileId, alt }: { fileId: string; alt: string }) {
  const [retry, setRetry] = useState(0);
  const [image, setImage] = useState<{fileId: string; url: string; error: string}>({fileId: "", url: "", error: ""});
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setImage({fileId, url: "", error: fileId ? "" : "插图文件不存在。"});
    if (fileId) void apiClient.getBlob(`/files/${encodeURIComponent(fileId)}/content`)
      .then(blob => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({fileId, url: objectUrl, error: ""});
      })
      .catch((error: unknown) => {
        if (active) setImage({fileId, url: "", error: error instanceof Error ? error.message : "插图加载失败。"});
      });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [fileId, retry]);
  const current = image.fileId === fileId ? image : null;
  if (current?.error) return <div role="alert"><p>插图暂不可用：{current.error}</p><Button size="sm" variant="outline" onClick={() => setRetry(value => value + 1)}>重试插图</Button></div>;
  return current?.url ? <img className="learning-canvas-image" src={current.url} alt={alt} onError={() => setImage({fileId, url: "", error: "图片无法解码。"})} /> : <LoadingBlock label="插图" />;
}

function SvgIllustration({ svg, caption }: { svg: string; caption: string }) {
  if (!svg.trim()) return <LoadingBlock label="图解" />;
  return (
    <figure className="learning-canvas-illustration">
      <img
        alt={caption || "学习内容图解"}
        src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`}
      />
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
}

function ActivityPreview({
  activity,
  activityState,
  onAction,
  onReset,
  onOpen,
  onStart,
  startPending = false,
}: {
  activity: ActivitySpec;
  activityState?: ActivityState;
  onAction?: (actionId: string) => void | Promise<void>;
  onReset?: () => void;
  onOpen?: () => void;
  onStart?: () => void;
  startPending?: boolean;
}) {
  const busy = !activityState;
  return (
    <section className="learning-canvas-card" aria-label="互动练习">
      <div className="learning-canvas-card__heading">
        <FlaskConical className="size-4" aria-hidden="true" />
        <strong>{activity.title}</strong>
        {activityState?.completed ? (
          <span className="learning-success">目标已完成</span>
        ) : null}
      </div>
      <p>{activity.instructions}</p>
      {activityState ? (
        <div className="learning-canvas-state">
          {activity.variables.map((variable) => (
            <div key={variable.id}>
              <span>{variable.label}</span>
              <strong>
                {variable.states?.[String(activityState.values[variable.id])] ??
                  activityState.values[variable.id] ??
                  "—"}
                {variable.unit ? <small> {variable.unit}</small> : null}
              </strong>
            </div>
          ))}
        </div>
      ) : null}
      {busy ? (
        onStart ? (
          <Button size="sm" disabled={startPending} onClick={onStart}>
            开始互动 <ArrowRight className="size-3.5" />
          </Button>
        ) : <LoadingBlock label="互动练习" />
      ) : (
        <div className="learning-canvas-actions">
          {activity.actions.map((action) => (
            <Button
              key={action.id}
              size="sm"
              variant="outline"
              onClick={() => void onAction?.(action.id)}
            >
              {action.label}
            </Button>
          ))}
          {onReset ? (
            <Button size="sm" variant="ghost" onClick={onReset}>
              重新实验
            </Button>
          ) : null}
        </div>
      )}
      {activityState?.feedback ? (
        <p aria-live="polite" className="learning-canvas-feedback">
          {activityState.feedback}
        </p>
      ) : null}
      {onOpen ? (
        <Button className="mt-2" size="sm" variant="ghost" onClick={onOpen}>
          打开完整互动实验 <ArrowRight className="size-3.5" />
        </Button>
      ) : null}
    </section>
  );
}

export type LearningCanvasNode = {
  graphId: string;
  nodeId?: string;
  nodeLabel?: string;
  /** Set by graph-open actions so the package starts as a compact card. */
  learningPackage?: boolean;
  /** Changes for every graph-open request, including cache hits. */
  openKey?: string;
};

type LearningPackageCanvasViewProps = {
  nodeLabel: string;
  description?: string;
  manifest?: LearningManifest | null;
  /** Build status is used to keep placeholders visible while stages run. */
  buildStatus?: string;
  buildError?: string;
  startPending?: boolean;
  readSections?: string[];
  onSectionRead?: (sectionId: string) => void;
  progressBusy?: boolean;
  activityState?: ActivityState;
  onActivityAction?: (actionId: string) => void | Promise<void>;
  onResetActivity?: () => void;
  onStartActivity?: () => void;
  onStartLearning?: () => void;
  onBuild?: () => void;
  buildPending?: boolean;
  onOpenActivity?: () => void;
  onOpenExam?: () => void;
  onFollowUp?: () => void;
  /** Graph launches start compact and replay the lesson on first expansion. */
  collapsedByDefault?: boolean;
  /** Only graph-launched learning packages replay cached Markdown. */
  replayOnOpen?: boolean;
  /** Stable per-open identity used to remount the typewriter renderer. */
  replayKey?: string;
};

/**
 * Chat-canvas presentation for a node learning package.
 *
 * The wrapper deliberately uses the same assistant message hooks as the
 * conversation renderer. This keeps typography, selection and follow-up
 * layout consistent when the component is mounted next to the normal composer
 * while allowing each build stage to appear independently.
 */
function LearningPackageCanvasView({
  nodeLabel,
  description,
  manifest,
  buildStatus,
  buildError,
  startPending = false,
  readSections = [],
  onSectionRead,
  progressBusy = false,
  activityState,
  onActivityAction,
  onResetActivity,
  onStartActivity,
  onStartLearning,
  onBuild,
  buildPending,
  onOpenActivity,
  onOpenExam,
  onFollowUp,
  collapsedByDefault = false,
  replayOnOpen = false,
  replayKey = "learning-package",
}: LearningPackageCanvasViewProps) {
  const blueprint = manifest?.blueprint;
  const lesson = manifest?.lesson;
  const activity = manifest?.activity;
  const exam = manifest?.exam;
  const image = manifest?.image;
  // A missing manifest is not necessarily an active load. For an on-demand
  // node there may simply be no build yet; treating that state as loading hid
  // the only control that can enqueue the build and left the canvas spinning
  // forever. Only an actually queued/running build should show placeholders.
  const buildActive = ["queued", "running"].includes(buildStatus ?? "");
  const [collapsed, setCollapsed] = useState(collapsedByDefault);
  const [replayVersion, setReplayVersion] = useState(0);
  const expandedOnceRef = useRef(!collapsedByDefault);

  // A graph-open request can replace the node in place (for example from the
  // bound graph rail). Reset the compact shell and the one-shot replay for the
  // new request without reacting to ordinary query/cache updates.
  useEffect(() => {
    setCollapsed(collapsedByDefault);
    expandedOnceRef.current = !collapsedByDefault;
    setReplayVersion((current) => current + 1);
  }, [collapsedByDefault, replayKey]);

  const toggleCollapsed = () => {
    if (!collapsed) {
      setCollapsed(true);
      return;
    }
    setCollapsed(false);
    if (!expandedOnceRef.current) {
      expandedOnceRef.current = true;
      setReplayVersion((current) => current + 1);
    }
  };

  const startLearning = () => {
    // Starting a lesson is an explicit open action, so reveal the package even
    // when the graph launched it in its compact state.
    if (collapsed) toggleCollapsed();
    onStartLearning?.();
  };

  const startActivity = () => {
    if (collapsed) toggleCollapsed();
    onStartActivity?.();
  };

  const bodyReplayKey = `${replayKey}:${replayVersion}`;

  return (
    <article
      className={`is-assistant learning-package-canvas${collapsed ? " is-collapsed" : " is-expanded"}`}
      data-learning-node={nodeLabel}
      data-learning-package-state={collapsed ? "collapsed" : "expanded"}
    >
      <div className="w-full" data-message-content>
        <div className="message-answer-segment learning-canvas-content" data-message-selectable-text>
          <header className="learning-canvas-header">
            <div className="learning-canvas-header__topline">
              <span className="learning-canvas-eyebrow">
                <BookOpen className="size-4" aria-hidden="true" /> 学习包
              </span>
              <Button
                aria-controls={`learning-package-body-${replayKey}`}
                aria-expanded={!collapsed}
                aria-label={collapsed ? "展开学习包" : "折叠学习包"}
                className="learning-canvas-toggle"
                onClick={toggleCollapsed}
                size="sm"
                variant="ghost"
              >
                {collapsed ? "展开学习包" : "折叠学习包"}
                <ChevronDown aria-hidden="true" className="size-4" />
              </Button>
            </div>
            <h1>{blueprint?.title || nodeLabel}</h1>
            {description ? <p>{description}</p> : null}
            {collapsed ? (
              <p className="learning-canvas-collapsed-note">
                教材、互动练习与测评已折叠，
                {replayOnOpen
                  ? "展开后将按打字机效果播放一次。"
                  : "展开后即可继续查看。"}
              </p>
            ) : null}
            {onFollowUp ? (
              <Button className="w-fit" size="sm" variant="outline" onClick={onFollowUp}>
                <MessageCircle className="size-4" />继续追问
              </Button>
            ) : null}
            {["failed", "cancelled", "skipped"].includes(buildStatus ?? "") && <p role="status">{buildError || (buildStatus === "cancelled" ? "生成已取消，可重新创建学习包。" : buildStatus === "skipped" ? "生成已跳过，请检查节点状态后重新创建。" : "生成失败，可重试失败阶段。")}</p>}
            {blueprint ? (
              <>
                <div className="learning-canvas-objectives">
                  <strong>本节目标</strong>
                  <ul>
                    {blueprint.objectives.map((objective) => (
                      <li key={objective}>{objective}</li>
                    ))}
                  </ul>
                  <span>预计 {blueprint.estimated_minutes} 分钟</span>
                </div>
                {["failed", "cancelled", "skipped"].includes(buildStatus ?? "") && onBuild ? (
                  <Button size="sm" onClick={onBuild} disabled={buildPending}>
                    {buildPending ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}
                    {buildStatus === "failed" ? "重试失败阶段" : "重新创建学习包"}
                  </Button>
                ) : null}
              </>
            ) : buildActive ? (
              <LoadingBlock label="学习目标" />
            ) : onBuild ? (
              <Button size="sm" onClick={onBuild} disabled={buildPending}>
                {buildPending ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}
                {buildPending
                  ? "正在创建学习包…"
                  : buildStatus === "failed"
                    ? "重试学习包生成"
                    : "创建学习包"}
              </Button>
            ) : (
              <p>节点内容尚未准备好，请先审核并发布图谱节点。</p>
            )}
            {onStartLearning && !activity ? (
              <Button className="w-fit" size="sm" disabled={startPending} onClick={startLearning}>
                <Play className="size-4" />开始学习
              </Button>
            ) : null}
          </header>

          <div
            aria-hidden={collapsed}
            className={`learning-canvas-body${collapsed ? " is-hidden" : ""}`}
            id={`learning-package-body-${replayKey}`}
          >
              {image ? <PreviewImage fileId={image.file_id} alt={image.alt} /> : buildActive ? <LoadingBlock label="插图" /> : null}
              {lesson ? (
                <>
                  <SvgIllustration svg={lesson.svg} caption={lesson.caption} />
                  {lesson.sections.map((section) => {
                    const read = readSections.includes(section.id);
                    const sectionReplayKey = `${bodyReplayKey}:${section.id}`;
                    return (
                      <section className="learning-canvas-section" id={`learning-section-${section.id}`} key={section.id}>
                        <h2>{section.title}</h2>
                        <div className="learning-prose">
                          <TypewriterMarkdown
                            active={replayOnOpen && !collapsed}
                            deferUntilActive={replayOnOpen}
                            key={sectionReplayKey}
                            replayKey={sectionReplayKey}
                            text={normalizeLearningMarkdown(section.body)}
                          />
                        </div>
                        {section.takeaway ? (
                          <aside className="learning-takeaway" aria-label="本节小结">
                            <span className="learning-takeaway__label">本节小结</span>
                            <p>{section.takeaway}</p>
                          </aside>
                        ) : null}
                        {onSectionRead ? (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={read || progressBusy}
                            onClick={() => onSectionRead(section.id)}
                          >
                            {read ? "本节已读" : "标记本节已读"}
                          </Button>
                        ) : null}
                      </section>
                    );
                  })}
                  {lesson.html ? (
                    <iframe
                      className="learning-demo"
                      title="本节交互演示"
                      srcDoc={sandboxedHtmlPreviewDocument(lesson.html, { offline: true, subappClient: true })}
                      sandbox="allow-scripts"
                      referrerPolicy="no-referrer"
                    />
                  ) : null}
                </>
              ) : buildActive ? (
                <LoadingBlock label="图文教材" />
              ) : null}

              {activity ? (
                activityState ? (
                  <ActivityCard
                    spec={activity}
                    state={activityState}
                    busy={progressBusy || !onActivityAction}
                    onAction={async (actionId) => {
                      await onActivityAction?.(actionId);
                    }}
                    onReset={() => onResetActivity?.()}
                  />
                ) : (
                  <ActivityPreview
                    activity={activity}
                    activityState={activityState}
                    onAction={onActivityAction}
                    onOpen={onOpenActivity}
                    onReset={onResetActivity}
                    onStart={startActivity}
                    startPending={startPending}
                  />
                )
              ) : buildActive ? (
                <LoadingBlock label="互动练习" />
              ) : null}

              {exam ? (
                <section className="learning-canvas-card learning-canvas-exam" aria-label="闯关测评">
                  <div className="learning-canvas-card__heading">
                    <Medal className="size-4" aria-hidden="true" />
                    <strong>{exam.title}</strong>
                  </div>
                  <p>
                    {exam.questions.length} 道题 · 满分 100 · {exam.pass_score} 分通过
                  </p>
                  {onOpenExam ? (
                    <Button size="sm" onClick={onOpenExam}>
                      打开测评 <ArrowRight className="size-3.5" />
                    </Button>
                  ) : null}
                </section>
              ) : buildActive ? (
                <LoadingBlock label="闯关测评" />
              ) : null}

              {manifest?.provenance ? <p className="learning-muted">{manifest.provenance}</p> : null}
              {manifest?.notes?.map((note) => (
                <p className="learning-muted" key={note}>{note}</p>
              ))}
          </div>
        </div>
      </div>
    </article>
  );
}

export type LearningPackageCanvasHostProps = {
  workspaceId: string;
  sessionId?: string;
  learningNode: LearningCanvasNode;
};

/** Data-connected wrapper used by the normal conversation canvas. */
export function LearningPackageCanvas(props: LearningPackageCanvasHostProps) {
  return <LearningPackageCanvasContent key={`${props.workspaceId}:${props.sessionId ?? ""}:${props.learningNode.nodeId ?? ""}:${props.learningNode.openKey ?? ""}`} {...props} />;
}

function LearningPackageCanvasContent({
  workspaceId,
  sessionId,
  learningNode,
}: LearningPackageCanvasHostProps) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const nodeId = learningNode.nodeId;
  const query = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "learning-page", nodeId ?? ""),
    queryFn: ({ signal }) => learningApi.page(nodeId!, signal),
    enabled: Boolean(nodeId),
    retry: false,
    refetchInterval: (current) =>
      ["queued", "running"].includes(current.state.data?.build?.status ?? "")
        ? 2_000
        : false,
  });
  const enrollment = query.data?.enrollment ?? null;
  const setEnrollment = (row: Enrollment) => {
    client.setQueryData<LearningPageData>(
      workspaceQueryKey(workspaceId, "learning-page", nodeId ?? ""),
      current => current ? {...current, enrollment: row} : current,
    );
  };
  const start = useMutation({
    mutationFn: () => learningApi.start(nodeId!),
    onMutate: () => client.cancelQueries({queryKey: workspaceQueryKey(workspaceId, "learning-page", nodeId ?? "")}),
    onSuccess: setEnrollment,
    onError: (error: Error) => toast.error(error.message),
  });
  const build = useMutation({
    mutationFn: () => query.data?.build?.status === "failed"
      ? learningApi.control(query.data.build.id, "retry")
      : learningApi.build(nodeId!),
    onSuccess: (row) => {
      client.setQueryData<LearningPageData>(
        workspaceQueryKey(workspaceId, "learning-page", nodeId ?? ""),
        current => current ? {...current, build: row} : current,
      );
      void query.refetch();
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const progress = useMutation({
    onMutate: () => client.cancelQueries({queryKey: workspaceQueryKey(workspaceId, "learning-page", nodeId ?? "")}),
    mutationFn: (payload: { section_id?: string; action_id?: string; reset_activity?: boolean }) => {
      if (!enrollment) throw new Error("请先开始学习");
      return learningApi.progress(enrollment.id, {
        expected_revision: enrollment.revision,
        ...payload,
      });
    },
    onSuccess: setEnrollment,
    onError: (error: Error) => {
      toast.error(error.message);
      void query.refetch();
    },
  });
  if (!nodeId) return null;
  if (query.isPending) return <LoadingBlock label="学习包" />;
  if (!query.data) {
    return <div className="learning-canvas-error" role="alert"><p>{query.error?.message ?? "学习包暂时无法读取。"}</p><Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>重新读取学习包</Button></div>;
  }
  const data = query.data;
  const manifest = data.package?.manifest ?? data.preview ?? null;
  const buildActive = ["queued", "running"].includes(data.build?.status ?? "");
  const trainingReady = Boolean(data.package || data.preview?.training_ready);
  return (
    <>
    {query.isError && <div className="learning-canvas-error" role="alert"><p>更新失败，已保留上次读取的内容。{query.error?.message}</p><Button size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新状态</Button></div>}
    <LearningPackageCanvasView
      nodeLabel={data.node.label || learningNode.nodeLabel || "当前学习节点"}
      description={data.node.description}
      manifest={manifest}
      buildStatus={data.package ? undefined : data.build?.status}
      buildError={data.build?.error}
      startPending={start.isPending}
      collapsedByDefault={learningNode.learningPackage === true}
      replayOnOpen={learningNode.learningPackage === true}
      replayKey={learningNode.openKey ?? `${workspaceId}:${sessionId ?? ""}:${nodeId}`}
      readSections={enrollment?.progress.sections ?? []}
      onSectionRead={enrollment ? (sectionId) => progress.mutate({ section_id: sectionId }) : undefined}
      progressBusy={progress.isPending || start.isPending}
      activityState={enrollment?.progress.activity}
      onActivityAction={async (actionId) => {
        await progress.mutateAsync({ action_id: actionId });
      }}
      onResetActivity={() => progress.mutate({ reset_activity: true })}
      onStartActivity={trainingReady ? () => start.mutate() : undefined}
      onStartLearning={trainingReady && !enrollment && !data.preview?.activity && !data.package?.manifest.activity ? () => start.mutate() : undefined}
      onBuild={data.package || buildActive || data.node.graph_status === "candidate" ? undefined : () => build.mutate()}
      buildPending={build.isPending}
      onOpenActivity={data.package ? () => navigate(`/w/${workspaceId}/learn/nodes/${nodeId}?tab=lab`) : undefined}
      onOpenExam={data.package ? () => navigate(`/w/${workspaceId}/learn/nodes/${nodeId}?tab=exam${sessionId ? `&returnSession=${encodeURIComponent(sessionId)}` : ""}`) : undefined}
      onFollowUp={() =>
        window.dispatchEvent(
          new CustomEvent("learngraph:compose", {
            detail: {
              content: `请继续讲解「${data.node.label}」，结合刚才的学习内容给出一个例子，并指出容易混淆的地方。`,
            },
          }),
        )
      }
    />
    </>
  );
}
