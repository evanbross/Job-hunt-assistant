# JobHuntAssistant — Pipeline Architecture Design

*A learning-oriented decomposition of the current monolithic Flask app into production-style AI agent pipelines.*

---

## Why Think in Pipelines

The current app is a **monolith with background threads**: a single Flask process does everything — scraping, email parsing, AI scoring, cover letter generation, and serving the UI. That's fine for a personal tool, but it doesn't reflect how production systems handle the same class of problem.

Production AI pipelines decompose these into **independent agents** connected by a **message queue or event bus**. Each agent does one thing, produces a well-defined output, and is replaceable without touching the rest of the system. This pattern appears in:

- ML feature pipelines (data ingest → transform → train → serve)
- RAG systems (crawl → chunk → embed → retrieve → generate)
- Content moderation pipelines (ingest → classify → review → act)
- And directly: job matching systems at LinkedIn, Greenhouse, Lever

---

## Current Architecture Map

Here is how the existing code maps to pipeline concepts:

```
┌─────────────────────────────────────────────────────────────┐
│                        Flask Monolith                       │
│                                                             │
│  Sources                  Processing          Outputs       │
│  ──────                   ──────────          ───────       │
│  gmail_client.py          email_parser.py     db.py         │
│  career_site_scanner.py   fit_scorer.py       sheets_client │
│  job_board_searcher.py    duplicate_checker   cover_letter_ │
│  import_listener.py       captured_page_      generator.py  │
│  (WebSocket)              import_service.py                 │
│                                                             │
│  All of these run in the same process, sharing memory and   │
│  calling each other as plain Python function calls.         │
└─────────────────────────────────────────────────────────────┘
```

The threads in `app.py` (`career-site scheduler`, `import_listener`, Vite dev server) are early signs that parts of this app *want* to be separate processes — they already need their own execution context.

---

## Proposed Pipeline Decomposition

Each box below is a **separate service** (its own process, container, or serverless function). They communicate over a message queue (e.g. Redis Streams, RabbitMQ, or AWS SQS), not direct function calls.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           SOURCE AGENTS                                      │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Email Scanner  │  │  Career Site    │  │  Board Search   │             │
│  │                 │  │  Crawler        │  │  Agent          │             │
│  │ gmail_client.py │  │ career_site_    │  │ job_board_      │             │
│  │ scan.py routes  │  │ scanner.py      │  │ searcher.py     │             │
│  │                 │  │                 │  │                 │             │
│  │ Outputs:        │  │ Outputs:        │  │ Outputs:        │             │
│  │  raw_email msg  │  │  raw_job_html   │  │  raw_job_json   │             │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘             │
└───────────┼─────────────────── │ ────────────────────┼──────────────────────┘
            │                    │                      │
            └────────────────────┼──────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────┐
│                       raw_jobs  (queue)                        │
│  Each message: { source, raw_content, url, metadata }          │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         EXTRACTION AGENT                                     │
│                                                                              │
│  email_parser.py + captured_page_import_service.py                          │
│                                                                              │
│  - Receives raw content from queue                                           │
│  - Calls Claude to extract: company, title, url, salary, location           │
│  - Outputs structured JobRecord to next queue                                │
└────────────────────────────┬─────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                   extracted_jobs  (queue)                      │
│  Each message: { company, job_title, url, description, ... }   │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        DEDUP AGENT                                           │
│                                                                              │
│  duplicate_checker.py                                                        │
│                                                                              │
│  - Checks incoming job against DB (exact URL + fuzzy title/company)         │
│  - Drops duplicates or marks as possible_duplicate                          │
│  - Passes unique jobs to next queue                                          │
└────────────────────────────┬─────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                     unique_jobs  (queue)                       │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        FIT SCORING AGENT                                     │
│                                                                              │
│  fit_scorer.py                                                               │
│                                                                              │
│  - Loads candidate profile (from DB or object store)                        │
│  - Calls Claude: score 0-100, reasoning, penalty deductions                 │
│  - Outputs scored job record                                                 │
└────────────────────────────┬─────────────────────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                      scored_jobs  (queue)                      │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                       PERSISTENCE AGENT                                      │
│                                                                              │
│  db.py (repositories)                                                        │
│                                                                              │
│  - Writes job to DB with score, status=pending_triage                       │
│  - Triggers a notification to the UI (SSE / WebSocket push)                 │
└──────────────────────────────────────────────────────────────────────────────┘


            ─────────── Triggered by user action ───────────

