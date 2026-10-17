"""Receive one tool's JSON string incrementally without exposing partial files."""
from __future__ import annotations

import hashlib
import json
import uuid

from app.core.errors import AppError
from app.providers.remote.sandbox import validate_agent_workspace_path


class ContentDecoder:
    """Incremental lexer for a top-level content string; final JSON remains authoritative."""
    def __init__(self, emit):
        self.emit = emit
        self.in_string = False
        self.key = None
        self.is_key = True
        self.is_content = False
        self.text = ""
        self.escape = False
        self.unicode = None
        self.high = None

    def feed(self, delta: str):
        output = []
        for char in delta:
            if not self.in_string:
                if char == '"':
                    self.in_string = True
                    self.text = ""
                    self.is_content = not self.is_key and self.key == "content"
                elif char in "{,":
                    self.is_key = True
                elif char == ":":
                    self.is_key = False
                continue
            if self.unicode is not None:
                self.unicode += char
                if len(self.unicode) < 4:
                    continue
                code = int(self.unicode, 16)
                self.unicode = None
                if 0xD800 <= code <= 0xDBFF:
                    self.high = code
                    continue
                if self.high is not None:
                    if not 0xDC00 <= code <= 0xDFFF:
                        raise ValueError("Invalid Unicode surrogate pair")
                    code = 0x10000 + ((self.high - 0xD800) << 10) + code - 0xDC00
                    self.high = None
                decoded = chr(code)
            elif self.escape:
                self.escape = False
                if char == "u":
                    self.unicode = ""
                    continue
                decoded = json.loads('"\\' + char + '"')
            elif char == "\\":
                self.escape = True
                continue
            elif char == '"':
                self.in_string = False
                if self.is_key:
                    self.key = self.text
                continue
            else:
                decoded = char
            if self.is_content:
                output.append(decoded)
            else:
                self.text += decoded
        if output:
            self.emit("".join(output))


# All paths are validated before this trusted, fixed assembler is installed.
# The new file becomes visible with one rename after size/hash validation.
ASSEMBLER = '''import hashlib,json,os,pathlib,tempfile
spec=json.loads(SPEC)
target=pathlib.Path(spec["target"])
parent=target.parent
for ancestor in (parent,*parent.parents):
    if ancestor.is_symlink(): raise RuntimeError("symlink parent")
if target.is_symlink(): raise RuntimeError("symlink target")
parent.mkdir(parents=True,exist_ok=True)
fd,tmp=tempfile.mkstemp(prefix=".lg-write-",dir=str(parent))
try:
    digest=hashlib.sha256()
    with os.fdopen(fd,"wb") as out:
        for part in spec["parts"]:
            data=pathlib.Path(part).read_bytes()
            digest.update(data);out.write(data)
        out.flush();os.fsync(out.fileno())
    if digest.hexdigest()!=spec["sha256"]: raise RuntimeError("stream digest mismatch")
    os.replace(tmp,target)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
'''


class StreamedFileWrite:
    CHUNK_BYTES = 32768

    def __init__(self, service, chat_session_id, sandbox_session_id=None):
        self.service = service
        self.chat_session_id = chat_session_id
        self.session_hint = sandbox_session_id
        self.session = self.handle = None
        self.root = "work/.lg-stream-" + uuid.uuid4().hex
        self.paths = []
        self.buffer = bytearray()
        self.digest = hashlib.sha256()
        self.size = 0
        self.decoder = ContentDecoder(self.append)
        self.closed = False

    def feed(self, delta):
        self.decoder.feed(delta)

    def append(self, text):
        data = text.encode("utf-8")
        self.size += len(data)
        if self.size > min(self.service.settings.sandbox_agent_file_bytes, 4 * 1048576):
            raise AppError(422, "sandbox_file_too_large", "Streamed file exceeds the tool byte limit")
        self.digest.update(data)
        self.buffer.extend(data)
        if len(self.buffer) >= self.CHUNK_BYTES:
            self.flush()

    def container_path(self, path):
        return self.service._container_path(self.chat_session_id, path) if self.service._pooling_enabled(self.session) else path

    def flush(self):
        if not self.buffer:
            return
        if self.session is None:
            self.session, self.handle = self.service._resolve_file_session(self.session_hint, self.chat_session_id)
        path = f"{self.root}/{len(self.paths):06d}.part"
        self.service._runtime_backend(self.session).write_agent_file(self.handle, self.container_path(path), bytes(self.buffer))
        self.paths.append(path)
        self.buffer.clear()

    def materialize(self, payload):
        path = validate_agent_workspace_path(payload.path)
        if hashlib.sha256(payload.content.encode("utf-8")).hexdigest() != self.digest.hexdigest():
            raise AppError(422, "stream_content_mismatch", "Tool arguments do not match streamed bytes")
        if payload.sandbox_session_id and self.session and payload.sandbox_session_id != self.session.id:
            raise AppError(409, "stream_session_mismatch", "Stream belongs to the current workspace session")
        self.flush()
        if self.session is None:
            self.session, self.handle = self.service._resolve_file_session(self.session_hint, self.chat_session_id)
        spec = json.dumps({"target": path, "parts": self.paths, "sha256": self.digest.hexdigest()}, ensure_ascii=True)
        script = "SPEC=" + repr(spec) + "\n" + ASSEMBLER
        helper = self.root + "/commit.py"
        backend = self.service._runtime_backend(self.session)
        backend.write_agent_file(self.handle, self.container_path(helper), script.encode())
        self.paths.append(helper)
        result = backend.exec_agent(self.handle, ("python", helper),
            cwd_relative=self.service._container_prefix(self.chat_session_id) if self.service._pooling_enabled(self.session) else ".",
            timeout_seconds=30, output_limit=65536)
        if result.exit_code or result.timed_out or result.truncated:
            raise AppError(502, "stream_commit_failed", "Streamed file could not be atomically committed")
        return self.session, len(self.paths) - 1

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.session is not None:
            backend = self.service._runtime_backend(self.session)
            for path in self.paths:
                try:
                    backend.delete_agent_file(self.handle, self.container_path(path))
                except Exception:
                    pass  # Runtime expiration also reclaims abandoned staging.
        self.buffer.clear()
