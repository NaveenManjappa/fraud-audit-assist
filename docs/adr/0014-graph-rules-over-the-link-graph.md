# ADR-0014: Graph Rules are small Python functions over a Link Graph interface

Status: Accepted | Date: 2026-10-24

Three graph rule kinds read the Link Graph through `LinkGraphReader` (`links_of`, `links_on`, `confirmed_fraud`) and
return candidate Rule Hits to the pure Detector, which places them in Risk Episodes like any other:

| Kind | Matches |
|---|---|
| `shared_identifier` | The Account shares an identifier (for example a payout account) with an Account of a different customer |
| `fraud_proximity` | The Account shares a Linking Identifier with a Confirmed Fraud Account |
| `ring` | At least `account_threshold` Accounts share one identifier (`max_hops: 1`), or are reachable within `max_hops` shared identifiers |

A graph hit's trigger time is when the pattern completed (the latest link it needs); its evidence is the events
behind those links, the completing one last. Rules read the graph as it is now; live ingestion and offline replay
apply events in time order, so a rule never sees a link from the future.

The same rule code runs on the SQL link table (live) and an in-memory graph (dry runs, replay, tests).

## Difference from vigil

vigil's rings were one hop only (a known limit: A–B on a device and B–C on a phone was not a ring). `max_hops` above
1 follows such chains, bounded at 200 explored Accounts so a busy shared identifier cannot explode the search, and
Neighbour re-checks reach as far as the widest ring rule.
