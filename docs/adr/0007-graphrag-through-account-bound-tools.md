# ADR-0007: The Investigator reaches evidence through three tools bound to the Case's Account

Status: Accepted | Date: 2026-10-24

The brief gives the Investigator the Alert: Score, Risk Band and Rule Hits, each citing the refs of the events
behind it. Everything else it fetches with three tools:

| Tool | Returns |
|---|---|
| `get_account_timeline(days)` | The Account's events in the last `days` days (1 to 90, at most 200 shown) |
| `query_graph_neighbourhood()` | Each Linking Identifier and the other Accounts on it, marked when Confirmed Fraud |
| `get_customer_baseline()` | Usual countries, devices, channels and activity, from before the flagged activity began |

The tools take no account argument: they are built per Investigation around the Case's Account, so the model cannot
be talked into reading another customer's data. Every result is written through the Evidence Ledger (ADR-0008). This
is the GraphRAG step: relational facts retrieved from the Link Graph, with no vector database, because fraud
evidence is relational rather than semantic text.

Every read is bounded by the Case's `as_of` time, so the agents never see an event from after the Case.
