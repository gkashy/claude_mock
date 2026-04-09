# Hybrid RAG + Knowledge Graph for Hyper-Personalisation
## A Pattern Guide for Multi-Agent Apps (e.g. Omnio)

---

## What We Built in Agent-Scope (The Reference Implementation)

Agent-Scope is a local AI chat agent with persistent memory. It had flat vector search already working -- facts extracted from conversations, embedded into Qdrant, retrieved by similarity. The problem: flat memory can store facts in isolation but cannot traverse dependencies between them automatically.

We added a **knowledge graph as an enrichment layer on top of vector search**. The two systems never compete -- they run together on every single message:

```
User message
    → embed query
    → Qdrant top-k facts           (vector: finds what's SIMILAR)
    → extract entity names from retrieved facts
    → graph neighbor lookup        (graph: finds what's CONNECTED)
    → merge facts + graph edges
    → inject enriched context into system prompt
    → LLM responds
```

The graph doesn't replace retrieval. It expands the retrieved context by one hop. One vector hit on a fact mentioning "Gaurav Kashyap" triggered a traversal that pulled 46 connected nodes -- projects, technologies, employers, goals, constraints -- all surfaced automatically without the agent calling any extra tools.

**Result:** The agent answered "What's my full technical stack across all projects?" purely from system prompt context, with no tool call, because the graph enrichment had already surfaced the complete picture.

---

## How This Applies to Omnio

Omnio has 30+ tools across workout, nutrition, and memory agents. The challenge with that many tools and a personalised fitness domain is that **user context is deeply relational**, not just keyword-similar. Consider:

- A user's injury history affects their workout plan
- Their dietary restrictions constrain their meal recommendations
- Their current goal (cut vs bulk) changes what both agents should recommend
- Their schedule (travel this week, no gym access) affects tool routing

Flat RAG finds facts that are semantically close to the current query. The graph finds facts that are **structurally connected** to what was retrieved -- even if those connected facts would never surface via similarity alone.

---

## The Omnio Entity Schema

```
Node types:
  User         → the person (one per account)
  Goal         → fitness targets (lose 10kg, run 5k, build shoulders)
  Constraint   → limitations (knee injury, lactose intolerant, no gluten, travels Mon-Wed)
  Food         → specific foods, meals, or cuisines the user likes/dislikes
  Exercise     → specific exercises (Romanian deadlift, incline press)
  WorkoutPlan  → a named routine (PPL, 5x5, HIIT circuit)
  MealPlan     → a named diet approach (IIFYM, 16:8, Mediterranean)
  Metric       → tracked numbers (weight, body fat %, PR on bench)
  Preference   → subjective likes/dislikes (hates cardio, prefers morning workouts)
  Equipment    → what they have access to (home gym, barbell only, resistance bands)
```

```
Edge types (relations):
  User --has_goal-->          Goal
  User --has_constraint-->    Constraint
  User --follows-->           WorkoutPlan / MealPlan
  User --prefers-->           Exercise / Food / Preference
  User --dislikes-->          Exercise / Food
  User --achieved-->          Metric  (e.g. hit 100kg squat)
  User --has_access_to-->     Equipment
  Goal --requires-->          Exercise / Food / MealPlan
  Goal --conflicts_with-->    Constraint
  Exercise --aggravates-->    Constraint  (e.g. squats aggravate knee)
  Exercise --requires-->      Equipment
  Food --conflicts_with-->    Constraint  (e.g. dairy conflicts_with lactose intolerant)
  WorkoutPlan --contains-->   Exercise
  MealPlan --excludes-->      Food
  Metric --tracks_progress_toward--> Goal
```

---

## What the Graph Unlocks for Each Agent

### Workout Agent

**Without graph:** User asks "what should I do for legs today?" → retrieves past workout facts → recommends leg exercises.

**With graph:**
```
retrieve "legs workout" facts
    → found entity: User
    → graph traversal:
        User --has_constraint--> Knee injury (left ACL, avoid deep flexion)
        User --has_goal--> Increase quad strength
        User --dislikes--> Leg press machine
        User --has_access_to--> Barbell, rack, cables
        Squat --aggravates--> Knee injury
    → LLM now recommends: Romanian deadlifts, leg extensions, hack squats
      instead of barbell squats -- without the user mentioning their knee
```

The agent didn't just find "leg workout" facts. It found the constraints and equipment attached to that user. The recommendation is safe and personalised without re-asking.

### Nutrition Agent

**Without graph:** User asks "what should I eat for lunch?" → retrieves past meal facts → generic suggestions.

**With graph:**
```
retrieve "lunch meal" facts
    → found entity: User
    → graph traversal:
        User --has_constraint--> Lactose intolerant
        User --has_constraint--> Gluten sensitivity
        User --has_goal--> Caloric surplus (bulking)
        User --prefers--> High protein meals
        User --dislikes--> Meal prep containers (eats out usually)
    → LLM recommends: rice bowl with grilled chicken, no cheese, gluten-free options
      and frames it as a caloric surplus meal automatically
```

### Memory Agent / Tool Routing

With 30+ tools, the graph helps the memory agent decide **which tools to activate** based on what's structurally relevant to the user right now:

```
User says: "I'm travelling to Austin this week"
    → extract triple: User --travelling_to--> Austin (event)
    → this connects to:
        User --has_constraint--> No gym access while travelling
        User --has_goal--> Maintain workout consistency
    → memory agent proactively flags: activate hotel/bodyweight workout tools
      AND nutrition tools for eating out
      before the user even asks
```

---

## Implementation Blueprint for Omnio

