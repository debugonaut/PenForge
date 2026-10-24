-- Frozen Phase 01 expansion. Preserve every object/job when identities combine.
CREATE TABLE identity_claims (
  guest_user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
  destination_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  mode text NOT NULL,
  response_body jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_identity_claims_mode_values CHECK (mode IN ('linked','transferred')),
  CONSTRAINT ck_identity_claims_response_body_size CHECK (octet_length(response_body::text) <= 32768),
  CONSTRAINT ck_identity_claims_mode_subjects CHECK ((mode='linked')=(guest_user_id=destination_user_id))
);
CREATE INDEX identity_claims_destination_idx ON identity_claims(destination_user_id);
CREATE INDEX assets_owner_inventory_idx ON assets(owner_user_id,project_id) WHERE state <> 'deleted';
ALTER TABLE jobs ADD COLUMN idempotency_scope_user_id uuid NULL
  CONSTRAINT fk_jobs_idempotency_scope_user_id_users REFERENCES users(id) ON DELETE RESTRICT;
UPDATE jobs SET idempotency_scope_user_id=owner_user_id;
DROP INDEX jobs_idem_uniq;
CREATE UNIQUE INDEX jobs_idem_uniq ON jobs(idempotency_scope_user_id,idempotency_key)
  WHERE idempotency_key IS NOT NULL;
DROP INDEX assets_owner_sha_uniq;
CREATE UNIQUE INDEX assets_owner_sha_uniq ON assets(owner_user_id,sha256)
  WHERE kind='original_upload' AND state='ready' AND duplicate_of IS NULL;
