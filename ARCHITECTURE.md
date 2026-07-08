# Ahjo Agent Architecture Plan

## Decision: 1 Agent + LLM-Augmented Tools

1 agent with smart tools is the right architecture for the current scope. The document processing pipeline is deterministic (not agentic) and belongs as a tool that internally calls the LLM for chunk summarization when needed. Multi-agent only becomes justified when security boundaries, parallelization across full meetings, or specialized document types require it.

---

## Scenario

"Tee yhteenveto esityslistan kohdasta X siten, etta otat huomioon kaikki asiaan liittyvat liitteet. Huomioi vastauksessa esityksen mahdolliset ristiriidat ja kytkokset kaupungin strategiaan."

Requirements:
- Executive summary in complete sentences (not bullet lists)
- Consider all attachments for the item
- Detect contradictions within and across attachments
- Strategy grounding is optional (user requests it)
- Only use item materials; clearly mark any additions
- Progressive output: don't leave user waiting without feedback

---

## Why NOT Multiple Agents (Yet)

The scenario has three parts:

1. **Meeting/agenda lookup** - purely deterministic API calls
2. **Attachment processing** - deterministic pipeline (download PDF, extract text, assess size, chunk/bundle, LLM summarize)
3. **Final synthesis** - actual agent reasoning (contradictions, strategy, formatting)

Only part 3 requires agentic behavior. Parts 1-2 follow a fixed sequence every time. Separate agents would add A2A protocol latency and extra model inference without benefit.

### The "tool with internal LLM" is NOT an agent

**Agent** = maintains conversation state, autonomously decides which tools to call, has its own instructions it reasons about.

**Tool with internal LLM** = follows a deterministic pipeline that uses LLM for a specific sub-task (chunk summarization). Always does the same thing in the same order.

The document processor should be a `@tool`-decorated Python function because:
- It doesn't decide what to do - the pipeline is fixed
- It doesn't maintain conversation state
- It doesn't choose between tools
- Its LLM usage is utility-level ("summarize this chunk"), not reasoning-level

### When to Split (Roadmap Triggers)

| Trigger | Justification |
|---------|--------------|
| Authenticated/non-public attachments | Different security boundary - separate privileged agent |
| Full-meeting batch processing | Parallel workers processing all items simultaneously |
| Specialized document types (scanned, maps, spreadsheets) | Agent-level routing for extraction strategy |
| Cross-meeting conflict detection | Analytical agent comparing across historical decisions |

---

## Architecture

```
User
  |
  v
+----------------------------------------------+
|  Ahjo Briefing Agent (MAF Hosted)            |
|  Model: gpt-5.4 (128K context)               |
|                                               |
|  Tools:                                       |
|  +-- get_meetings(days_ahead)                 |
|  +-- get_meeting_agenda(policymaker_code)     |
|  +-- get_item_attachments(case_url)           |
|  +-- read_attachment(case_url, att_num?)      |  <-- LLM-augmented
|  +-- search_strategy(query)                   |  <-- optional grounding
+----------------------------------------------+
```

---

## Tool: read_attachment

**Input**: case_url + optional attachment number(s) (or "all published")
**Output**: Structured content with per-attachment summaries and extracted key facts

### Internal Pipeline

1. Fetch case page -> get PDF URLs for published attachments
2. Download each PDF via httpx (skip unpublished, acknowledge them)
3. Extract text with pdfplumber
4. Assess and group:
   - **Small** (<~1500 tokens): candidates for bundling
   - **Medium** (~1500-4000 tokens): return full text directly
   - **Large** (>4000 tokens): chunk by pages, LLM-summarize each chunk
5. **Bundling**: Group small attachments together into a single LLM call (up to ~4000 tokens per bundle), with clear separators (`--- Attachment 3: [name] ---`). Reduces total LLM calls and amortizes system prompt overhead.
6. **Chunking**: For large documents, split by pages/sections, summarize each chunk separately.
7. Return structured result: `{attachments: [{name, status, content_or_summary, page_count, token_estimate}]}`

### Bundling vs Chunking

| Strategy | When | Benefit |
|----------|------|---------|
| **Bundling** | Multiple small attachments (<1500 tokens each) | Fewer LLM calls, amortize system prompt, model sees related context together |
| **Pass-through** | Medium attachments (1500-4000 tokens) | No LLM call needed - return full text for the agent to reason about directly |
| **Chunking** | Large attachments (>4000 tokens) | Avoid lost-in-the-middle, fit within working memory |

