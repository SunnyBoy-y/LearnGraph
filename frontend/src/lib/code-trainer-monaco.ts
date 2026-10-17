import * as monaco from "monaco-editor/editor/editor.api";
// Register the standalone editor's services before the first model is created.
// Loading contributions piecemeal caused missing CodeLens/DnD services when
// several generated trainers mounted together in the actual conversation.
import "monaco-editor/editor/editor.main";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import TypeScriptWorker from "monaco-editor/language/typescript/ts.worker?worker";
import { javascriptDefaults, typescriptDefaults } from "monaco-editor/languages/features/typescript/register";
import "monaco-editor/languages/definitions/python/register";
import "monaco-editor/languages/definitions/javascript/register";
import "monaco-editor/languages/definitions/typescript/register";
import "monaco-editor/languages/definitions/java/register";
import "monaco-editor/languages/definitions/go/register";
import "monaco-editor/languages/definitions/cpp/register";
import "monaco-editor/languages/definitions/rust/register";
import "monaco-editor/editor/contrib/suggest/browser/suggestController";
import "monaco-editor/editor/contrib/snippet/browser/snippetController2";
import "monaco-editor/editor/contrib/find/browser/findController";
import "monaco-editor/editor/contrib/folding/browser/folding";
import "monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching";
import "monaco-editor/editor/contrib/hover/browser/hoverContribution";
import "monaco-editor/editor/contrib/parameterHints/browser/parameterHints";
import "monaco-editor/editor/contrib/multicursor/browser/multicursor";
import "monaco-editor/editor/contrib/wordOperations/browser/wordOperations";
import "monaco-editor/editor/contrib/linesOperations/browser/linesOperations";
import "monaco-editor/editor/contrib/comment/browser/comment";
import "monaco-editor/editor/contrib/clipboard/browser/clipboard";

(globalThis as typeof globalThis & { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker: (_id: string, label: string) => label === "typescript" || label === "javascript" ? new TypeScriptWorker() : new EditorWorker(),
};
const runnerTypes = "declare const stdin: string; declare function readline(): string | null; declare function print(...values: unknown[]): void;";
for (const defaults of [javascriptDefaults, typescriptDefaults]) {
  defaults.addExtraLib(runnerTypes, "file:///code-trainer/runner.d.ts");
  defaults.setEagerModelSync(true);
}
// TS is transpiled at execution time, while the editor provides type diagnostics.
javascriptDefaults.setDiagnosticsOptions({ noSemanticValidation: true });

const keywords: Record<string, string> = {
  python: "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case",
  javascript: "async await break case catch class const continue debugger default delete do else export extends false finally for from function if import in instanceof let new null of return static super switch this throw true try typeof undefined var void while with yield",
  typescript: "abstract any as asserts async await bigint boolean break case catch class const constructor continue declare default delete do else enum export extends false finally for from function if implements import in infer instanceof interface is keyof let namespace never new null number object of private protected public readonly return satisfies static string super switch symbol this throw true try type typeof undefined unknown var void while yield",
  java: "abstract boolean break byte case catch char class continue default do double else enum extends false final finally float for if implements import instanceof int interface long native new null package private protected public return short static strictfp super switch synchronized this throw throws transient true try void volatile while var record sealed",
  go: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var",
  cpp: "alignas auto bool break case catch char class const constexpr continue default delete do double else enum explicit export extern false float for friend if inline int long namespace new noexcept nullptr operator private protected public register return short signed sizeof static struct switch template this throw true try typedef typename union unsigned using virtual void volatile while",
  rust: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
};
const smartCompletion = new WeakMap<monaco.editor.ITextModel, boolean>();
const pythonBuiltins = "abs all any bool dict enumerate filter float input int len list map max min open print range reversed round set sorted str sum tuple zip".split(" ");
for (const [language, words] of Object.entries(keywords)) {
  monaco.languages.registerCompletionItemProvider(language, {
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position);
      const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
      return { suggestions: [
        ...words.split(" ").map(label => ({ label, insertText: label, kind: monaco.languages.CompletionItemKind.Keyword, range, sortText: `0_${label}`, detail: "语言关键词" })),
        ...(language === "python" && smartCompletion.get(model) !== false ? pythonBuiltins.map(label => ({ label, insertText: label, kind: monaco.languages.CompletionItemKind.Function, range, detail: "Python 内置函数" })) : []),
      ] };
    },
  });
}
export function completionOptions(enabled: boolean): monaco.editor.IEditorOptions & monaco.editor.IGlobalEditorOptions {
  return {
    quickSuggestions: { other: true, comments: false, strings: false },
    quickSuggestionsDelay: 80,
    wordBasedSuggestions: enabled ? "currentDocument" : "off",
    suggestOnTriggerCharacters: enabled,
    parameterHints: { enabled },
    tabCompletion: "on",
    acceptSuggestionOnEnter: "on",
    suggest: {
      showKeywords: true, showWords: enabled, showSnippets: enabled,
      showMethods: enabled, showFunctions: enabled, showVariables: enabled,
      showFields: enabled, showProperties: enabled, showClasses: enabled,
      showInterfaces: enabled, showModules: enabled, showConstants: enabled,
      showEnums: enabled, showEnumMembers: enabled, showStructs: enabled,
      showTypeParameters: enabled, showValues: enabled, showEvents: enabled,
      showOperators: enabled, showReferences: enabled, showFiles: enabled,
      showFolders: enabled, showIssues: enabled, showUsers: enabled,
    },
  };
}
export function setSmartCompletion(model: monaco.editor.ITextModel, enabled: boolean) {
  smartCompletion.set(model, enabled);
}
export { monaco };
