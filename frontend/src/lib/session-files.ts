import type { SessionFile } from "@/types/files";

export function sessionFileKey(file: SessionFile) {
  return file.file_id ?? `${file.path ?? ""}:${file.filename}`;
}

export function sessionFilePath(file: SessionFile) {
  return (file.path || file.filename).replace(/\\/g, "/").split("/").filter(segment => segment && segment !== ".").join("/");
}