### Step 1: Schema (two tables per user DB)

```python
# kg_entities: id, name, name_lower, entity_type, properties (JSON),
#              user_id, first_seen, last_seen, active

# kg_relationships: id, source_entity_id, target_entity_id, relation,
#                   properties (JSON), user_id, created_at, active
```

Dedup key for entities: `(name_lower, user_id)` -- cross-type. "knee injury" mentioned in 10 sessions is still one node, even if the LLM labels it as "constraint" in one session and "event" in another. First-seen type wins. This prevents the LLM's inconsistent type assignments from creating duplicate nodes.

### Step 2: Extraction (piggyback on your existing memory extraction)

You already run a memory extraction pass after conversations. Extend the LLM prompt to also output structured triples in the same JSON response:

```json
{
  "facts": ["User prefers morning workouts before 8am"],
  "triples": [
    {
      "subject": "Alex", "subject_type": "person",
      "relation": "prefers",
      "object": "morning workouts", "object_type": "preference"
    },
    {
      "subject": "Alex", "subject_type": "person",
      "relation": "has_constraint",
      "object": "available before 8am only", "object_type": "constraint"
    }
  ]
}
```

No extra LLM call. Same extraction pass, extended output schema.

### Step 3: Retrieval enrichment (the hot path -- microseconds)

```python
async def retrieve_with_graph_enrichment(user_id, query, top_k=10):
    # Step 1: vector search (existing)
    facts = await vector_search(user_id, query, top_k=top_k)

    # Step 2: extract entity names from retrieved facts (regex, no LLM)
    entity_names = extract_entities_from_text(facts, user_id=user_id)

    # Step 3: graph neighbor lookup (in-memory NetworkX, microseconds)
    graph_lines = []
    for name in entity_names:
        for neighbor in get_neighbors(name, user_id=user_id):
            graph_lines.append(
                f"[Graph] {neighbor['from']} --{neighbor['relation']}--> {neighbor['to']}"
            )

    # Step 4: merge within token budget and return
    return facts + graph_lines
```

This runs before every agent turn. The graph is a NetworkX DiGraph loaded in memory at startup from Postgres -- lookups are O(degree), no DB round-trip on the hot path.

### Step 4: Multi-agent routing benefit

Each of your 30+ tools can register which entity types it cares about. The graph gives you a structured way to decide which tools are relevant before the LLM even sees the message:

```python
TOOL_ENTITY_AFFINITY = {
    "workout_planner":   ["goal", "constraint", "exercise", "equipment"],
    "meal_recommender":  ["goal", "constraint", "food", "mealplan"],
    "progress_tracker":  ["metric", "goal"],
    "injury_advisor":    ["constraint"],
}

def relevant_tools_for_user(user_id, retrieved_entities):
    active_entity_types = {
        get_entity_type(e, user_id) for e in retrieved_entities
    }
    return [
        tool for tool, affinities in TOOL_ENTITY_AFFINITY.items()
        if active_entity_types & set(affinities)
    ]
```

Instead of passing all 30+ tool definitions to every agent turn (token cost), you pass only the tools whose entity types appear in the user's graph context.

---

## The Core Bet Worth Making

With flat RAG, personalisation is: *"I remember you mentioned X."*

With a memory graph, personalisation is: *"I understand that your goal conflicts with your constraint, your injury rules out certain exercises, and your schedule this week changes what's feasible -- here's what I recommend."*

The graph doesn't replace memory. It makes memory **traversable**. Facts that were stored independently across 20 different conversations become a connected picture that the agent reads as a whole every single turn.

The incremental write cost per conversation is negligible -- a few extra rows in two tables. The retrieval benefit compounds with every session as the graph grows denser.

---

## User Identity Anchoring

A critical lesson: the system prompt must include the user's canonical full name, and the extraction prompt must be told to always use it. Without this:

- The LLM uses "Gaurav" in one triple and "Gaurav Kashyap" in another -- creating two separate person nodes
- The LLM writes `subject="user"` or `subject="the user"` -- creating a generic orphan node
- "Who am I?" returns nothing useful because the agent has no baked-in identity context

### The fix (three places)

**1. Config:** Add a `USER_NAME` setting (env var or config file). For Omnio, this would come from the user's account profile.

**2. System prompt:** Inject at the top: *"You are speaking with **{USER_NAME}**. Always use this exact name when referring to the user."* This means the agent knows who it's talking to even with zero memory facts.

**3. Extraction prompt:** Add a `## Primary user` section: *"The primary user's canonical full name is: **{USER_NAME}**. Always use this exact name in triples."* This eliminates partial-name and pronoun variations at the source.

For Omnio, `USER_NAME` maps directly to the user's profile display name. The extraction prompt should also list any known aliases (nicknames, usernames) so the LLM can map them to the canonical name.

---

## Graph Hygiene: Pitfalls We Hit

These are real bugs we encountered in production that would affect any knowledge graph built from LLM-extracted triples. Each one silently degrades graph quality if not addressed.

### 1. Emoji contamination in entity names

**Problem:** The LLM titled an artifact "Marketing Jobs for Nandini :dart:" and that emoji ended up as part of the graph node name. Emojis break regex matching, display poorly in visualizations, and create dedup misses (the same entity with and without emoji becomes two nodes).

**Fix:** Strip all Unicode emoji from entity names before they hit the database. Apply this in `add_triple()` so it's impossible for an emoji to enter the graph regardless of source.

```python
import re
_EMOJI_RE = re.compile("[" + ranges + "]+", flags=re.UNICODE)

def _strip_emojis(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()
```

### 2. Partial-name duplication

