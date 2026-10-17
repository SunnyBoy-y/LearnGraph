import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CodeTrainer } from "./code-trainer";
import { StreamUIStateScope } from "@/lib/stream-ui-local-state";
import { normalizeLanguage, sameOutput, trainerComponentSchema, trainerPrompt, trainerPropsSchema } from "@/lib/code-trainer";
import { runCode } from "@/lib/code-trainer-runner";
vi.mock("@/lib/code-trainer-runner", () => ({ runCode: vi.fn() }));
vi.mock("./code-trainer-editor", () => ({ CodeTrainerEditor: ({ value, onChange, readOnly }: {value: string; onChange: (value: string) => void; readOnly: boolean}) => <textarea aria-label="代码编辑器" value={value} readOnly={readOnly} onChange={event => onChange(event.target.value)} /> }));
const props = trainerPropsSchema.parse({ title: "输入求和", language: "javascript", starter_code: "console.log(stdin)", stdin: "5", tests: [{ name: "样例", stdin: "5", expected_stdout: "5" }] });
function mount(data = props, options: { onAsk?: (prompt: string) => void; interactive?: boolean } = {}) {
  return render(<StreamUIStateScope.Provider value={crypto.randomUUID()}><CodeTrainer props={data} interactive={options.interactive ?? true} onAsk={options.onAsk} /></StreamUIStateScope.Provider>);
}
describe("code trainer", () => {
  it("compares public output without discarding significant whitespace", () => {
    expect(sameOutput("a  \r\nb\n", "a\nb")).toBe(true);
    expect(sameOutput(" a", "a")).toBe(false);
    expect(sameOutput("a b", "a  b")).toBe(false);
  });
  it("only recognizes the three browser language aliases", () => {
    expect(normalizeLanguage("JS")).toBe("javascript");
    expect(normalizeLanguage("TS")).toBe("typescript");
    expect(normalizeLanguage("py")).toBe("python");
    expect(normalizeLanguage("java")).toBe("java");
  });
  it("rejects executable configuration and excessive test data", () => {
    expect(trainerComponentSchema.safeParse({ component_type: "code_trainer", schema_version: "1.0", props: { ...props, runner_url: "https://example.com" } }).success).toBe(false);
    expect(trainerPropsSchema.safeParse({ ...props, tests: Array(13).fill(props.tests[0]) }).success).toBe(false);
  });
  it("runs only on click and grades exact sample output", async () => {
    vi.mocked(runCode).mockResolvedValue({ status: "completed", stdout: "6\n", stderr: "" });
    mount();
    expect(runCode).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "测试公开样例" }));
    await screen.findByText("已通过 0 / 1 个公开样例");
    expect(screen.getByText(/样例 · 输出不符/)).toBeInTheDocument();
  });
  it("does not count runtime errors as accepted even when stdout matches", async () => {
    vi.mocked(runCode).mockResolvedValue({ status: "runtime_error", stdout: "5", stderr: "error" });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "测试公开样例" }));
    await screen.findByText("已通过 0 / 1 个公开样例");
    expect(screen.getByText(/样例 · 运行错误/)).toBeInTheDocument();
  });
  it("unsupported languages submit source as a prompt without executing", async () => {
    const ask = vi.fn();
    mount({ ...props, language: "java", starter_code: "class Main {}" }, { onAsk: ask });
    expect(screen.queryByRole("button", { name: "运行", exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "交给 AI 分析" }));
    await waitFor(() => expect(ask).toHaveBeenCalledOnce());
    expect(ask.mock.calls[0][0]).toContain("class Main {}");
    expect(ask.mock.calls[0][0]).toContain("此语言没有真实执行");
    expect(runCode).not.toHaveBeenCalled();
  });
  it("sends the edited draft, current input and actual result for local tutoring", async () => {
    const ask = vi.fn();
    const code = "// user draft: tutoring-snapshot-1015\nconsole.log(Number(stdin) + 1);\n";
    vi.mocked(runCode).mockResolvedValue({ status: "completed", stdout: "42\n", stderr: "", durationMs: 3 });
    mount(props, { onAsk: ask });
    fireEvent.change(screen.getByRole("textbox", { name: "代码编辑器" }), { target: { value: code } });
    fireEvent.change(screen.getByRole("textbox", { name: "标准输入" }), { target: { value: "41" } });
    expect(screen.getByLabelText("AI 辅导代码预览").textContent).toBe(code);
    fireEvent.click(screen.getByRole("button", { name: "运行", exact: true }));
    await screen.findByText("运行完成 · 3 ms");
    fireEvent.click(screen.getByRole("button", { name: "请 AI 辅导" }));
    await waitFor(() => expect(ask).toHaveBeenCalledOnce());
    const prompt = ask.mock.calls[0][0] as string;
    expect(prompt).toContain(code);
    expect(prompt).not.toContain(props.starter_code);
    expect(prompt).toContain('"stdin":"41"');
    expect(prompt).toContain('"stdout":"42\\n"');
    expect(prompt).toContain('"public_tests":');
    expect(prompt).toContain("【本轮用户请求：编程辅导】");
  });
  it("does not send obsolete sample results after editing the input", async () => {
    const ask = vi.fn();
    vi.mocked(runCode).mockResolvedValue({ status: "completed", stdout: "5", stderr: "" });
    mount(props, { onAsk: ask });
    fireEvent.click(screen.getByRole("button", { name: "测试公开样例" }));
    await screen.findByText("已通过 1 / 1 个公开样例");
    fireEvent.change(screen.getByRole("textbox", { name: "标准输入" }), { target: { value: "99" } });
    fireEvent.click(screen.getByRole("button", { name: "请 AI 辅导" }));
    await waitFor(() => expect(ask).toHaveBeenCalledOnce());
    expect(ask.mock.calls[0][0]).toContain('"browser_practice_result":null');
  });
  it("inert/read-only controls cannot run or submit", () => {
    mount(props, { onAsk: vi.fn(), interactive: false });
    expect(screen.getByRole("button", { name: "运行", exact: true })).toBeDisabled();
    expect(screen.getByRole("button", { name: "请 AI 辅导" })).toBeDisabled();
  });
  it("cancels a pending run when removed", () => {
    let signal: AbortSignal | undefined;
    vi.mocked(runCode).mockImplementation((_req, value) => { signal = value; return new Promise(() => {}); });
    const view = mount();
    fireEvent.click(screen.getByRole("button", { name: "运行", exact: true }));
    expect(signal?.aborted).toBe(false);
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });
  it("bounds prompts including large logs and reports truncation", () => {
    const prompt = trainerPrompt({ ...props, tests: Array(12).fill({ name: "x", stdin: "a".repeat(4000), expected_stdout: "a".repeat(4000) }) }, "java", "a".repeat(16000), "a".repeat(4000), { stdout: "a".repeat(190000) });
    expect(prompt.length).toBeLessThan(50000);
    expect(prompt).toContain('"truncated":true');
    expect(prompt).toContain("不要调用代码执行");
  });
  it.each(["\u0000".repeat(16000), "`".repeat(16000), "~".repeat(8000) + "`".repeat(8000)])("never truncates the full source when auxiliary material exceeds the budget (%#)", code => {
    const prompt = trainerPrompt({ ...props, description: "x".repeat(8000), tests: Array(12).fill({ name: "x", stdin: "a".repeat(4000), expected_stdout: "a".repeat(4000) }) }, "python", code, "a".repeat(4000), { stdout: "a".repeat(190000) });
    expect(prompt.length).toBeLessThan(50000);
    expect(prompt).toContain(code);
    expect(prompt).toContain('"truncated":true');
  });
});
