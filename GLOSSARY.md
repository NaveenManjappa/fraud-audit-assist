# Glossary

The domain language. Code, docs and API use these terms; the _Avoid_ lines name words that mean something else here.

**Domain Pack**: A directory that makes the engine about one business: its event catalogue, identifiers, fraud
typologies, actions, terminology, Rule Sets, Personas and Scenarios (ADR-0017). _Avoid_: plugin, tenant, config.

**Account**: The unit that is scored for fraud risk. A pack may call it something else in prompts. _Avoid_: profile, user.

**Customer**: The person who owns an Account (a pack may say Holder). _Avoid_: user, client.

**Event**: One recorded action on an Account at a moment in time, in the canonical shape every source system is
normalised into. Idempotent on `event_id`. _Avoid_: activity, log entry, transaction.

**Rule**: A named pattern worth a fixed number of points when it matches. Kinds: `sequence`, `first_sighting`,
`velocity` (per Account) and `shared_identifier`, `fraud_proximity`, `ring` (graph). _Avoid_: check, detector, signal.

**Rule Set**: One versioned rules file. Its version is stamped on every Alert and Rule Hit. _Avoid_: config version.

**Window**: The longest time a Rule allows across its steps, measured on event time, inclusive. _Avoid_: timeout.

**First Sighting**: An event carrying a value (device, country, ...) no earlier event of its Account carried. An
Account's first event is never one, and several new values on one event are one sighting. _Avoid_: new device (alone).

**Rule Hit**: One Rule matching on an Account, with its evidence event ids (the completing event last), trigger time and
a plain-English explanation. Never rewritten. _Avoid_: match, finding, reason code.

**Risk Episode**: A run of Rule Hits where each triggered at most the risk period after the previous. Each is one
Alert (ADR-0013). _Avoid_: incident, session.

**Score**: The sum of the points of the distinct Rules hit in an Alert. Never decreases. _Avoid_: rating.

**Risk Band**: Low, Medium, High or Critical, by the Rule Set's thresholds. _Avoid_: severity, priority.

**Alert**: Detection's verdict on one Risk Episode: its Rule Hits, Score, Risk Band and the Rules Version that opened
it. Only grows. _Avoid_: flag, case.

**Flagged**: Said of an Account when any of its Alerts reached High. _Avoid_: detected.

**Linking Identifier**: An identifier whose reuse across Accounts suggests one actor (device, email, phone, payout or
payee account), as the pack marks with `linking: true`. IP addresses and postal addresses are masked but never link.

**Link Graph**: The `identifier_links` table: each Account's earliest use of each Linking Identifier (ADR-0015).
_Avoid_: graph database, projection.

**Neighbour**: An Account outside a batch that a new link or a new Confirmed Fraud can affect, re-scored in the same
transaction. **Neighbourhood**: one hop of the Link Graph around an Account, as the Investigator sees it.

**Ring**: At least `account_threshold` Accounts on one identifier, or within `max_hops` shared identifiers.

**Confirmed Fraud**: An Account known to be fraud, from a seed or an Analyst Decision. Recorded once.
_Avoid_: blacklisted, fraud label.

**Case**: The work item an analyst picks up for one Alert, opened the first time it reaches High. Open → Investigating
→ AwaitingApproval → (Deciding) → Closed. One Case per Alert, one Investigation per Case. _Avoid_: ticket, alert.

**Investigation**: The AI work on one Case: the Investigator gathers evidence and writes a Finding, code validates it,
the Reviewer checks it. It only recommends. _Avoid_: agent run, analysis.

**Finding**: The Investigator's conclusion: verdict (a typology, Inconclusive or LikelyGenuine), confidence, summary,
recommendation (a pack action) and cited Evidence Refs. _Avoid_: summary (alone), report.

**Evidence Ref**: A short label for one piece of evidence in one Investigation: `E-n` for an event, `G-n` for a graph
fact. A Finding citing a ref that was never issued is rejected before review. _Avoid_: citation, evidence id.

**Evidence Ledger**: One Investigation's Pseudonyms, Evidence Refs and the exact lines shown to the agents. Lives in the
workflow state and is checkpointed with it. _Avoid_: masker, context.

**Pseudonym**: The stand-in for an identifier before text reaches an LLM, such as `ACCOUNT-1`, numbered per
Investigation. _Avoid_: mask, token, hash.

**Review Flag**: The mark on a Finding the Reviewer rejected even after the one retry. _Avoid_: low confidence.

**InvestigationFailed**: The outcome when the agents could not produce a reviewed or reviewable Finding. The Case still
reaches the analyst.

**Restrictive action**: An action that limits a customer (blocking payouts, holding payments). Needs a fraud verdict.

**Analyst Decision**: A named analyst's ruling: outcome (ConfirmedFraud or FalsePositive), action and notes. Closes the
Case. _Avoid_: approval (alone), label.

**Intervention**: The instruction recorded for the source system to enforce when an action is approved. This service
enforces nothing itself. _Avoid_: block, freeze.

**Audit Entry**: An append-only record of a step after detection: who or what acted, model, Prompt Version, Rules
Version, evidence and tokens. _Avoid_: log line, history.

**Prompt Version**: The prompt file in force (`investigator.v1`), recorded with every Case and Audit Entry.

**Scenario**: One hand-written Account journey with relative timings, in `packs/<pack>/scenarios/`. _Avoid_: test case.

**Persona**: A template of behaviour from which the simulator generates many randomised Accounts. _Avoid_: profile.

**Dataset**: A seeded, reproducible set of generated Accounts with their events and Labels (JSON lines).

**Label**: Ground truth for a generated or golden Account: a typology or Genuine. Never sent to the API.

**Golden case**: One hand-curated case in `evals/golden/` with its expected detection and investigation outcome.

**Near-miss**: A genuine Account that peaked at Medium: no live Case, but worth sampling to test that agents can clear it.

**Known miss**: Fraud the current rules do not flag, kept in the golden set to measure what the rules cannot see.
