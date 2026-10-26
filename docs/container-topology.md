# Three resident containers

The default Compose topology is:

- `app`: API on 8000 and a separate read-only preview process on 8001.
- `sandboxd`: isolated Docker sandbox management.
- `egress-proxy`: reviewed sandbox network access.

The browser still uses two origins, normally ports 18000 and 18001.
Capability tokens, CSP and the bidirectional subapp protocol are unchanged.
The supervisor waits for API liveness before starting preview, checks both
origins for container health, and stops both children when either exits.
`tini` remains PID 1; SIGTERM is forwarded and shutdown has a bounded timeout.
This is process separation, not the former container/filesystem isolation.

## Optional PostgreSQL provisioner

The agent is in the `database-migration` profile and uses `restart: "no"`.
Normal `up` does not create it or wait for it. The administrator's existing
password-confirmed migration action automatically asks sandboxd to create or
start the fixed agent after the verified backup. Once PostgreSQL is ready,
the agent stops before the data-copy stage. Failures/cancellation also stop it.

The admin-only lifecycle endpoint accepts only an opaque migration job ID,
never an image, command, mount or arbitrary container ID. sandboxd derives
the approved image/data/network from the one application in its own Compose
project and checks the complete agent configuration before starting/stopping.
The web app still has no Docker socket. A persisted 120-second lease is renewed
every 15 seconds while provisioning; the daemon sweeps expired leases after
an application crash, including when the application container is stopped.

Update/restart both the application and sandboxd when upgrading an older
installation. Compose sets `LEARNGRAPH_DATABASE_MIGRATION_AGENT_AUTO_START=true`
and `SANDBOXD_MANAGED_DATABASE_ENABLED=true`. Local/non-Docker launches default
to manual control. For troubleshooting, explicit service selection still works:

```sh
node scripts/compose.mjs up -d --no-deps database-migration-agent
node scripts/compose.mjs ps database-migration-agent
```

Leave a manually started agent running until provisioning has finished,
then stop it:

```sh
node scripts/compose.mjs stop database-migration-agent
```

The provisioned PostgreSQL container is independent and must not be stopped
or deleted together with the agent. External PostgreSQL does not need it.
Do not enable this optional profile globally via `COMPOSE_PROFILES`.

## Existing installations

This topology change is a manual deployment migration, not an image-only
updater operation. Keep the previous images/configuration for rollback and
take a verified site backup. Do not delete or replace any data volumes.

1. Build the new application image with `APP_VERSION` matching `VERSION`.
2. Remove the obsolete `preview` service entry from the generated
   `docker-compose.update.json`, if present. Preserve unrelated overrides.
   Pin the new image for `app`; pin a compatible image for the optional agent.
3. Check for an active database migration and finish/cancel it before stopping
   its agent. Stop the old preview container to release host port 18001.
4. Recreate only `app` with the same data volume, secrets and public origins.
   Do not run a broad `down`, `--remove-orphans` or any volume cleanup.
5. Verify API readiness, both origins, a real subapp interaction round trip,
   and the absence of a Docker socket in `app`.
6. Remove only the stopped legacy preview container, without `-v`. Set the
   old agent's restart policy to `no` and stop it, or recreate it under the
   optional profile on its next use. Keep its data and existing PostgreSQL.

The updated host updater discovers both the combined and legacy topologies.
Saved rollback jobs from another topology are rejected instead of recreating
removed services. Restart the host updater after updating its source.
