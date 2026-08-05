-- Add trade_notes column to inventory_items table for trade functionality
ALTER TABLE inventory_items
ADD COLUMN IF NOT EXISTS trade_notes text;

-- Create index for trade status queries if not exists
CREATE INDEX IF NOT EXISTS idx_inventory_items_status
ON inventory_items(user_id, status);