┌──────────────────────────────────────────────────────────────────────────────┐
│                     COVER LETTER AGENT                                       │
│                                                                              │
│  cover_letter_generator.py                                                   │
│                                                                              │
│  - Runs on-demand when user approves a job                                  │
│  - Could also run as a low-priority background task for high-fit jobs       │
│  - Supports multi-turn chat refinement (existing fit_chat endpoint)         │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Web Scraper Separation — The Login Problem

Your intuition here is correct: separating the web scraper is specifically how you solve authenticated job boards like LinkedIn.

### Why the Current Approach Can't Log In

The current `career_site_scanner.py` uses plain `requests` HTTP calls. Job boards with login walls (LinkedIn, Indeed, Greenhouse SSO pages) return a login redirect instead of job content. There's no way to handle sessions, cookies, or OAuth flows in a headless HTTP library.

### The Separation Pattern

Run the scraper as a **separate long-lived process with a real browser**:

```
┌───────────────────────────────────────────────────────────────┐
│                  Browser Automation Agent                      │
│                                                               │
│  Technology: Playwright (Python) or Puppeteer (Node)         │
│                                                               │
│  1. Launches a real Chromium/Firefox instance                 │
│  2. Logs in once; persists cookies in a session store        │
│  3. Navigates job board pages                                 │
│  4. Streams discovered job data via WebSocket or queue       │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  Session Manager                                     │    │
│  │  - Stores auth cookies (encrypted at rest)           │    │
│  │  - Detects session expiry, triggers re-login         │    │
│  │  - Supports multiple accounts/job boards             │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
│  Output: Streams raw HTML or structured job JSON to the      │
│          raw_jobs queue consumed by the Extraction Agent     │
└───────────────────────────────────────────────────────────────┘
```

The existing `import_listener.py` WebSocket server is already a receiver for this pattern — the browser agent would be the sender. Today you manually trigger it (paste HTML); a browser agent makes it continuous.

---

## Production Infrastructure Patterns

Here is the technology stack each pipeline component would use in a real production deployment:

### Message Queue Options

| Tool | Best For | Notes |
|------|----------|-------|
| **Redis Streams** | Single-machine or small scale | Simplest to add to existing stack; persistent, ordered |
| **RabbitMQ** | Multi-service, reliable delivery | AMQP protocol; good Python support via `pika` |
| **AWS SQS** | Cloud-native, serverless workers | Pay-per-message; infinite scale; dead-letter queues built in |
| **Apache Kafka** | High-volume, event replay needed | Overkill for this app but standard in large ML pipelines |

For learning purposes, **Redis Streams** is the best starting point — Redis is already commonly used alongside Flask, the Python client is simple, and the mental model (append-only log of messages with consumer groups) is the same as Kafka.

### Orchestration Options

These are frameworks that manage the "run this agent, then that one, retry on failure" logic:

| Tool | Paradigm | Notes |
|------|----------|-------|
| **Celery** | Task queue (Python-native) | Closest to your current architecture; each pipeline step is a task |
| **Prefect** | Workflow orchestration | Python-native; good UI for monitoring flow runs |
| **Temporal** | Durable execution | More complex but handles long-running workflows with retries |
| **AWS Step Functions** | Cloud workflow | JSON-defined state machines; pairs with Lambda workers |
| **LangGraph** | LLM agent graphs | Purpose-built for multi-step LLM pipelines; uses a graph model |

For your use case, **Celery + Redis** is the most direct learning path from where you are today. Celery is essentially "make your existing Python functions run as background workers with queues and retries."

**LangGraph** is worth knowing about specifically for the AI portions — it's the framework behind many production LLM agent pipelines and models the extraction → scoring → generation chain as an explicit graph.

### Deployment Model

```
┌─────────────────────────────────────────────────────────────────┐
│                         Docker Compose                          │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │  Flask   │  │  Redis   │  │  Celery  │  │Playwright│      │
│  │  (API +  │  │ (queue + │  │  Worker  │  │ Scraper  │      │
│  │   UI)    │  │  cache)  │  │ (AI jobs)│  │          │      │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘      │
│                                                                 │
│  All services defined in docker-compose.yml                    │
│  Redis as the shared message bus                               │
│  Celery workers scaled independently of Flask                  │
└─────────────────────────────────────────────────────────────────┘
```

