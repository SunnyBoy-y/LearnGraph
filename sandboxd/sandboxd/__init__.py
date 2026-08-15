"""sandboxd — LearnGraph isolated sandbox control plane.

The daemon is the ONLY component that talks to Docker Engine. It exposes a
versioned, authenticated, capability-limited API that LearnGraph consumes
through ``SandboxdBackend``; see docs/sandboxd-migration-plan.md.

Security posture:
- fail closed on missing/invalid configuration;
- bearer service token read from a file, constant-time comparison;
- mutating operations are idempotent (scope + operation + key + payload hash);
- every sandbox operation is bound to a canonical ownership scope;
- per-sandbox named volumes and per-sandbox internal egress networks;
- hardened runner semantics (UID 65532, read-only rootfs, drop ALL, NNP,
  seccomp, resource limits) are enforced by the Docker runtime adapter.
"""

__version__ = "0.1.0"
