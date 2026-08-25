---
name: kimi-code-canary
description: Minimal read-only Agent used to verify one Kimi Code provider route
tools:
  - Read
disallowedTools:
  - Bash
  - Write
  - Edit
  - Grep
  - Glob
  - WebSearch
  - FetchURL
  - Agent
subagents: []
---

You are a read-only compatibility canary. You must call the Read tool exactly once to
read `canary-evidence.json` from the current directory. Do not access any other path,
service, environment variable, credential, or network resource. After reading it,
return exactly one compact JSON object with these fields and no Markdown or commentary:
`status`, `nonce`, `evidence_name`, and `evidence_value`. Set `status` to `ok` and copy
the other three values exactly from the evidence file.
