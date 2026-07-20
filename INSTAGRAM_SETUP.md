# Instagram Auto-Posting Setup Guide

This feature allows you to post Pokémon card inventory items to Instagram Stories with automatic Stripe payment links.

## Quick Start

### 1. Database Setup

Add these columns to the `user_profiles` table (run in Supabase SQL Editor):

```sql
ALTER TABLE user_profiles 
  ADD COLUMN IF NOT EXISTS instagram_access_token text,
  ADD COLUMN IF NOT EXISTS instagram_business_account_id text;
```

These columns store per-user Instagram credentials securely in the database.

### 2. Get Instagram Business Account Credentials

#### Create a Meta App (if you don't have one)
1. Go to [Meta Developers](https://developers.facebook.com/apps)
2. Create a new app (Business type recommended)
3. Add the "Instagram" product to your app

#### Get Your Access Token
1. Go to your app's dashboard
2. Select "Instagram" product
3. Go to "Tools" → "Access Token Tool" or use the Graph API Explorer
4. Generate a new access token with these permissions:
   - `instagram_business_content_publish`
   - `instagram_business_profile_get_name`
5. Copy the token to your PokeManager Settings → Instagram section

**Note:** Access tokens expire. You may need to refresh periodically using the token refresh endpoint and reconnect in Settings.

#### Get Your Instagram Business Account ID
1. In your app, go to "Settings" → "Basic"
2. In Graph API Explorer, run this query:
   ```
   GET /me/instagram_business_accounts
   ```
3. Find your account ID in the response
4. Copy it to your PokeManager Settings → Instagram section

### 3. Supabase Storage Configuration

The feature requires a public storage bucket for temporary image hosting.

1. **Create Storage Bucket** (if not exists):
   - Go to Supabase Dashboard → Storage
   - Create a new public bucket named `ig-stories`
   - Set to "Public" access (Images are temporary and deleted after posting)

2. **Verify Permissions**:
   - Bucket should allow authenticated POST/PUT
   - Images should be publicly readable (they're temporary)

### 4. Stripe Configuration

Stripe payment links are created dynamically when posting. Your Stripe keys must already be configured:

```env
STRIPE_SECRET_KEY=sk_test_...  # Required for payment link creation
```

## Usage

### 1. Connect Your Instagram Account (First Time Only)

1. Go to **Settings** → **Instagram** section
2. Enter your Instagram access token and business account ID
3. Click **"Connect Instagram"** — the token will be validated
4. Once connected, you'll see a green ✓ badge

### 2. Post Cards to Instagram

1. Navigate to Inventory
2. Find the card you want to post
3. Click the **📸 IG** button on the card
4. The app will:
   - Generate a story image (1080×1920px)
   - Create a Stripe payment link
   - Upload the image to Supabase
   - Post to Instagram Stories with payment link sticker
   - Copy the payment link to a modal for manual sharing

### What Gets Posted

**Story Image includes:**
- "AZARAM VAULT" brand text at top (muted gray)
- Card image centered (~70% of width with thin white border)
- Item price in large white text at bottom
- Card name below price

**Instagram Sticker:**
- Automatic link sticker with Stripe payment URL
- Users can tap to complete payment directly

## Database Schema

### User Profiles (per-user credentials)

```sql
ALTER TABLE user_profiles 
ADD COLUMN IF NOT EXISTS instagram_access_token text,
ADD COLUMN IF NOT EXISTS instagram_business_account_id text;
```

- `instagram_access_token`: Facebook Graph API access token (stored securely)
- `instagram_business_account_id`: Your Instagram Business Account ID

### Inventory Items (posting metadata)

```sql
ALTER TABLE inventory_items 
ADD COLUMN ig_story_posted boolean DEFAULT false,
ADD COLUMN ig_payment_link text,
ADD COLUMN ig_media_id text,
ADD COLUMN ig_posted_at timestamptz;
```

- `ig_story_posted`: Boolean flag indicating if posted
- `ig_payment_link`: Stripe payment link URL
- `ig_media_id`: Instagram media ID (for reference/tracking)
- `ig_posted_at`: Timestamp of posting

## API Endpoints

### Settings Endpoints

#### GET /api/settings/instagram
Returns connection status and masked account ID.

**Response:**
```json
{
  "connected": true,
  "account_id": "123456...7890"
}
```

#### POST /api/settings/instagram
Connect Instagram account (validates token).

**Request:**
```json
{
  "access_token": "IGBAbcdef...",
  "business_account_id": "123456789"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Instagram account connected"
}
```

#### DELETE /api/settings/instagram
Disconnect Instagram account.

**Response:**
```json
{
  "success": true,
  "message": "Instagram account disconnected"
}
```

### Posting Endpoint

#### POST /api/instagram/post-story

**Request:**
```json
{
  "item_id": 123
}
```

**Success Response:**
```json
{
  "success": true,
  "payment_link": "https://buy.stripe.com/...",
  "ig_media_id": "17999...",
  "message": "Posted 'Charizard Holographic' to Instagram!"
}
```

**Error Response:**
```json
{
  "detail": "Instagram credentials not configured. Visit Settings → Instagram to connect your account."
}
```

## Troubleshooting

### Settings: Token validation failed
- Make sure you're using an access token, not an app ID or other credential
- Token may have expired — refresh via Meta Dashboard
- Verify token has required permissions: `instagram_business_content_publish`

### Posting: Instagram credentials not configured
- Go to Settings → Instagram and connect your account
- Credentials must be validated before use

### 400: Item has no price
- Set `quick_price`, `live_price`, or `sale_price` on the item before posting
- These are already populated if you have PriceCharting configured

### 500: Supabase Storage Error
- Verify `ig-stories` bucket exists and is public
- Check Supabase service key has storage permissions
- Verify image upload path is correct

### 500: Stripe Payment Link Error
- Verify `STRIPE_SECRET_KEY` is set correctly
- Check Stripe API quota (rate limits)
- Ensure account can create prices/payment links (active Stripe account required)

### Story Image Not Generating
- Pillow must be installed: `pip install pillow` (included in requirements.txt)
- System fonts should be available at `/usr/share/fonts/truetype/dejavu/`
- If fonts unavailable, default font is used (less pretty but functional)

## Frontend UI

### Inventory Card Indicators
- **📸 IG badge**: Shows small Instagram icon if item already posted
- **📸 IG button**: Click to post to stories
  - Button changes to **✓ Posted** after successful posting
  - Disabled state while uploading (shows ⏳…)

### Success Flow
1. Click "📸 IG" button
2. Loading spinner appears
3. On success:
   - Badge appears on card
   - Modal shows payment link with copy button
   - Button text changes to "✓ Posted"
4. Share payment link in DMs or post in story comments

## Security Notes

- Access tokens are stored encrypted in Supabase (per-user, never in environment variables)
- Tokens are validated before saving using Facebook Graph API
- Credentials are never sent to the browser (server-side only)
- Images uploaded to Supabase are temporary (should be cleaned up periodically)
- Stripe payment links are public but single-use (user clicks once per share)
- Each user manages their own Instagram credentials independently
- Account IDs are masked in the UI (showing first 6 and last 4 digits)

## Future Enhancements

- Batch posting (multiple items at once)
- Scheduled posting (queue for specific time)
- Custom story templates
- Image cache optimization
- Automatic image cleanup after posting