Sweet spot per LLM call: ~3000-4000 tokens of content. Short enough to avoid lost-in-the-middle, long enough to be efficient.

Bundled attachments get clear delimiters so the model knows they're separate documents:
```
=== Attachment 1: Neuvottelukutsu Helen 24.6.2026 ===
[content]

=== Attachment 3: Tasmennyspyynto, Helen v30.10.2025 ===
[content]
```

### LLM Calls Inside the Tool

Use same model endpoint (gpt-5.4) with focused system prompt:
"Summarize these document sections concisely. Preserve all factual claims, numbers, dates, named entities, and obligations. Note any internal contradictions. Mark which attachment each fact comes from."

---

## Progressive UX

Agent calls `read_attachment` per-attachment (or per small group), reporting progress between calls. Agent instructions guide it to say "Processing attachments..." between tool calls. This addresses the requirement of not leaving the user waiting without output.

Alternative: single call that returns all attachment data, but this risks long wait times for meetings with many attachments.

Decision: Agent calls `read_attachment` once with all desired attachment numbers. The tool itself handles bundling/chunking internally and returns a complete result. For very large sets (>5 published attachments), the agent can call the tool in batches and report intermediate progress.

---

## Implementation Phases

### Phase 1: Core Tool

1. Verify/add pdfplumber to requirements.txt
2. Implement `read_attachment` tool in main.py:
   - PDF download and text extraction
   - Token estimation
   - Bundling logic for small attachments
   - Chunking logic for large attachments
   - Internal LLM calls for summarization
3. Update agent INSTRUCTIONS
4. Register tool, deploy, test

### Phase 2: Progressive Output

5. Agent instructions for progress reporting
6. Batch mode for meetings with many attachments

### Phase 3: Synthesis Quality

7. Contradiction detection instructions (compare claims across attachments + main proposal)
8. Strategy grounding gating (only when user requests)
9. Output format guidance (executive summary, complete sentences, sources cited by attachment number)
10. Scope discipline (only item materials, mark additions)

### Phase 4: Edge Cases & Resilience

11. Non-machine-readable PDFs (report status, future: Azure AI Document Intelligence for OCR)
12. Very large attachments (>50 pages): hierarchical summarization
13. All-unpublished scenario: summarize from proposal page text only
14. Rate limiting / timeout handling
15. Memory monitoring (increase from 1Gi if needed)

---

## Constraints & Considerations

- **Token budget**: 50K TPM quota. Processing 5 attachments with internal summarization uses ~10-20K tokens. Concurrent users will hit limits quickly.
- **Memory**: 1Gi RAM likely sufficient for typical municipal PDFs (<3MB). Monitor.
- **Attachment reality**: 80-90% are "not published online" for sensitive items. Tool acknowledges but cannot access.
- **Unpublished attachments**: Roadmap item for authenticated access (separate privileged agent when needed).
- **PDF library**: pdfplumber as primary (handles 98%+ of Ahjo text-native PDFs). PyMuPDF as fallback candidate if scanned documents become common.
- **Bundling threshold**: ~1500 tokens per document = "small". Bundle up to ~4000 tokens total per LLM call. These are tunable parameters.
- **Response time target**: First partial output <20s, full response <90s for typical items (3-5 attachments).

---

## Relevant Files

- `src/ahjo-meetings/main.py` - Add read_attachment tool
- `src/ahjo-meetings/requirements.txt` - Verify/add pdfplumber
- `src/ahjo-meetings/agent.yaml` - May need memory increase for large PDFs
- `ahjo-spec.md` - Phase 1 (briefing pipeline) and Phase 2 (attachment understanding) alignment

---

## Verification Checklist

- [ ] read_attachment works against known published PDFs (e.g., Helen Tasmennyspyynto)
- [ ] Bundling triggers correctly for multiple small attachments
- [ ] Chunking triggers correctly for large documents
- [ ] E2E: "Tee yhteenveto kohdan 5 esityksesta ja sen liitteista"
- [ ] Agent skips unpublished attachments (acknowledges them)
- [ ] Strategy grounding only activates when requested
- [ ] Response times within target
- [ ] No lost-in-the-middle degradation for bundled content
