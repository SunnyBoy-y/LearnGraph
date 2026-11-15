import { Folder, FolderOpen } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import type { SessionFile } from "@/types/files";
import { sessionFileKey, sessionFilePath } from "@/lib/session-files";

interface FileDirectory {
  name: string;
  path: string;
  directories: Map<string, FileDirectory>;
  files: SessionFile[];
}

export function SessionFileTree({ files, renderFile }: {
  files: SessionFile[];
  renderFile: (file: SessionFile, name: string) => ReactNode;
}) {
  const root = useMemo(() => {
    const root: FileDirectory = { name: "", path: "", directories: new Map(), files: [] };
    for (const file of files) {
      const segments = sessionFilePath(file).split("/");
      let directory = root;
      for (const name of segments.slice(0, -1)) {
        if (!directory.directories.has(name)) {
          directory.directories.set(name, { name, path: `${directory.path}/${name}`, directories: new Map(), files: [] });
        }
        directory = directory.directories.get(name)!;
      }
      directory.files.push(file);
    }
    return root;
  }, [files]);

  function entries(directory: FileDirectory): ReactNode {
    return <ul className="session-file-tree__entries">
      {[...directory.directories.values()].sort((a, b) => a.name.localeCompare(b.name, "zh-CN", { numeric: true })).map(child => (
        <li key={`directory:${child.path}`}>
          <details className="session-file-tree__directory">
            <summary title={child.path.slice(1)}>
              <Folder className="session-file-tree__folder-closed size-4" />
              <FolderOpen className="session-file-tree__folder-open size-4" />
              <span>{child.name}</span>
            </summary>
            {entries(child)}
          </details>
        </li>
      ))}
      {[...directory.files].sort((a, b) => sessionFilePath(a).localeCompare(sessionFilePath(b), "zh-CN", { numeric: true })).map(file => (
        <li key={`file:${sessionFileKey(file)}`}>{renderFile(file, sessionFilePath(file).split("/").at(-1) || file.filename)}</li>
      ))}
    </ul>;
  }

  return <nav aria-label="文件目录" className="session-file-tree">{entries(root)}</nav>;
}
