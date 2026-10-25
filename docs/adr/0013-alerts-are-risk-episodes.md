# ADR-0013: Alerts are Risk Episodes with a never-decreasing Score

Status: Accepted | Date: 2026-10-24

An Account gets one Alert per Risk Episode. A Rule Hit joins the episode of the previous hit if it triggered at most
the risk period (7 days) later, measured on event time, never the wall clock, so replaying 90 days in seconds gives
the same Alerts as live ingestion. A longer gap opens a new Alert. Each Rule counts once per episode, so the Score is
the sum of distinct Rules and never decreases: an Account cannot silently drop out of High.

Stored Alerts are only extended. Detection takes the Account's stored Alerts and returns them first, unchanged,
followed by anything new; a late hit for a Rule already in the episode is dropped, and a late hit just before a stored
episode joins it. The evidence behind an Alert an analyst has seen never changes.
