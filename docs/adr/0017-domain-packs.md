# ADR-0017: Domain Packs make the engine generic

Status: Accepted | Date: 2026-10-24

vigil was built for one business. Here everything business-specific lives in a Domain Pack directory:
`pack.yaml` (event catalogue, identifiers and which ones link, non-identifying attributes, fraud typologies, actions
and whether each is restrictive, terminology), versioned rules, Personas and Scenarios. The engine, the agents, the
prompts and the API read only the pack's models: verdicts are the pack's typologies plus `Inconclusive` and
`LikelyGenuine`, recommendations are its actions, prompts are filled in with its description and terms.

Two packs ship: `investor_portal` (vigil's domain, with its scenarios and personas) and `digital_banking` (SIM-swap
takeover, mule payees, application fraud). A new business is a new directory, validated at startup: rules may only
name the pack's event types and linking identifiers, labels only its typologies, and a pack must offer at least one
non-restrictive action so a genuine verdict always has somewhere safe to land.

One service instance runs one pack. Several packs side by side would need a pack id on events and Cases, which can
be added without changing the engine.