In production (e.g. AWS ECS or Kubernetes), each box is a separate container with independently scalable replicas. The AI scoring workers might need more CPU/memory than the Flask API, so you scale them separately.

---

## Phased Migration Path

The existing app does not need to be rewritten. These steps move it toward the pipeline model incrementally:

### Phase 1 — Add Redis, extract one agent (low risk)
- Add Redis to the stack
- Move `fit_scorer.py` calls to a Celery task
- Flask route enqueues the task instead of calling the function directly
- Frontend polls or uses SSE for completion
- **What you learn**: async task dispatch, Celery beat scheduler, task result storage

### Phase 2 — Browser automation agent
- Add a Playwright-based scraper as a separate service
- It logs in to one job board (LinkedIn is hard; start with Greenhouse or Lever)
- Sends job records to Redis → Celery picks them up for dedup + scoring
- **What you learn**: browser automation, session management, producer/consumer pattern

### Phase 3 — LangGraph extraction pipeline
- Replace the ad-hoc `email_parser.py` + `captured_page_import_service.py` with a LangGraph flow
- Nodes: `fetch_content` → `classify_source` → `extract_fields` → `validate`
- **What you learn**: LLM routing, structured output validation, graph-based orchestration

### Phase 4 — Observability
- Add Prometheus metrics to each worker (jobs processed/sec, LLM latency, queue depth)
- Celery Flower dashboard for task monitoring
- **What you learn**: the operational reality of production pipelines — things fail silently without this

---

## What Would NOT Change

- The Flask API and React frontend remain as-is; they just talk to the queue/DB instead of calling modules directly
- The DB schema and repository pattern are already well-suited for this architecture
- The `ai_provider.py` abstraction carries forward unchanged — it's already the right interface for a scoring agent

---

## Key Papers and Resources

If you want to go deeper on the concepts:

- **"The Bitter Lesson" (Sutton)** — why scale beats clever pipelines; context for why the simple "call Claude" approach often beats elaborate multi-step orchestration
- **LangGraph documentation** — `langchain-ai/langgraph` on GitHub; the "multi-agent" examples map directly to this architecture
- **Celery docs + Redis Streams tutorial** — start with `celery.io` and the Redis Streams intro in Redis docs
- **Playwright Python docs** — `playwright.dev/python`; the `browser_context.storage_state()` API is what you use to persist login sessions

---

*Generated 2026-05-03 as a learning exercise for pipeline architecture patterns.*

---
Would the separate services still launch as one big program with an inherent orchestrator or would it be a collection of containers?                         
                                                                                                                                       
● Both, depending on where you are on the maturity spectrum. It's a sliding scale, not a binary choice.                                                        
                                                            
  In development / personal use: you'd use Docker Compose or a process manager like supervisord or even just a Procfile (Honcho/Foreman). One command starts   
  all the services, they talk over localhost. It feels like one program but each piece is an independent process with its own restart behavior. This is exactly
   what the existing launcher.py is doing in miniature — it's a hand-rolled process orchestrator.                                                              
                                                            
  In production at small-to-medium scale: you'd containerize each service (Docker images) and deploy them to something like AWS ECS, Fly.io, or a single
  Kubernetes cluster. They're still "one system" in the sense that they share a message queue and a database, but now each service scales, restarts, and
  deploys independently. Kubernetes is the orchestrator in this case — it handles health checks, restarts, and scaling.

  In production at large scale: the orchestrator becomes a dedicated workflow engine (Temporal, AWS Step Functions, Prefect). These go further than "keep the
  process running" — they track the state of individual pipeline runs, handle partial failures, retry specific steps, and give you a UI showing that job ID
  12345 got through extraction but stalled at scoring.

  The key distinction is what the orchestrator knows about:
  - Process managers (supervisord, Docker Compose) know about processes — are they alive or dead?
  - Container orchestrators (Kubernetes, ECS) know about containers — healthy, scaling, deployed?
  - Workflow engines (Temporal, Prefect, LangGraph) know about individual work items — did this specific job complete the dedup step?

  For your use case as a learning exercise, Docker Compose is the sweet spot. You'd have a docker-compose.yml with five or six services, docker compose up
  starts everything, and you get the real mental model of independent services without the operational overhead of Kubernetes.


