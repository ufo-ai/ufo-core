-- Pattern for a user-owned table with app-level filtering.
-- The Express backend filters by user_id from the session cookie (express-session).
-- Adapt to your app's tables. Pass to apply_migration().

CREATE TABLE public.items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id text NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_items_user_id ON public.items(user_id);
