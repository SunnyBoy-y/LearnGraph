import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StreamUI } from "./stream-ui";
import { parseUISnapshot } from "@/lib/stream-ui";
import { placeRichResponseParts } from "@/features/chat/chat-message-parts";
import { fileStreamUrl } from "@/api/files";
import type { MessagePart } from "@/types/sessions";

vi.mock("@/components/ai-elements/lazy-streamdown", () => ({ LazyStreamdown: ({children}: {children: string}) => <p>{children}</p> }));
vi.mock("./message-part-renderer", () => ({ TextWithCitations: ({content}: {content: string}) => <p>{content}</p> }));
vi.mock("@/api/files", () => ({fileStreamUrl: vi.fn(async (id: string) => `/api/v1/files/${id}/stream?ticket=test`)}));

const snapshot = (blocks: unknown[], revision = 0) => ({ protocol: "suip/0.1", block_id: "document", component: "document", revision,
  state: "streaming", props: {title: "解释"}, blocks });
const illustration = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><circle cx="320" cy="160" r="80" fill="#ddd"/><text x="320" y="300" text-anchor="middle" font-size="24">主体示意</text></svg>';

describe("rich document in the existing message lane", () => {
  it("replaces an input preview at the same revision and commits the same part", async () => {
    const preview = {tool_call_id: "call:1", sequence: 1, base: null};
    const {rerender} = render(<StreamUI partId="input-preview" data={{...snapshot([
      {id: "p", type: "paragraph", text: "先显示第一段"},
      {id: "sim", type: "simulation", engine: "chase"},
    ]), preview}} />);
    expect(await screen.findByText("先显示第一段")).toBeInTheDocument();
    expect(screen.getByText("内容正在生成…")).toBeInTheDocument();
    fireEvent.change(await screen.findByRole("slider", {name: "追赶速度"}), {target: {value: "3"}});
    rerender(<StreamUI partId="input-preview" data={{...snapshot([
      {id: "p", type: "paragraph", text: "先显示第一段，然后补齐"},
      {id: "sim", type: "simulation", engine: "chase"},
    ]), preview: {...preview, sequence: 2}}} />);
    expect(await screen.findByText("先显示第一段，然后补齐")).toBeInTheDocument();
    expect(screen.getByRole("slider", {name: "追赶速度"})).toHaveValue("3");
    rerender(<StreamUI partId="input-preview" data={{...snapshot([
      {id: "p", type: "paragraph", text: "完整内容"},
      {id: "sim", type: "simulation", engine: "chase"},
    ], 1), state: "completed", preview: null}} />);
    expect(await screen.findByText("完整内容")).toBeInTheDocument();
    expect(screen.queryByText("内容正在生成…")).toBeNull();
    expect(screen.getByRole("slider", {name: "追赶速度"})).toHaveValue("3");
  });
  it("replays an interrupted preview without describing it as committed", async () => {
    render(<StreamUI partId="interrupted-preview" failed settled data={{...snapshot([
      {id: "p", type: "paragraph", text: "已收到的半段内容"},
    ]), preview: {tool_call_id: "call:2", sequence: 1, base: null}}} />);
    expect(await screen.findByText("已收到的半段内容")).toBeInTheDocument();
    expect(screen.getByText("内容生成中断，显示已接收部分")).toBeInTheDocument();
  });
  it.each(["no_results", "timeout", "search_failed", "download_failed", "provider_unavailable", "disabled", "storage_failed", "capacity"])("draws the model's SVG when image search ends with %s", async reason => {
    render(<StreamUI partId={`svg-${reason}`} data={snapshot([{id: "pic", type: "image", alt: "叶片", caption: "叶片说明", fallback_svg: illustration, resource_state: "unavailable", resource_reason: reason}])} />);
    expect(await screen.findByRole("img", {name: "叶片"})).toHaveAttribute("viewBox", "0 0 640 400");
    expect(screen.getByRole("figure", {name: "叶片（SVG 示意图）"})).toBeInTheDocument();
    expect(screen.queryByText(/非真实照片|叶片说明/)).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("switches a pending resource in place without replacing sibling state", async () => {
    const blocks = [{id: "group", type: "media_text", columns: 2}, {id: "pic", parent_id: "group", type: "image", alt: "叶片", fallback_svg: illustration, resource_state: "pending"}, {id: "sim", parent_id: "group", type: "simulation", engine: "chase"}];
    const {rerender} = render(<StreamUI partId="svg-transition" data={snapshot(blocks)} />);
    fireEvent.change(await screen.findByRole("slider", {name: "追赶速度"}), {target: {value: "3"}});
    expect(screen.queryByText(/非真实照片/)).toBeNull();
    rerender(<StreamUI partId="svg-transition" data={{...snapshot(blocks.map(node => node.id === "pic" ? {...node, resource_state: "unavailable", resource_reason: "no_results"} : node)), resource_revision: 1}} />);
    expect(await screen.findByRole("img", {name: "叶片"})).toBeInTheDocument();
    expect(screen.getByRole("slider", {name: "追赶速度"})).toHaveValue("3");
    expect(screen.queryByText(/未找到相关图片/)).toBeNull();
  });
  it("keeps a successful photo and switches to SVG on actual load failure without photo attribution", async () => {
    render(<StreamUI partId="svg-load-failure" data={snapshot([{id: "pic", type: "image", alt: "叶片", fallback_svg: illustration, resource_state: "ready", media: [{url: "https://photos.example/leaf.jpg", title: "Original photo", source_url: "https://photos.example/leaf", attribution: "Photographer"}]}])} />);
    const photo = await screen.findByAltText("Original photo");
    expect(photo.tagName).toBe("IMG");
    expect(screen.queryByText(/非真实照片/)).toBeNull();
    fireEvent.error(photo);
    expect(await screen.findByRole("img", {name: "叶片"})).toHaveAttribute("viewBox");
    expect(screen.queryByText(/原图片暂时无法加载/)).toBeNull();
    expect(screen.queryByRole("link", {name: "Original photo"})).toBeNull();
    expect(screen.queryByText(/Photographer/)).toBeNull();
  });
  it("also switches to SVG if the native file ticket cannot be obtained", async () => {
    vi.mocked(fileStreamUrl).mockRejectedValueOnce(new Error("file unavailable"));
    render(<StreamUI partId="svg-file-failure" data={snapshot([{id: "pic", type: "image", alt: "叶片", fallback_svg: illustration, resource_state: "ready", media: [{url: "https://photos.example/leaf.jpg", file_id: "missing-native-file"}]}])} />);
    expect(await screen.findByRole("img", {name: "叶片"})).toHaveAttribute("viewBox");
    expect(screen.queryByText(/原图片暂时无法加载/)).toBeNull();
    expect(screen.queryByRole("button", {name: "放大图片：叶片"})).toBeNull();
  });
  it("keeps adjacent widgets in the model's vertical flow without inferring a parallel group", async () => {
    const {container} = render(<StreamUI partId="vertical-intent" data={snapshot([
      {id: "s", type: "simulation", engine: "chase"},
      {id: "s2", type: "simulation", engine: "quadratic"},
    ])} />);
    const slider = await screen.findByRole("slider", {name: "追赶速度"});
    expect(slider.closest(".rich-doc__node")?.parentElement).toHaveClass("rich-doc__flow");
    expect(container.querySelector(".rich-doc__row, .rich-doc__columns, [data-paired]")).toBeNull();
  });

  it("honors a model-selected parallel group and retains interaction state as it streams", async () => {
    const blocks = [{id: "row", type: "row", columns: 3}, {id: "s", parent_id: "row", type: "simulation", engine: "chase"}];
    const {rerender} = render(<StreamUI partId="flow-patch" data={snapshot(blocks)} />);
    fireEvent.change(await screen.findByRole("slider", {name: "追赶速度"}), {target: {value: "3"}});
    rerender(<StreamUI partId="flow-patch" data={snapshot([...blocks, {id: "s2", parent_id: "row", type: "simulation", engine: "quadratic"}], 1)} />);
    expect(screen.getByRole("slider", {name: "追赶速度"})).toHaveValue("3");
    const group = screen.getByRole("slider", {name: "追赶速度"}).closest(".rich-doc__node")?.parentElement;
    expect(group).toHaveClass("rich-doc__row");
    expect(group?.style.getPropertyValue("--rich-columns")).toBe("2");
  });

  it("shows the recorded retrieval failure without inventing a permission issue", async () => {
    render(<StreamUI partId="missing-image-reason" data={snapshot([
      {id: "pic", type: "image", alt: "形态示例", resource_state: "unavailable", resource_reason: "no_results"},
    ])} />);
    expect(await screen.findByText("未找到相关图片")).toBeInTheDocument();
    expect(screen.queryByText(/权限/)).not.toBeInTheDocument();
  });
  it("keeps local simulation state as later blocks stream and after final placement", async () => {
    const blocks = [{id: "layout", type: "columns"}, {id: "sim", parent_id: "layout", type: "simulation", engine: "chase"}];
    const { rerender } = render(<StreamUI partId="rich-state" data={snapshot(blocks)} />);
    const slider = await screen.findByRole("slider", {name: "追赶速度"});
    fireEvent.change(slider, {target: {value: "3"}});
    expect(screen.getByText(/相遇时间 5.00/)).toBeInTheDocument();
    rerender(<StreamUI partId="rich-state" data={snapshot([...blocks, {id: "after", type: "paragraph", text: "后续文字"}], 1)} />);
    expect(await screen.findByText("后续文字")).toBeInTheDocument();
    expect(screen.getByRole("slider", {name: "追赶速度"})).toHaveValue("3");
    rerender(<section><StreamUI partId="rich-state" data={{...snapshot(blocks, 2), state: "completed"}} /></section>);
    expect(await screen.findByRole("slider", {name: "追赶速度"})).toHaveValue("3");
  });

  it("uses keyboard tabs and retains the selected panel across patches", async () => {
    const blocks = [{id: "tabs", type: "tabs", label: "视角"},
      {id: "one", parent_id: "tabs", type: "paragraph", label: "第一", text: "第一内容"},
      {id: "two", parent_id: "tabs", type: "paragraph", label: "第二", text: "第二内容"}];
    const {rerender} = render(<StreamUI partId="rich-tabs" data={snapshot(blocks)} />);
    const first = await screen.findByRole("tab", {name: "第一"});
    first.focus(); fireEvent.keyDown(first, {key: "ArrowRight"});
    expect(screen.getByRole("tab", {name: "第二"})).toHaveAttribute("aria-selected", "true");
    rerender(<StreamUI partId="rich-tabs" data={snapshot([...blocks, {id: "tail", type: "paragraph", text: "新增"}], 1)} />);
    expect(screen.getByRole("tab", {name: "第二"})).toHaveAttribute("aria-selected", "true");
  });

  it("renders a bounded step demonstration without making a network request", async () => {
    const onAction = vi.fn();
    render(<StreamUI partId="rich-sort" onAction={onAction} data={snapshot([{id: "s", type: "simulation", engine: "sort_steps", values: [3,1,2]}])} />);
    await userEvent.click(await screen.findByRole("button", {name: "下一步"}));
    expect(screen.getByRole("list", {name: "当前数组"})).toHaveTextContent("132");
    expect(onAction).not.toHaveBeenCalled();
  });

  it("uses image fallbacks and preserves source metadata", async () => {
    const node = {id: "image", type: "image", alt: "土星", resource_state: "ready", media: [{url: "https://images.example.com/saturn.jpg", source_url: "https://example.com/saturn", title: "Saturn", attribution: "CC BY"}]};
    render(<StreamUI partId="rich-image" data={snapshot([node])} />);
    fireEvent.error(await screen.findByAltText("Saturn"));
    expect(screen.getByText(/图片暂时无法加载/)).toBeInTheDocument();
    expect(screen.getByRole("link", {name: "Saturn"})).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("loads searched images through the existing authenticated file lane while text continues", async () => {
    const blocks = [{id: "image", type: "image", alt: "蜜蜂", resource_state: "pending"}];
    const {rerender} = render(<StreamUI partId="rich-native-file" data={snapshot(blocks)} />);
    expect(await screen.findByText("正在查找相关图片…")).toBeInTheDocument();
    rerender(<StreamUI partId="rich-native-file" data={snapshot([
      {...blocks[0], resource_state: "ready", media: [{url: "https://photos.example/bee.jpg", file_id: "native-file", title: "Bee", attribution: "Author · CC BY"}]},
      {id: "tail", type: "paragraph", text: "图片就绪后继续解释。"},
    ], 1)} />);
    expect(await screen.findByAltText("Bee")).toHaveAttribute("src", "/api/v1/files/native-file/stream?ticket=test");
    expect(screen.getByText("图片就绪后继续解释。")).toBeInTheDocument();
    expect(screen.getByText(/Author · CC BY/)).toBeInTheDocument();
  });

  it("binds historical photo captions and accessible names to each actual source, not the search intent", async () => {
    const blocks = [
      {id: "fig", type: "figure", caption: "两只鸟站在岩壁上"},
      {id: "photos", parent_id: "fig", type: "gallery", alt: "站在岩壁上的鸟", caption: "两只鸟都是站立姿态", resource_state: "ready", media: [
        {url: "https://photos.example/flying.jpg", title: "Bird in flight", source_url: "https://photos.example/flight"},
        {url: "https://photos.example/resting.jpg", title: "Bird resting"},
      ]},
    ];
    render(<StreamUI partId="source-labels" data={snapshot(blocks)} />);
    expect(await screen.findByRole("img", {name: "Bird in flight"})).toBeInTheDocument();
    expect(screen.getByRole("img", {name: "Bird resting"})).toBeInTheDocument();
    expect(screen.queryByText(/站在岩壁|站立姿态/)).toBeNull();
    await userEvent.click(screen.getByRole("button", {name: "放大图片：Bird in flight"}));
    expect(screen.getByRole("dialog", {name: "图片预览"})).toBeInTheDocument();
    expect(screen.getAllByRole("img", {name: "Bird in flight"}).length).toBeGreaterThan(0);
  });

  it("uses a neutral label when an actual source has no title", async () => {
    render(<StreamUI partId="untitled-photo" data={snapshot([{id:"photo", type:"image", alt:"飞翔的红鸟", caption:"红鸟在飞", resource_state:"ready", media:[{url:"https://photos.example/bird.jpg"}]}])} />);
    expect(await screen.findByRole("img", {name:"检索图片"})).toBeInTheDocument();
    expect(screen.queryByText("红鸟在飞")).toBeNull();
  });

  it.each([
    [{id: "x", type: "stack", parent_id: "x"}],
    [{id: "x", type: "paragraph", onClick: "alert(1)"}],
    [{id: "x", type: "simulation", engine: "eval"}],
    [{id: "x", type: "image", media: [{url: "http://127.0.0.1/secret"}]}],
    [{id: "x", type: "image", media: [{url: "https://127.1/secret"}]}],
    [{id: "x", type: "image", media: [{url: "https://host.local/secret"}]}],
    [{id: "x", type: "image", media: [{url: "https://example.com/script.svg"}]}],
    [{id: "x", type: "chart", visual: {blocks: []}}],
  ])("rejects unsafe or malformed node trees", node => {
    expect(parseUISnapshot(snapshot([node]))).toBeNull();
  });

  it("places live UI between text segments even if its original ordinal precedes the reference", () => {
    const block: MessagePart = {id: "part-ui", type: "ui", status: "completed", data: {block_id: "doc"}};
    const text: MessagePart = {id: "part-text", type: "text", status: "streaming", content: "前文[[artifact:block_id:doc]]后文"};
    const placed = placeRichResponseParts([block, text]);
    expect(placed.map(part => part.type)).toEqual(["text", "ui", "text"]);
    expect(placed[1]).toBe(block);
    const appended = placeRichResponseParts([block, {...text, content: text.content + "继续"}]);
    expect(appended.map(part => part.id)).toEqual(placed.map(part => part.id));
    expect(appended[0].content).toBe("前文");
  });
});
