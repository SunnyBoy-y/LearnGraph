import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, BookOpen, FlaskConical, LoaderCircle, Medal, MessageCircle, Play } from "lucide-react";
import { toast } from "sonner";

import { apiClient } from "@/api/client";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { IncrementalMarkdown } from "@/components/ai-elements/incremental-markdown";
import { Button } from "@/components/ui/button";
import { workspaceQueryKey } from "@/lib/query-keys";
import type {
  ActivitySpec,
  ActivityState,
  LearningBuildPreview,
  Enrollment,
  Manifest,
} from "./node-learning-api";
import { learningApi } from "./node-learning-api";
import { ActivityCard } from "./node-learning-page";

type LearningManifest = Manifest | LearningBuildPreview;

function LoadingBlock({ label }: { label: string }) {
  return (
    <div className="learning-canvas-placeholder" role="status" aria-live="polite">
      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
      <span>{label}正在加载中…</span>
    </div>
  );
}

function PreviewImage({ fileId, alt }: { fileId: string; alt: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!fileId) return;
    let active = true;
    let objectUrl = "";
    void apiClient
      .getBlob(`/files/${encodeURIComponent(fileId)}/content`)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (active) setUrl("");
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId]);
  return url ? (
    <img className="learning-canvas-image" src={url} alt={alt} />
  ) : (
    <LoadingBlock label="插图" />
  );
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
}: {
  activity: ActivitySpec;
  activityState?: ActivityState;
  onAction?: (actionId: string) => void | Promise<void>;
  onReset?: () => void;
  onOpen?: () => void;
  onStart?: () => void;
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
          <Button size="sm" onClick={onStart}>
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
};

type LearningPackageCanvasViewProps = {
  nodeLabel: string;
  description?: string;
  manifest?: LearningManifest | null;
  /** Build status is used to keep placeholders visible while stages run. */
  buildStatus?: string;
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
}: LearningPackageCanvasViewProps) {
  const blueprint = manifest?.blueprint;
  const lesson = manifest?.lesson;
  const activity = manifest?.activity;
  const exam = manifest?.exam;
  const image = manifest?.image;
  const loading = !manifest || ["queued", "running", "failed"].includes(buildStatus ?? "");

  return (
    <article className="is-assistant learning-package-canvas" data-learning-node={nodeLabel}>
      <div className="w-full" data-message-content>
        <div className="message-answer-segment space-y-5" data-message-selectable-text>
          <header className="learning-canvas-header">
            <span className="learning-canvas-eyebrow">
              <BookOpen className="size-4" aria-hidden="true" /> 学习包
            </span>
            <h1>{blueprint?.title || nodeLabel}</h1>
            {description ? <p>{description}</p> : null}
            {onFollowUp ? (
              <Button className="w-fit" size="sm" variant="outline" onClick={onFollowUp}>
                <MessageCircle className="size-4" />继续追问
              </Button>
            ) : null}
            {blueprint ? (
              <div className="learning-canvas-objectives">
                <strong>本节目标</strong>
                <ul>
                  {blueprint.objectives.map((objective) => (
                    <li key={objective}>{objective}</li>
                  ))}
                </ul>
                <span>预计 {blueprint.estimated_minutes} 分钟</span>
              </div>
            ) : loading ? (
              <LoadingBlock label="学习目标" />
            ) : onBuild ? (
              <Button size="sm" onClick={onBuild} disabled={buildPending}>
                {buildPending ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}
                {buildPending ? "正在创建学习包…" : "创建学习包"}
              </Button>
            ) : (
              <p>节点内容尚未准备好，请先审核并发布图谱节点。</p>
            )}
            {onStartLearning && !activity ? (
              <Button className="w-fit" size="sm" onClick={onStartLearning}>
                <Play className="size-4" />开始学习
              </Button>
            ) : null}
          </header>

          {image ? <PreviewImage fileId={image.file_id} alt={image.alt} /> : loading ? <LoadingBlock label="插图" /> : null}
          {lesson ? (
            <>
              <SvgIllustration svg={lesson.svg} caption={lesson.caption} />
              {lesson.sections.map((section) => {
                const read = readSections.includes(section.id);
                return (
                  <section className="learning-canvas-section" id={`learning-section-${section.id}`} key={section.id}>
                    <h2>{section.title}</h2>
                    <div className="learning-prose">
                      <IncrementalMarkdown text={section.body} codeHighlight="plain" />
                    </div>
                    {section.takeaway ? <p className="learning-takeaway">{section.takeaway}</p> : null}
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
          ) : loading ? (
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
                onStart={onStartActivity}
              />
            )
          ) : loading ? (
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
          ) : loading ? (
            <LoadingBlock label="闯关测评" />
          ) : null}

          {manifest?.provenance ? <p className="learning-muted">{manifest.provenance}</p> : null}
          {manifest?.notes?.map((note) => (
            <p className="learning-muted" key={note}>{note}</p>
          ))}
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
export function LearningPackageCanvas({
  workspaceId,
  sessionId,
  learningNode,
}: LearningPackageCanvasHostProps) {
  const navigate = useNavigate();
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
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  useEffect(() => {
    setEnrollment(query.data?.enrollment ?? null);
  }, [query.data?.enrollment]);
  const start = useMutation({
    mutationFn: () => learningApi.start(nodeId!),
    onSuccess: setEnrollment,
    onError: (error: Error) => toast.error(error.message),
  });
  const build = useMutation({
    mutationFn: () => learningApi.build(nodeId!),
    onSuccess: () => void query.refetch(),
    onError: (error: Error) => toast.error(error.message),
  });
  const progress = useMutation({
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
  if (query.isError || !query.data) {
    return <p className="learning-canvas-error">学习包暂时无法读取，请稍后重试。</p>;
  }
  const data = query.data;
  const manifest = data.package?.manifest ?? data.preview ?? null;
  const buildActive = ["queued", "running"].includes(data.build?.status ?? "");
  const trainingReady = Boolean(data.package || data.preview?.training_ready);
  return (
    <LearningPackageCanvasView
      nodeLabel={data.node.label || learningNode.nodeLabel || "当前学习节点"}
      description={data.node.description}
      manifest={manifest}
      buildStatus={data.package ? undefined : data.build?.status}
      readSections={enrollment?.progress.sections ?? []}
      onSectionRead={enrollment ? (sectionId) => progress.mutate({ section_id: sectionId }) : undefined}
      progressBusy={progress.isPending}
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
  );
}
