-- WP19: Like-with-like token accounting (prompt tokens vs prompt tokens)
ALTER TABLE proxy_requests ADD COLUMN IF NOT EXISTS prompt_tokens_actual INT;
ALTER TABLE proxy_requests ADD COLUMN IF NOT EXISTS completion_tokens_actual INT;
ALTER TABLE proxy_requests ADD COLUMN IF NOT EXISTS reasoning_tokens_actual INT;

ALTER TABLE proxy_demo_requests ADD COLUMN IF NOT EXISTS prompt_tokens_actual INT;
ALTER TABLE proxy_demo_requests ADD COLUMN IF NOT EXISTS completion_tokens_actual INT;
ALTER TABLE proxy_demo_requests ADD COLUMN IF NOT EXISTS reasoning_tokens_actual INT;
