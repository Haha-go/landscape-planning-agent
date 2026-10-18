# backend/db/migrations.py
# 启动时幂等 Schema：可在已有 Postgres（含 EduAgent 旧表）上安全补建景观表

from sqlalchemy import text
from backend.dependencies import AsyncSessionLocal
from backend.core.logger import get_logger

logger = get_logger(__name__)

# 全部幂等 DDL。旧 EduAgent 表不删除。
_MIGRATIONS: list[tuple[str, str]] = [
    (
        "ext.uuid-ossp",
        'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
    ),
    (
        "table.qa_sessions",
        """
        CREATE TABLE IF NOT EXISTS qa_sessions (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            thread_id       VARCHAR(128) NOT NULL UNIQUE,
            summary         TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx.qa_sessions_thread_id",
        "CREATE INDEX IF NOT EXISTS idx_qa_sessions_thread_id ON qa_sessions (thread_id)",
    ),
    (
        "table.qa_messages",
        """
        CREATE TABLE IF NOT EXISTS qa_messages (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            session_id      UUID REFERENCES qa_sessions(id) ON DELETE CASCADE,
            thread_id       VARCHAR(128) NOT NULL,
            role            VARCHAR(16) NOT NULL
                            CHECK (role IN ('user', 'assistant', 'system')),
            content         TEXT NOT NULL,
            route           VARCHAR(32)
                            CHECK (route IS NULL OR route IN (
                                'direct', 'retrieve', 'direct_no_hit', 'other'
                            )),
            citations       JSONB,
            metrics         JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx.qa_messages_thread_id",
        "CREATE INDEX IF NOT EXISTS idx_qa_messages_thread_id ON qa_messages (thread_id)",
    ),
    (
        "idx.qa_messages_session_id",
        "CREATE INDEX IF NOT EXISTS idx_qa_messages_session_id ON qa_messages (session_id)",
    ),
    (
        "idx.qa_messages_created",
        "CREATE INDEX IF NOT EXISTS idx_qa_messages_created "
        "ON qa_messages (thread_id, created_at)",
    ),
    (
        "qa_messages.metrics",
        "ALTER TABLE qa_messages ADD COLUMN IF NOT EXISTS metrics JSONB",
    ),
    (
        "qa_messages.route_widen",
        "ALTER TABLE qa_messages ALTER COLUMN route TYPE VARCHAR(32)",
    ),
    (
        "qa_messages.route_check_drop",
        """
        DO $$
        BEGIN
            ALTER TABLE qa_messages DROP CONSTRAINT IF EXISTS qa_messages_route_check;
        EXCEPTION WHEN undefined_object THEN
            NULL;
        END;
        $$
        """,
    ),
    (
        "qa_messages.route_check_add",
        """
        DO $$
        BEGIN
            ALTER TABLE qa_messages
                ADD CONSTRAINT qa_messages_route_check
                CHECK (
                    route IS NULL OR route IN (
                        'direct', 'retrieve', 'direct_no_hit', 'other'
                    )
                );
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
        $$
        """,
    ),
    (
        "idx.qa_messages_route",
        "CREATE INDEX IF NOT EXISTS idx_qa_messages_route ON qa_messages (route)",
    ),
    (
        "table.plan_reviews",
        """
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
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "plan_reviews.issues",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS issues JSONB",
    ),
    (
        "idx.plan_reviews_status",
        "CREATE INDEX IF NOT EXISTS idx_plan_reviews_status ON plan_reviews (status)",
    ),
    (
        "idx.plan_reviews_created",
        "CREATE INDEX IF NOT EXISTS idx_plan_reviews_created ON plan_reviews (created_at DESC)",
    ),
    (
        "table.kb_documents",
        """
        CREATE TABLE IF NOT EXISTS kb_documents (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            title           VARCHAR(256) NOT NULL,
            standard_code   VARCHAR(128),
            source_path     VARCHAR(512) NOT NULL,
            chunk_count     INT NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx.kb_documents_standard_code",
        "CREATE INDEX IF NOT EXISTS idx_kb_documents_standard_code ON kb_documents (standard_code)",
    ),
    (
        "fn.update_updated_at_column",
        """
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
    ),
    (
        "trg.qa_sessions_updated_at",
        """
        DO $$
        BEGIN
            DROP TRIGGER IF EXISTS trg_qa_sessions_updated_at ON qa_sessions;
            CREATE TRIGGER trg_qa_sessions_updated_at
            BEFORE UPDATE ON qa_sessions
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        END;
        $$
        """,
    ),
    (
        "trg.plan_reviews_updated_at",
        """
        DO $$
        BEGIN
            DROP TRIGGER IF EXISTS trg_plan_reviews_updated_at ON plan_reviews;
            CREATE TRIGGER trg_plan_reviews_updated_at
            BEFORE UPDATE ON plan_reviews
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        END;
        $$
        """,
    ),
    (
        "trg.kb_documents_updated_at",
        """
        DO $$
        BEGIN
            DROP TRIGGER IF EXISTS trg_kb_documents_updated_at ON kb_documents;
            CREATE TRIGGER trg_kb_documents_updated_at
            BEFORE UPDATE ON kb_documents
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        END;
        $$
        """,
    ),
    (
        "plan_reviews.review_status",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS review_status VARCHAR(32) DEFAULT 'pending'",
    ),
    (
        "plan_reviews.submitter_id",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS submitter_id VARCHAR(64)",
    ),
    (
        "plan_reviews.assignee_id",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS assignee_id VARCHAR(64)",
    ),
    (
        "plan_reviews.human_scores",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS human_scores JSONB",
    ),
    (
        "plan_reviews.human_overall",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS human_overall FLOAT",
    ),
    (
        "plan_reviews.human_comment",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS human_comment TEXT",
    ),
    (
        "plan_reviews.final_decision",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS final_decision VARCHAR(32)",
    ),
    (
        "plan_reviews.decided_at",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ",
    ),
    (
        "plan_reviews.decided_by",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS decided_by VARCHAR(64)",
    ),
    (
        "plan_reviews.run_metrics",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS run_metrics JSONB",
    ),
    (
        "plan_reviews.needs_human_review",
        "ALTER TABLE plan_reviews ADD COLUMN IF NOT EXISTS needs_human_review BOOLEAN NOT NULL DEFAULT TRUE",
    ),
    (
        "idx.plan_reviews_review_status",
        "CREATE INDEX IF NOT EXISTS idx_plan_reviews_review_status ON plan_reviews (review_status)",
    ),
    (
        "idx.plan_reviews_assignee",
        "CREATE INDEX IF NOT EXISTS idx_plan_reviews_assignee ON plan_reviews (assignee_id)",
    ),
    (
        "table.review_assignments",
        """
        CREATE TABLE IF NOT EXISTS review_assignments (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            review_id       UUID NOT NULL REFERENCES plan_reviews(id) ON DELETE CASCADE,
            reviewer_id     VARCHAR(64) NOT NULL,
            claimed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            due_at          TIMESTAMPTZ,
            status          VARCHAR(16) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'released', 'done')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "idx.review_assignments_review",
        "CREATE INDEX IF NOT EXISTS idx_review_assignments_review ON review_assignments (review_id)",
    ),
    (
        "table.review_actions",
        """
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
        )
        """,
    ),
    (
        "idx.review_actions_review",
        "CREATE INDEX IF NOT EXISTS idx_review_actions_review ON review_actions (review_id, created_at DESC)",
    ),
    (
        "table.agent_metrics",
        """
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
        )
        """,
    ),
    (
        "idx.agent_metrics_created",
        "CREATE INDEX IF NOT EXISTS idx_agent_metrics_created ON agent_metrics (created_at DESC)",
    ),
]


async def run_migrations() -> None:
    """应用启动时执行所有幂等 DDL 补丁。"""
    async with AsyncSessionLocal() as session:
        for desc, sql in _MIGRATIONS:
            try:
                await session.execute(text(sql))
                await session.commit()
                logger.debug("db.migration_applied", column=desc)
            except Exception as e:
                await session.rollback()
                err = str(e)
                if "already exists" not in err.lower():
                    logger.warning("db.migration_failed", column=desc, error=err)

    logger.info("db.migrations_done", count=len(_MIGRATIONS))