**Problem:** The LLM extracted `subject="Gaurav"` in some triples and `subject="Gaurav Kashyap"` in others. The dedup key `(name_lower, entity_type, user_id)` treated these as distinct entities, creating two separate person nodes with different edge sets.

**Fix:** Before upserting, check if the incoming person name is a word-prefix of an already-known person name (or vice versa). If so, canonicalize to the longer name. Only applies to `entity_type="person"` to avoid false positives.

```python
def _resolve_person_name(name, entity_type, user_id):
    if entity_type != "person":
        return name
    # scan known person nodes for prefix match
    # if "gaurav" is a prefix of "gaurav kashyap", return "Gaurav Kashyap"
```

Also provide a `POST /api/graph/merge-duplicates` admin endpoint to retroactively merge existing partial-name duplicates by re-pointing all their relationships to the canonical entity.

### 3. Disconnected subgraphs (island problem)

**Problem:** When a third-party person (e.g. "Nandini") appeared in conversation, the LLM extracted triples within Nandini's context (Nandini --looking_for--> Marketing Jobs) but never created a bridge triple connecting the primary user to Nandini. Result: a floating island in the graph with no path to the main cluster.

**Fix:** Add an explicit rule to the extraction prompt:

> *When a third-party person appears, always extract at least one triple connecting the primary user to them (e.g. knows, is_roommate_of, works_with, helped).*

This ensures every person node is reachable from the primary user's hub.

### 4. Generic "user" node from artifact registration

**Problem:** When artifacts were registered in the graph, the code hardcoded `obj="user"` instead of resolving the actual person name. This created a floating "user" node disconnected from "Gaurav Kashyap".

**Fix:** Resolve the real person name dynamically from the graph by finding the most-connected person entity for that user_id. Fall back to user_id only if no person entity exists yet.

### For Omnio

All four of these apply directly. Fitness data is especially prone to name variations ("bench press" vs "bench" vs "flat bench press"), so consider extending the prefix-dedup logic beyond just person entities to exercise and food entities as well, using a domain-specific synonym table.

---

## The Append-Only Problem and Entity Resolution

### The problem

The extraction LLM produces triples blind to existing graph state. It sees existing **facts** (with IDs, so it can UPDATE/DELETE them) but sees **zero** existing triples. The graph is append-only -- triples are added but never updated or removed.

This causes three layers of duplication that silently degrade graph quality:

| Layer | Example | Why it happens |
|---|---|---|
| Same entity, different type | "Lead Engineer" as person AND as goal | LLM assigns different types across sessions; old dedup key included entity_type |
| Same concept, different name | "Marketing" vs "Marketing job search in Canada" vs "marketing roles in Canada" | LLM paraphrases freely; no existing entity names visible to guide it |
| Same relationship, different verb | `has_boyfriend` vs `is_dating` for the same pair | LLM picks a different verb each session; no existing edges visible |

A real example from production: Nandini had 10 outgoing edges when she should have had 3-4. Three separate nodes for marketing-related goals, four overlapping nodes for anxiety-related events, and two edges to Archit (`has_boyfriend` + `is_dating`).

### Why deterministic fixes don't work alone

- **Fuzzy string matching** catches "Marketing job search in Canada" ~ "marketing roles in Canada" (token overlap), but would also merge "React" with "React Native" (false positive). And it misses "panic attacks" ~ "job search anxiety" (zero overlap, same concept).
- **Embedding similarity** catches semantic near-misses but is threshold-dependent -- too tight misses real dupes, too loose merges distinct concepts.
- **Relation synonym maps** work for verbs (small vocabulary, unambiguous) but can't handle entity names (open vocabulary, context-dependent).

No single deterministic method is both safe and complete.

### The solution: retrieval + LLM reranking

The pattern that works: use deterministic methods as **candidate finders** (high recall, some noise), then let the LLM make the **final decision** (high precision). The matching methods never decide -- they only surface possibilities. The LLM judges.

```
Phase 1: EXTRACT       (existing LLM call, unchanged)
    Raw triples from conversation

Phase 2: ENTITY RESOLUTION
    For each entity in the raw triples:
        - Exact match: check name_lower index
        - Fuzzy match: Jaccard token overlap against known entity names
        - Vector match: embed entity name, search Qdrant entities collection
        - Union all candidates
        - If candidates found -> LLM call: "Is X the same as any of [A, B, C]?"
        - LLM returns: matching name or NULL
    Map raw entity names to resolved canonical names

Phase 3: RELATION NORMALIZATION
    Apply static synonym map (deterministic, safe for small vocabularies):
        is_dating -> has_partner
        holds_role -> has_role
        targets -> has_goal
        pursues -> has_goal
        ...

Phase 4: EDGE RESOLUTION
    For each resolved triple:
        - Query existing edges between the same entity pair (simple ID lookup)
        - If existing edges found -> LLM call:
            "New: Gaurav --ex_girlfriend--> Kashish
             Existing: [rel-7a] --has_girlfriend-->
             SUPERSEDE, COEXIST, or SKIP?"
        - SUPERSEDE: delete old edge, create new (ex replaces girlfriend)
        - COEXIST: add alongside (works_at + leads_team_at are both valid)
        - SKIP: discard, already captured

Phase 5: PERSIST
    Execute resolved actions: ADD / SUPERSEDE / SKIP
```

### Why each matching method matters

Each method catches what the others miss:

| Method | Catches | Misses |
|---|---|---|
| Exact match | "Marketing" = "Marketing" | Any variation |
| Fuzzy match (token overlap) | "Marketing job search in Canada" ~ "marketing roles in Canada" | "panic attacks" ~ "job search anxiety" |
| Vector match (embedding similarity) | "panic attacks" ~ "job search anxiety" (semantically close) | Could over-match "React" ~ "React Native" |

