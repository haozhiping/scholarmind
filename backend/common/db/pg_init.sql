-- PostgreSQL schema initialization for ScholarMind Memory (chat-agent only)

CREATE TABLE IF NOT EXISTS conversations (
  id         BIGSERIAL PRIMARY KEY,
  conv_id    UUID UNIQUE NOT NULL,
  user_id    VARCHAR(64),
  title      VARCHAR(256),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conv_conv_id ON conversations(conv_id);

CREATE TABLE IF NOT EXISTS messages (
  id              BIGSERIAL PRIMARY KEY,
  msg_id          UUID UNIQUE NOT NULL,
  conv_id         UUID NOT NULL REFERENCES conversations(conv_id) ON DELETE CASCADE,
  role            VARCHAR(16) NOT NULL,            -- user | assistant | system
  content         TEXT NOT NULL,
  metadata        JSONB,                           -- additional metadata
  created_at      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id);
CREATE INDEX IF NOT EXISTS idx_msg_msg_id ON messages(msg_id);
