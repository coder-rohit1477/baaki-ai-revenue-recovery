-- Baaki — provider secret bootstrap. Run AFTER migration 0001, as baaki_migrate:
--   psql "$BAAKI_MIGRATE_DSN" -v ON_ERROR_STOP=1 -v webhook_secret="$BAAKI_WEBHOOK_SECRET" -f bootstrap/secrets.sql
-- The committed file never contains a secret; the value arrives as a psql variable (H19).
SET ROLE baaki_owner;
INSERT INTO baaki.provider_secret (provider, webhook_secret, rotated_at)
VALUES ('razorpay', :'webhook_secret', now())
ON CONFLICT (provider) DO UPDATE SET webhook_secret = EXCLUDED.webhook_secret, rotated_at = now();
RESET ROLE;
