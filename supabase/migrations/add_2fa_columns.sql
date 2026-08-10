-- Add 2FA support columns to user_profiles table
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS two_fa_enabled boolean DEFAULT false;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS totp_secret text;

-- Add comment explaining the columns
COMMENT ON COLUMN user_profiles.two_fa_enabled IS 'Whether two-factor authentication is enabled for this user';
COMMENT ON COLUMN user_profiles.totp_secret IS 'Encrypted TOTP secret (base32 encoded, then Fernet encrypted)';

-- Index on two_fa_enabled for efficient queries
CREATE INDEX IF NOT EXISTS idx_user_profiles_2fa_enabled ON user_profiles(two_fa_enabled);
