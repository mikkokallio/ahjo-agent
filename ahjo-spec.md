Spec: Meeting Preparation Using Ahjo Interfaces
Objective
Investigate the available Ahjo-related APIs and data sources that can support AI-assisted preparation for governance, committee, board, and council meetings.
The goal of this phase is not to build an AI solution.
The goal is to determine:

What data is available.
How the data can be accessed.
What formats are returned.
Whether attachments can be retrieved.
Whether the available data is sufficient for future AI-assisted summarization, briefing, and inconsistency detection.


Phase 0 – API Discovery and Capability Assessment
Primary Questions
Meeting Discovery
Determine whether APIs expose:

Upcoming meetings
Past meetings
Meeting calendars
Meeting identifiers
Meeting dates and times
Governing body / committee information

Example questions:

Can upcoming meetings be listed?
Can meetings be filtered by committee?
Can meetings be filtered by date range?
Is there a stable meeting identifier?


Agenda Items
Determine whether APIs expose:

Agenda items
Item identifiers
Item titles
Item descriptions
Item categories
Processing status
Links between agenda items and decisions

Example questions:

Can a meeting be expanded into agenda items?
Can agenda items be queried independently?
Can agenda items be linked across multiple meetings?


Decisions
Determine whether APIs expose:

Decision proposals
Final decisions
Decision history
Processing history
Vote information
Amendments
Decision timestamps

Example questions:

Can a decision be linked to its originating agenda item?
Can historical versions be retrieved?
Can decision outcomes be compared with proposals?


Attachments (Critical Investigation Area)
This is the highest-priority area.
Determine:

Are attachments exposed at all?
Are attachment URLs available?
Are files downloadable?
What file formats are supported?
Are attachment metadata available?
Are attachment titles available?
Are attachment MIME types available?
Are attachments versioned?
Are attachment sizes available?

Example questions:

Can PDFs be downloaded directly?
Can Word documents be downloaded directly?
Can spreadsheets be downloaded directly?
Are attachments public or authenticated?

Success criterion:
Demonstrate retrieval of at least one complete meeting package including all available attachments.

Related Material
Determine whether APIs expose:

Memorandums
Statements
Supporting material
Preparation material
Processing notes
References to previous decisions

Example questions:

Can a decision reference older decisions?
Can related documents be traversed automatically?


Metadata
Determine availability of:

Authors
Preparers
Organizational units
Departments
Committees
Tags
Categories
Classification identifiers

Example questions:

Can metadata be used for filtering?
Can metadata support future AI prioritization?


API Characterization
For every discovered endpoint document:
Endpoint
Example:
Plain Text1GET /meetingsNäytä lisää rivejä
Document:

URL
Method
Authentication requirements
Parameters
Pagination
Rate limits


Response Schema
Capture:

JSON structure
Nested objects
Arrays
Resource identifiers

Store example payloads.

Attachment Schema
Document:

Download URLs
Filename
MIME type
Size
Identifiers
Relationships


Deliverables
Produce:
API Inventory
Table containing:

Endpoint
Purpose
Authentication
Supports filtering
Supports historical data
Supports attachments


Entity Model
Construct conceptual relationships:
Plain Text1Meeting2 └─ Agenda Item3      ├─ Attachment4      ├─ Proposal5      └─ Decision6 7Decision8 └─ Related Decisions9 10Committee11 └─ MeetingsNäytä lisää rivejä
Actual model must be based on observed API responses, not assumptions.

Future Phases (Do Not Implement Yet)
These are downstream possibilities only.
No implementation work should be done until Phase 0 is completed.

Phase 1 – Meeting Briefing Pipeline
Objective:
Generate concise briefing packages from a selected meeting.
Typical flow:
Plain Text1Meeting2 -> Agenda Items3 -> Attachments4 -> Summaries5 -> Executive BriefNäytä lisää rivejä
No enterprise-wide indexing.
No "index everything" approach.
Focus only on selected meetings.

Phase 2 – Attachment Understanding
Potential Azure services to evaluate:
Azure AI Content Understanding
Preferred candidate.
Evaluate suitability for:

PDF documents
Word documents
Scanned documents
Multi-document extraction
Structured fact extraction
Key point extraction

Investigate:

Accuracy
Cost
Throughput
Attachment size limitations


Azure AI Document Intelligence
Evaluate as alternative/baseline.
Useful for:

OCR
Layout extraction
Tables
Forms


Phase 3 – AI-Assisted Briefing Agent
Potential platforms:
Copilot Studio
Evaluate:

Custom connectors
Agent flows
Workflows
Dataverse integration


Azure AI Foundry
Evaluate:

Agent Service
Prompt orchestration
Tool calling
Observability
Governance


Phase 4 – Strategic Grounding (Limited RAG)
The objective is NOT large-scale document indexing.
Only a small, curated set of relatively stable reference material may be used.
Examples:

Organizational strategy
Governance principles
Budget framework
Operating principles
Long-term plans

Potential use cases:

Detect inconsistencies
Detect strategic conflicts
Detect policy conflicts
Explain alignment with strategic goals

Questions AI should eventually answer:

Does this proposal contradict existing strategy?
Is this proposal aligned with strategic objectives?
Are there unresolved conflicts between proposal and governing documents?


Explicit Non-Goals
Do NOT:

Build a full enterprise RAG solution.
Index every meeting ever held.
Index every attachment in the system.
Design production architecture yet.
Choose a final AI platform yet.
Assume attachment formats or APIs before verification.

The purpose of this phase is discovery, evidence gathering, and capability assessment only.
Tämä speksi ohjaa Copilotin oikeaan ongelmaan: ensin selvitetään mitä rajapinnoista oikeasti saa, erityisesti liitteet, ja vasta sen jälkeen mietitään agentteja, Content Understandingia, AI Searchia tai muuta AI-kerrosta. Liitteet ovat tässä nimenomaan ykkösriski ja ykköstutkimuskohde.