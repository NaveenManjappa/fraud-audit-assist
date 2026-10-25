# ADR-0010: One relational schema, PostgreSQL in production and SQLite for development

Status: Accepted | Date: 2026-10-24

Events are stored append-only in one `events` table: the fields rules read are typed columns, type-specific details
live in a JSON `attributes` column, so a new event type needs no migration. `event_id` is the primary key and
ingestion is idempotent on it. `(account_id, occurred_at)` is indexed for the per-Account timeline.

The schema is portable (SQLAlchemy `Uuid`, `JSON` and a UTC datetime type that restores the timezone SQLite drops),
so the same code and migrations run on PostgreSQL in production and SQLite on a laptop. Two things differ by
dialect, behind `Database`: PostgreSQL takes per-Account advisory locks, while SQLite serialises writes in-process;
and deadlocks or serialisation failures on PostgreSQL replay the whole transaction.

Schema changes are Alembic migrations; a test checks they build exactly the models' schema on both databases.
Production refuses SQLite.
