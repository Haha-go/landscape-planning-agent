-- ============================================================
-- Landscape Planning Agent — PostgreSQL 初始化脚本
-- Docker 首次启动时自动执行
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 问答会话
-- ============================================================
CREATE TABLE IF NOT EXISTS qa_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    thread_id       VARCHAR(128) NOT NULL UNIQUE,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qa_sessions_thread_id ON qa_sessions (thread_id);

CREATE TABLE IF NOT EXISTS qa_messages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID REFERENCES qa_sessions(id) ON DELETE CASCADE,
    thread_id       VARCHAR(128) NOT NULL,
    role            VARCHAR(16) NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    route           VARCHAR(32)
                    CHECK (route IS NULL OR route IN ('direct', 'retrieve', 'direct_no_hit', 'other')),
    citations       JSONB,
    metrics         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qa_messages_thread_id ON qa_messages (thread_id);
CREATE INDEX IF NOT EXISTS idx_qa_messages_session_id ON qa_messages (session_id);
CREATE INDEX IF NOT EXISTS idx_qa_messages_route ON qa_messages (route);

-- ============================================================
-- 规划方案评估
-- ============================================================
CREATE TABLE IF NOT EXISTS plan_reviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_path       VARCHAR(512) NOT NULL,
    file_name       VARCHAR(256),
    extracted_data  JSONB,
    scores          JSONB,
    overall_score   FLOAT,
    summary         TEXT,
    issues          JSONB,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    error_msg       TEXT,
    review_status   VARCHAR(32) NOT NULL DEFAULT 'pending',
    submitter_id    VARCHAR(64),
    assignee_id     VARCHAR(64),
    human_scores    JSONB,
    human_overall   FLOAT,
    human_comment   TEXT,
    final_decision  VARCHAR(32),
    decided_at      TIMESTAMPTZ,
    decided_by      VARCHAR(64),
    run_metrics     JSONB,
    needs_human_review BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_plan_reviews_status ON plan_reviews (status);
CREATE INDEX IF NOT EXISTS idx_plan_reviews_created ON plan_reviews (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_plan_reviews_review_status ON plan_reviews (review_status);
CREATE INDEX IF NOT EXISTS idx_plan_reviews_assignee ON plan_reviews (assignee_id);

-- ============================================================
-- 人工审核
-- ============================================================
CREATE TABLE IF NOT EXISTS review_assignments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    review_id       UUID NOT NULL REFERENCES plan_reviews(id) ON DELETE CASCADE,
    reviewer_id     VARCHAR(64) NOT NULL,
    claimed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at          TIMESTAMPTZ,
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'released', 'done')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_review_assignments_review ON review_assignments (review_id);

CREATE TABLE IF NOT EXISTS review_actions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    review_id       UUID NOT NULL REFERENCES plan_reviews(id) ON DELETE CASCADE,
    actor_id        VARCHAR(64) NOT NULL,
    action          VARCHAR(32) NOT NULL
                    CHECK (action IN (
                        'claim', 'assign', 'comment', 'override_score',
                        'approve', 'reject', 'request_revision'
                    )),
    before_snapshot JSONB,
    after_snapshot  JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_review_actions_review ON review_actions (review_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_metrics (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    route           VARCHAR(32),
    expected_route  VARCHAR(32),
    route_hit       BOOLEAN,
    tool            VARCHAR(32),
    success         BOOLEAN,
    latency_ms      FLOAT,
    retried         BOOLEAN,
    recovered       BOOLEAN,
    step            VARCHAR(64),
    tokens          INT,
    detail          JSONB
);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_created ON agent_metrics (created_at DESC);

-- ============================================================
-- 知识库文档元数据
-- ============================================================
CREATE TABLE IF NOT EXISTS kb_documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(256) NOT NULL,
    standard_code   VARCHAR(128),
    source_path     VARCHAR(512) NOT NULL,
    chunk_count     INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_standard_code ON kb_documents (standard_code);

-- ============================================================
-- updated_at 触发器
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'qa_sessions', 'plan_reviews', 'kb_documents'
    ]
    LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS trg_%s_updated_at ON %s;
            CREATE TRIGGER trg_%s_updated_at
            BEFORE UPDATE ON %s
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        ', t, t, t, t);
    END LOOP;
END;
$$;
