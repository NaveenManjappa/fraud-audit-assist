# ADR-0012: A human approves every rule change; the service never tunes itself

Status: Accepted | Date: 2026-10-24

Analyst Decisions are recorded with the Finding's recommendation beside them, and every Alert and Rule Hit records
its Rules Version. That is the raw material for a rule-tuning report, which is future work. Whatever form it takes,
it only suggests: a person writes a new rules file, compares it on the golden dataset and the synthetic Datasets, and
deploys it. A weight that looks noisy may still carry fraud over the line, and in a regulated firm a model-risk owner
must be able to say who made the trade-off and on what evidence.
