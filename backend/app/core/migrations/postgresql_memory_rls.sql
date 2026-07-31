-- PostgreSQL defense-in-depth policies for event-primary deployments.
-- The runtime role MUST NOT own these tables and must set the transaction-local
-- app.tenant_id/app.workspace_id/app.user_id values before accessing them.

ALTER TABLE memory_streams ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_streams FORCE ROW LEVEL SECURITY;
CREATE POLICY memory_stream_scope ON memory_streams
USING (
  tenant_id = current_setting('app.tenant_id', true)
  AND (
    workspace_id IS NULL
    OR workspace_id = current_setting('app.workspace_id', true)
  )
  AND (
    subject_user_id IS NULL
    OR subject_user_id = current_setting('app.user_id', true)
  )
)
WITH CHECK (
  tenant_id = current_setting('app.tenant_id', true)
  AND (
    workspace_id IS NULL
    OR workspace_id = current_setting('app.workspace_id', true)
  )
  AND (
    subject_user_id IS NULL
    OR subject_user_id = current_setting('app.user_id', true)
  )
);

ALTER TABLE memory_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_events FORCE ROW LEVEL SECURITY;
CREATE POLICY memory_event_scope ON memory_events
USING (
  tenant_id = current_setting('app.tenant_id', true)
  AND (
    workspace_id IS NULL
    OR workspace_id = current_setting('app.workspace_id', true)
  )
  AND (
    subject_user_id IS NULL
    OR subject_user_id = current_setting('app.user_id', true)
  )
)
WITH CHECK (
  tenant_id = current_setting('app.tenant_id', true)
  AND (
    workspace_id IS NULL
    OR workspace_id = current_setting('app.workspace_id', true)
  )
  AND (
    subject_user_id IS NULL
    OR subject_user_id = current_setting('app.user_id', true)
  )
);