Together they surface a small candidate set (2-5 per entity). The LLM looks at those candidates in context and rejects false positives. This gives you high recall (three methods cover each other's blind spots) with high precision (LLM is the final judge).

### Edge resolution: why it matters

Without edge resolution, the graph accumulates contradictory information. "I broke up with Kashish" would produce a new `ex_girlfriend` triple, but the old `has_girlfriend` edge would persist. Both would be true in the graph simultaneously.

Edge resolution is cheap: after entity resolution, you know the exact entity IDs. Querying existing edges between a pair is a simple graph lookup -- no fuzzy matching or embedding needed. The LLM call is tiny (the new triple + 1-3 existing edges = ~100 tokens).

### Cost of the resolution pipeline

| Step | Cost per extraction |
|---|---|
| Entity embeddings | ~5 OpenAI embed calls (~$0.0001) |
| Entity Qdrant search | Negligible (in-memory) |
| Entity LLM resolution | ~200 input / ~50 output tokens (~$0.001) |
| Edge LLM resolution | ~150 input / ~30 output tokens (~$0.001) |
| **Total** | **~$0.002 per extraction cycle** |

Two tiny LLM calls and a few embedding calls. Negligible compared to the extraction call itself (~$0.02-0.05).

### For Omnio

This pattern is critical for a fitness app. Users describe the same exercise in multiple ways ("bench press", "flat bench", "barbell bench press"), change goals over time ("cutting" to "bulking"), and update constraints ("knee is feeling better, cleared by physio"). Without entity resolution, the graph fills with near-duplicate exercise, food, and goal nodes. Without edge resolution, outdated constraints persist alongside current ones, and the agent can't distinguish which is current.

The entity embedding collection can also double as a domain vocabulary: embed all standard exercise names, food items, and constraint types at setup. New user-mentioned entities get resolved against this vocabulary automatically.

---

## Artifacts as Graph Nodes

When the agent creates a persistent output -- an HTML presentation, a code file, a document -- that output gets registered as an `artifact` entity node in the knowledge graph at creation time. This means agent-created outputs are not buried in a specific session; they become discoverable graph nodes that can be found in any future session.

### How it works

```
Agent creates artifact (e.g. "10-Day Netflix Movie Night Plan")
    → artifact saved to DB with id, title, filename, content, session_id
    → upsert_entity(name=title, entity_type="artifact", properties={id, filename, content_type})
    → edge: User --created--> artifact node

Later session: "pull up the movie night presentation"
    → find_artifact tool called with query "movie night presentation"
    → DB search splits query into keywords ["movie", "night", "presentation"]
    → OR match against title + filename across all sessions
    → returns artifact id, title, session
    → get_artifact_content fetches and displays it
    → artifact also served at GET /api/artifacts/{id}/render for browser viewing
```

No session ID required. The user never has to remember when or where they created something -- the keyword search finds it and the graph connects it to the context that produced it.

### Why this matters for Omnio

Every agent-generated output in a fitness app is a candidate for this pattern:

- A generated weekly meal plan → `artifact` node, discoverable as "pull up the meal plan from last month"
- A custom workout schedule → retrievable across sessions without regenerating
- A progress report → linkable to the `metric` and `goal` nodes it was built from
- A grocery list → connected to the meal plan that produced it

Without this, every session starts with the agent having no knowledge that it previously created these outputs. With artifact nodes, the agent can find, display, and build on its own prior work.

### Entity connections for artifacts

```
artifact node properties: {id, filename, content_type, session_id, version}

edges:
  User --created-->      Artifact
  Artifact --produced_in--> Session  (optional, for tracing)
  Artifact --based_on-->  Goal / Metric / MealPlan  (Omnio: connect output to what drove it)
```

The `find_artifact` search uses keyword OR logic -- any word in the query matches against title or filename. This is intentionally broad: users rarely remember exact artifact titles.

---

## Graph Update Cadence and Scaling

### How the graph actually gets updated

The graph is **eventually consistent**, not real-time. Triples are extracted in the same LLM pass that extracts flat facts, so the graph only updates when extraction runs -- not on every message.

There are two triggers:

```
Turn-based:  every N user turns (default: 10)
             IF cooldown has expired (default: 5 min since last extraction)
             → fires as a background task, doesn't block the response

Disconnect:  when the WebSocket closes (user leaves or navigates away)
             IF cooldown has expired
             → fires as a background task
```

That's it. In a typical 15-minute conversation you get **2-3 extraction passes total**. New entities and relationships from the current conversation are invisible to graph traversal until extraction fires. Flat facts from Qdrant carry the context in the interim.

### What one extraction pass costs

Each extraction fires one LLM call with:
- Full session transcript (each message truncated to 500 chars)
- ALL existing facts for the user across ALL sessions

The LLM returns `actions` (ADD/UPDATE/DELETE facts) and `triples` (graph edges). Then for each action:
- 1 embedding API call + 1 Qdrant search for semantic dedup
- 1-2 Postgres writes

And for each triple:
- 3 Postgres writes (upsert subject entity, object entity, relationship)
- O(1) in-memory NetworkX update

The LLM call dominates everything. Input token cost grows linearly with your total fact count.

| Facts stored | Approx input tokens per extraction | Cost (Claude Sonnet) |
|---|---|---|
| 50 facts | ~3,000 tokens | ~$0.01 |
| 200 facts | ~8,000 tokens | ~$0.02 |
| 500 facts | ~15,000 tokens | ~$0.05 |
| 2,000 facts | ~50,000 tokens | ~$0.20 |

Every 10 turns at the default settings means for a power user with 500 facts, you're spending ~$0.05 per extraction and ~$0.50 per 100-turn session. Manageable, but worth knowing.

### Tuning the levers

Three constants control the cadence (all in `app.py`):

```python
_EXTRACTION_COOLDOWN_SECS = 300   # 5 min between extractions per session
_MID_SESSION_EXTRACT_EVERY = 10   # extract every N turns
```

Recommended settings for richer/faster graph population:

```python
_EXTRACTION_COOLDOWN_SECS = 120   # 2 min cooldown
_MID_SESSION_EXTRACT_EVERY = 5    # every 5 turns
```

This gives ~4-5 extractions in a 15-minute conversation instead of 2-3. Cost doubles, but for most usage patterns it's negligible.

### Manual triggers

Force extraction for a specific session without waiting for the schedule:
```
POST /api/sessions/{session_id}/extract
```

Force a full graph rebuild from Postgres (re-runs Leiden clustering):
```
POST /api/graph/rebuild
```

Merge partial-name duplicates (e.g. "Alex" and "Alex Johnson" → one node):
```
POST /api/graph/merge-duplicates
```

A good workflow during development: chat for a while, hit the extract endpoint manually, then hit rebuild to see the updated visualization with fresh community clusters.

### The scaling problem as facts grow

The core issue: `extract_facts_from_session` calls `load_facts(user_id)` which loads ALL facts from Postgres and stuffs them into the LLM prompt. This is necessary for diff-aware ADD/UPDATE/DELETE, but it means prompt size grows linearly with your total memory.

**Smarter approach as you scale: pre-filter using two axes**

Instead of sending all N facts, select a relevant subset:

```python
# Axis 1: semantic similarity (catches direct contradictions)
similar_facts = await qdrant_search(embed(transcript), top_k=30)

# Axis 2: entity overlap (catches indirect contradictions)
entities_in_transcript = extract_entities_from_text(transcript)
entity_facts = [f for f in all_facts if any(e in f["content"] for e in entities_in_transcript)]

# Union: send the combined set, capped at ~50 facts
facts_to_send = deduplicate(similar_facts + entity_facts)[:50]
```

Why two axes:
- Semantic similarity alone misses indirect contradictions: "I had a steak dinner" won't semantically match "User is vegetarian" even though it clearly contradicts it
- Entity overlap catches this: both mention the same person/topic, so they get included regardless of embedding distance

This keeps the prompt fixed-size regardless of whether you have 100 or 10,000 facts. The only edge case it misses is a contradiction where neither the topic nor any named entity overlaps with existing facts -- genuinely rare.

### Two-tier extraction (the advanced pattern)

For near-real-time graph updates without full extraction cost, split into two passes:

**Lightweight pass (per-turn, cheap):**
- Send only the last 1-2 messages to the LLM
- Ask only for `triples` (no fact diff)
- No existing facts needed in the prompt
- Fires after every turn, ~200 input tokens, negligible cost
- Updates the graph in near-real-time

**Full pass (on disconnect or every N turns, expensive):**
- Full transcript + filtered facts
- Produces both `actions` (ADD/UPDATE/DELETE) and `triples`
- Catches indirect contradictions, keeps flat facts accurate
- Current implementation handles this

The lightweight pass keeps the graph live during conversation. The full pass keeps flat facts accurate at the end. Together they give you real-time graph traversal without the cost of full extraction on every turn.

---

## Graph-Aware Extraction: Full CRUD on Graph Edges

### The asymmetry we discovered

The extraction LLM had full CRUD on facts -- it saw every existing fact with its ID and could ADD, UPDATE, or DELETE them. But it was **completely blind to graph edges**. This created a critical asymmetry:

| User says | Fact layer | Graph layer |
|-----------|-----------|-------------|
| "I like chicken" | Fact stored | Edge added: `likes --> chicken` |
| "I'm allergic to chicken" | Fact DELETED (LLM sees it, removes it) | Edge **stays** (LLM can't see it, can't remove it) |

The same problem appeared with negations ("Archit is NOT a serial killer" -- the fact was deleted but `classified_as --> serial killer` persisted in the graph) and cascading corrections ("allergic to chicken" should also invalidate `prefers --> chicken biryani`, but the graph had no mechanism for this).

The entity resolver we built handles *contradicting positive triples* (e.g., `ex_girlfriend` supersedes `has_girlfriend`). But pure negations and cascading corrections produce no positive triple for the resolver to work with. The resolver is a deduplication safety net, not a correction mechanism.

### The fix: give the extraction LLM visibility into both stores

The solution mirrors the existing fact CRUD pattern. The extraction prompt already shows all existing facts with IDs so the LLM can reference them for UPDATE/DELETE. We now also show **relevant graph edges with relationship IDs**.

The extraction prompt gains two additions:

1. An `## Existing graph edges` section showing edges relevant to the conversation:
```
- [rel-a1] Gaurav Kashyap --likes--> chicken
- [rel-b2] Gaurav Kashyap --prefers--> chicken biryani
- [rel-c3] Gaurav Kashyap --orders_frequently--> chicken tikka
```

2. A `triple_deletions` output key where the LLM can reference edge IDs for removal:
```json
{"triple_deletions": [
  {"id": "rel-a1", "reason": "user is allergic to chicken"},
  {"id": "rel-b2", "reason": "allergic to chicken products"},
  {"id": "rel-c3", "reason": "allergic to chicken products"}
]}
```

One LLM call, full visibility into both stores, full CRUD on both. The LLM handles cascading for facts AND graph edges because it can see both.

### How relevant edges are retrieved (not "show all")

Showing every graph edge to the LLM would waste tokens and dilute attention. Instead, we use retrieval-based filtering -- the same infrastructure built for entity resolution:

1. **Exact match**: `extract_entities_from_text()` scans the transcript and existing fact texts for known entity names using word-boundary regex.
2. **Fuzzy + vector expansion**: `find_candidates()` (from the entity resolver) expands the entity set using token overlap and Qdrant embedding similarity. This catches related entities not literally mentioned -- "chicken" expands to "chicken biryani" and "chicken tikka."
3. **Neighbor lookup**: `get_neighbors()` pulls all edges (both directions) for every matched entity, deduplicated by relationship ID.

The result is a focused subset of 5-20 relevant edges, not the entire graph. This scales to any graph size.

### Processing flow

```
extract_facts_from_session()
  |
  +--> _retrieve_relevant_edges(transcript, facts, user_id)
  |       Uses exact + fuzzy + vector to find relevant entities
  |       Pulls edges with relationship IDs via get_neighbors
  |
  +--> LLM call (prompt includes facts + graph edges + transcript)
  |
  +--> Parse response: actions, triples, triple_deletions
  |
  +--> Fact CRUD (existing: ADD/UPDATE/DELETE)
  +--> Triple ADD via resolver pipeline (existing: entity resolve -> edge resolve -> persist)
  +--> Triple DELETIONS: deactivate_relationship() + remove_edge_by_id()
```

### What this solves

- **Pure negation** ("Archit is NOT a serial killer"): LLM sees the `classified_as --> serial killer` edge and emits a deletion. Edge removed.
- **Cascading correction** ("allergic to chicken"): LLM sees all chicken-related edges, understands the implication, deletes them all. No individual fact-to-edge matching needed.
- **Supersession** ("broke up with X"): LLM can delete `has_girlfriend --> X` and the resolver adds `ex_girlfriend --> X` from the new triple.
- **Stale edges with no matching fact**: Even if the corresponding fact was already deleted by the agent mid-conversation, the graph edge is visible in the prompt and gets cleaned up.

### Omnio implications

For a fitness app, this pattern is essential. Users frequently change dietary preferences, get injuries that invalidate exercise routines, or update goals. "I'm now vegetarian" should cascade to invalidate all meat-preference edges. "I injured my knee" should flag edges about leg exercises. The extraction LLM handles this naturally when it can see both the fact store and the graph -- no special cascading logic needed.

---

## EKGLMM: The SDK That Packages All of This

All of the patterns described in this document -- the graph manager, entity resolution pipeline, graph-aware extraction with `triple_deletions`, enriched semantic recall, and pluggable backends -- have been extracted from Agent-Scope into a standalone Python SDK called **EKGLMM** (Efficient Knowledge Graph and Long-term Memory Manager).

Built on [graphify (v3)](https://github.com/safishamsi/graphify/tree/v3) for graph visualization, Leiden community clustering, and NetworkX graph infrastructure. EKGLMM adds the memory management, LLM-powered extraction, entity/edge resolution, and graph-aware CRUD layer on top.

Soundar does not need to build any of the graph or memory infrastructure from scratch. He installs EKGLMM, wires in his database URLs and API keys, and gets everything.

---

### Install

```bash
pip install ekglmm[all]
```

This installs EKGLMM with all default backends: PostgreSQL (asyncpg + SQLAlchemy), Qdrant, OpenAI embeddings, and Anthropic LLM.

Or selectively:

```bash
pip install ekglmm                # core + graphify only
pip install ekglmm[postgres]      # + asyncpg + sqlalchemy
pip install ekglmm[qdrant]        # + qdrant-client
pip install ekglmm[openai]        # + openai
pip install ekglmm[anthropic]     # + anthropic
```

---

### Omnio Setup (5 steps)

#### Step 1: Wire up the SDK

```python
from ekglmm import EKGLMM
from ekglmm.storage import PostgresStorage
from ekglmm.vectors import QdrantVectors
from ekglmm.embeddings import OpenAIEmbeddings
from ekglmm.llm import AnthropicLLM

mem = EKGLMM(
    storage=PostgresStorage("postgresql+asyncpg://user:pass@localhost/omnio"),
    vectors=QdrantVectors("localhost", 6333),
    embeddings=OpenAIEmbeddings(api_key="sk-..."),
    llm=AnthropicLLM(api_key="sk-ant-..."),
    user_name=None,                   # set per-user from profile at init time
    entity_types=[
        "person", "exercise", "food",
        "goal", "injury", "metric",
        "preference", "equipment", "mealplan", "workoutplan",
    ],
    relation_synonyms={
        "does": "performs",
        "eats": "consumes",
        "struggles_with": "has_constraint",
        "hurt": "has_injury",
    },
)
```

#### Step 2: Initialize on app startup (once per user)

```python
@app.on_event("startup")
async def startup():
    await mem.init(user_id="default")
    # For multi-tenant: call mem.init(user_id=...) per user on first login
```

`init()` creates the two Postgres tables (`kg_entities`, `kg_relationships`) and the two Qdrant collections (`facts`, `entities`) if they don't exist, then loads the full graph into memory.

#### Step 3: Store facts and let it auto-extract triples

```python
# On user onboarding / profile setup
await mem.remember("Priya is allergic to peanuts", user_id="priya")
await mem.remember("Priya's goal is to lose 5kg by June", user_id="priya")
await mem.remember("Priya has a left knee injury, cleared for low-impact only", user_id="priya")
```

Each `remember()` call:
- Deduplicates against existing facts (semantic similarity)
- Persists to Postgres + Qdrant
- Runs a mini-extraction to produce graph triples automatically

So after the three calls above, the graph already has:
```
Priya --allergic_to--> peanuts
Priya --has_goal--> lose 5kg by June
Priya --has_injury--> left knee injury
```

#### Step 4: Recall context before every agent turn

```python
async def build_system_context(user_id: str, user_message: str) -> str:
    results = await mem.recall(user_message, user_id=user_id, top_k=10)
    context_lines = [r.content for r in results]
    return "\n".join(context_lines)
```

`recall()` runs vector search on facts + graph neighbor expansion. For "what should I eat for lunch?", it returns:
```
[vector] Priya is allergic to peanuts
[vector] Priya's goal is to lose 5kg by June
[graph]  Priya --has_goal--> lose 5kg by June
[graph]  Priya --allergic_to--> peanuts
[graph]  Priya --has_constraint--> no peanut products
```

Inject this into your system prompt before calling the LLM.

#### Step 5: Extract memory from conversations

```python
# At end of coaching session (or every N turns as a background task)
applied = await mem.extract(
    messages=conversation_history,
    user_id="priya",
    session_id="session-abc",
)
# applied = ["ADD: Priya started doing Pilates", "TRIPLES(2): graph updated",
#            "DELETE(fact-x1): user said she stopped running", "TRIPLE_DELETIONS(1): edges removed"]
```

`extract()` runs the full pipeline described in this document:
- Diff-aware fact CRUD (ADD/UPDATE/DELETE) with graph-aware visibility
- Entity resolution (exact + fuzzy + vector + LLM) to avoid duplicate nodes
- Edge resolution (SUPERSEDE/COEXIST/SKIP) for contradicting relationships
- `triple_deletions` for negations and cascading corrections

---

### The 5-Method API

That is the entire public interface Soundar needs to know:

| Method | What it does |
|---|---|
| `await mem.init(user_id)` | One-time startup: tables, collections, graph load |
| `await mem.remember(content, user_id)` | Store a fact + auto-extract triples |
| `await mem.recall(query, user_id)` | Semantic search + graph enrichment |
| `await mem.extract(messages, user_id)` | Full extraction from a conversation |
| `await mem.forget(fact_id)` | Delete a fact from storage + vectors |
| `mem.graph` | Direct graph access (neighbors, stats, visualize, cluster) |

No NetworkX knowledge required. No Qdrant internals. No understanding of the resolution pipeline. graphify handles the graph engine; EKGLMM handles everything else.

---

### Graph access (admin + debugging)

```python
# First-degree neighbors -- used for enrichment and debugging
neighbors = mem.graph.neighbors("Priya", user_id="priya")

# Stats: node count, edge count, top entities by degree
stats = mem.graph.stats(user_id="priya")

# Interactive HTML visualization (graphify-powered, Leiden clustering)
html = mem.graph.visualize(user_id="priya")   # serve this in an admin endpoint

# Leiden community detection -- group related nodes
communities = mem.graph.cluster(user_id="priya")

# Raw JSON export (node-link format, for custom visualizations)
graph_json = mem.graph.export_json(user_id="priya")
```

---

### What Soundar still builds himself

EKGLMM is the memory brain. Soundar builds the nervous system around it:

| Soundar builds | EKGLMM handles |
|---|---|
| Chat API (FastAPI / WebSocket) | Fact storage + semantic dedup |
| Auth + user profiles (Apple ID, Google) | Vector search + graph enrichment |
| Mapping auth tokens to `user_id` | Entity resolution pipeline |
| Raw message history (show last 10 messages) | Graph-aware extraction + CRUD |
| The LLM call that generates coach responses | Relation normalization |
| System prompt assembly (inject `recall()` results) | Edge lifecycle (SUPERSEDE/COEXIST/SKIP) |
| Scheduling when to call `mem.extract()` | Knowledge graph (NetworkX + graphify) |
| Push notifications, scheduling, Apple HealthKit | Clustering + visualization |
| Fitness domain logic (calorie math, exercise programming) | Triple deletion and cascading corrections |

The scheduling question -- when to call `extract()` -- is important. The pattern from Agent-Scope that works well: every 10 user turns if 5 minutes have passed since the last extraction, plus on session end. As a background task so it doesn't block responses. See the **Graph Update Cadence** section above for the full breakdown.

---

### Configuring for Omnio's fitness domain

Two things to set at init time:

**entity_types** -- tells the extraction prompt what node types to produce. Use the Omnio schema from this document:
```python
entity_types=["person", "exercise", "food", "goal", "injury",
              "metric", "preference", "equipment", "mealplan", "workoutplan"]
```

**relation_synonyms** -- deterministic verb normalization. Add fitness-specific synonyms so "does", "performs", and "trains" all map to the same edge type:
```python
relation_synonyms={
    "does": "performs",
    "trains": "performs",
    "eats": "consumes",
    "avoids": "dislikes",
    "hurt": "has_injury",
    "struggles_with": "has_constraint",
    "cleared_for": "can_perform",
}
```

**Seed the entity Qdrant collection with domain vocabulary** (optional but recommended):
```python
# At app startup, embed standard fitness terms so entity resolution
# can match "bench press" against "flat bench press" via vector similarity
standard_exercises = ["barbell squat", "bench press", "romanian deadlift", ...]
standard_foods = ["chicken breast", "brown rice", "whey protein", ...]
for term in standard_exercises + standard_foods:
    emb = await mem._embeddings.embed_text(term)
    await mem._vectors.upsert_entity(generate_id(term), emb, {
        "user_id": "vocabulary", "name": term, "entity_type": "exercise",
    })
```

This turns entity resolution into a vocabulary lookup: when a user says "flat bench" the resolver finds "bench press" via embedding similarity and maps to the canonical name automatically.

---

### Package structure (for reference)

```
ekglmm/
  ekglmm/
    __init__.py          # EKGLMM class -- the 5-method API
    _types.py            # Dataclasses: Entity, Relationship, Fact, Triple, RecallResult
    protocols.py         # Protocol interfaces: StorageBackend, VectorStore, EmbeddingProvider, LLMProvider
    graph.py             # GraphManager (instance-based, no globals) -- wraps NetworkX + graphify
    resolver.py          # Entity + edge resolution pipeline
    extraction.py        # LLM extraction with graph-aware CRUD (triple_deletions)
    enrichment.py        # Graph-enriched semantic recall
    storage/
      _models.py         # SQLAlchemy ORM models (FactModel, EntityModel, RelationshipModel)
      postgres.py        # PostgresStorage -- StorageBackend implementation
    vectors/
      qdrant.py          # QdrantVectors -- VectorStore implementation
    embeddings/
      openai_embed.py    # OpenAIEmbeddings -- text-embedding-3-small
    llm/
      anthropic_llm.py   # AnthropicLLM -- Claude text completion
      openai_compat.py   # OpenAICompatLLM -- OpenAI / Groq / Together / local
```

**Key design decisions vs Agent-Scope:**
- **No global state**: Agent-Scope uses module-level globals (`_graph`, `_entity_index`). EKGLMM puts all state inside the `EKGLMM` instance -- multiple instances can coexist for different users or different databases.
- **`remember()` triggers extraction**: In Agent-Scope, `remember_fact` bypasses the graph entirely. EKGLMM's `remember()` stores the fact AND runs a mini-extraction to produce graph triples. This closes the gap.
- **Protocols, not base classes**: Backends are defined as Python `Protocol` classes (structural typing). Soundar can plug in any storage system that matches the method signatures -- no inheritance required.

---

## Reference Implementation

The full working implementation lives in [Agent-Scope](https://github.com/GauravKashyap/Agent-Scope):

**Core graph module:**
- `knowledge_graph.py` -- Graph init, `add_triple` (with emoji strip + person name resolution), `get_neighbors` (with `rel_id` for graph-aware extraction), `get_edges_between`, `extract_entities_from_text`, `get_primary_person_name`, graphify clustering and HTML export

**Entity resolution:**
- `entity_resolver.py` -- `find_candidates` (exact + fuzzy + vector), `resolve_entities` (batched LLM entity dedup), `resolve_edges` (existing edge lookup + LLM SUPERSEDE/COEXIST/SKIP), `resolve_and_persist` (full pipeline entry point), `RELATION_SYNONYMS` map

**Memory layer:**
- `memory.py` -- `Entity` + `Relationship` ORM models, `upsert_entity` (cross-type dedup), `upsert_relationship`, `deactivate_relationship`, `load_graph_data`, enriched `retrieve_relevant_facts`, enriched `recall_facts_smart`, extended `extract_facts_from_session` with triple output + canonical user name + entity/edge resolution + graph-aware extraction (retrieves relevant edges, supports `triple_deletions`), `_retrieve_relevant_edges` (exact + fuzzy + vector entity scan for edge context), `search_artifacts` with keyword OR matching

**Artifact tools:**
- `tools/artifacts.py` -- `create_artifact`, `update_artifact`, `get_artifact_content`, `find_artifact` (cross-session discovery), `_register_artifact_in_graph` (registers artifacts as graph nodes using resolved person name)

**Config and identity:**
- `config.py` -- `USER_NAME` setting for canonical identity anchoring
- `prompts/system.md` -- `{{USER_IDENTITY}}` block injected at top with canonical name

**Admin and backfill:**
- `backfill_graph.py` -- One-time script to extract triples from all existing flat facts and register all existing artifacts as graph nodes (uses canonical user name)

**API endpoints (app.py):**
- `GET /api/graph/stats` -- node/edge counts, entity types, top entities by degree
- `GET /api/graph/entities` -- all entity nodes
- `GET /api/graph/neighbors/{name}` -- first-degree neighbors of a named entity
- `GET /api/graph/visualize` -- interactive HTML visualization via graphify
- `GET /api/graph/export` -- node-link JSON export
- `POST /api/graph/rebuild` -- force full graph reload from Postgres
- `POST /api/graph/merge-duplicates` -- detect and merge partial-name person duplicates
- `GET /api/artifacts/{id}/render` -- serve HTML artifacts directly in browser

[graphify (v3)](https://github.com/safishamsi/graphify/tree/v3) is used for Leiden community clustering and interactive HTML visualization. NetworkX handles the in-memory graph. Postgres persists entities and relationships.

**For Omnio:** Rather than adapting these files individually, use the **EKGLMM SDK** (see the section above). EKGLMM packages all of these modules -- `knowledge_graph.py`, `entity_resolver.py`, the graph-aware extraction in `memory.py`, and the enriched recall -- into a clean, installable Python library with pluggable backends and no global state. The `entity_types` and `relation_synonyms` parameters at init time replace the need to edit extraction prompts or relation maps directly. The graph management, entity resolution pipeline, and graph-aware `triple_deletions` are entity-type agnostic and require no changes for the fitness domain -- only the configuration parameters differ.
