# Evidence policy

## Search scope

Use free public information by default, including:

- Government records, statistics, court filings, legislation, regulator releases, and official transcripts.
- Primary company disclosures, technical documentation, schedules, filings, and direct operational data.
- Reputable local and international reporting.
- Public academic papers, working papers, research databases, books, and established theoretical frameworks.
- Ordinary web search results that lead to verifiable primary or credible secondary sources.

Do not restrict research to a fixed publisher allowlist. Judge a source in relation to the claim it supports.

## Source evaluation

For each claim, evaluate proximity to the underlying event, methodology, incentives, track record, date, independence, and corroboration. Prefer primary records for facts and rigorous research for causal or base-rate claims. Treat rumors, engagement bait, anonymous claims, circular citations, and unverified screenshots as weak evidence.

## Public-figure statements

Treat important-person statements as evidence, not facts. Assess:

- Whether the statement is formal, prepared, and representative of official policy.
- Whether the speaker controls or materially influences the outcome.
- The speaker's historical follow-through in comparable situations.
- Personal, family, political, financial, or reputational conflicts of interest.
- Whether independent actions or sources corroborate the statement.

A leader with a careful record of speaking for an institution may deserve substantial weight. A figure whose statements mix negotiation, attention-seeking, policy signals, and personal interests may deserve little weight unless behavior and independent evidence confirm the relevant interpretation.

## Required evidence record

Record every key evidence item with:

1. `source_name` and `source_type`.
2. `title` and direct `url`.
3. `published_at` and `retrieved_at`.
4. A concise factual `summary` of the source content.
5. The research `conclusion` drawn from it.
6. The exact causal `factor_id` whose probability it informs.
7. A `credibility` explanation specific to this use.

For a current position, also record `new_since_previous`. When true, state the affected factor, the child-probability change, and the propagated final-probability change.

Do not cite a search-result snippet when the underlying page is available. Do not invent publication dates or links. Separate what the source says from what the analysis infers.

## Contradictions

When credible sources conflict:

1. Check whether they address the same date, definition, population, and settlement criterion.
2. Prefer sources closer to the underlying fact and explain methodological differences.
3. Model genuinely distinct possibilities as scenario branches rather than averaging incompatible claims without explanation.
4. Search for later corrections and primary documentation.
