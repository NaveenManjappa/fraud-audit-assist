# ADR-0015: The Link Graph is a table in the event store, written in the ingest transaction

Status: Accepted | Date: 2026-10-24

vigil projected events into Neo4j. Here the graph is one table, `identifier_links`: one row per Account per Linking
Identifier, keeping the earliest use (`first_seen`, with the lower event id breaking an exact tie), so links are
order-independent and can be rebuilt from the events at any time (`faa db rebuild-links`).

Links are upserted in the same transaction as the events. That removes vigil's dual-write window (Neo4j committed,
Postgres rolled back) and its dependency on a second database. After the upsert, ingestion finds the Neighbours the
new links can affect, locks them in sorted order and re-scores them in the same transaction, so the first member of a
ring is lifted by the third member's event, not on its own next event.

Every rule query is one hop at a time over an indexed `(kind, value)` lookup, which a relational database answers
well at this scale.

## Considered options

- **Neo4j, as vigil**: Cypher reads well and suits deep graph analytics, but every rule here is one hop at a time, and
  a second store costs a dual write, a rebuild path and an operational dependency. The `LinkGraphReader` interface is
  the seam if graph analytics (communities, centrality) are needed later.
- **Recursive SQL for multi-hop rings**: one query, but harder to read and to keep identical to the in-memory graph.
