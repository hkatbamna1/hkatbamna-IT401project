# Encore

**Application:** Encore

**Student:** Hardik Katbamna

**Course and assignment:** IT 401 — A5: Expanding the Web Intelligence Application with Intelligence Generation

**Branch:** `hkatbamna-it401-a5`

**Submission report:** [Encore A5 PDF](docs/Encore-A5.pdf)

## Project Overview

Encore helps concertgoers discover upcoming concerts through JamBase, revisit performances through Setlist.fm, and keep a private library of concert searches. Its intended users are listeners comparing artists, venues, cities, and dates before planning a night out or revisiting a previous show.

A4 makes those stored discoveries searchable. Users can search their own saved titles, notes, and concert facts, combine filters, rank or sort results, and move between result pages. This avoids repeating an external concert search just to find information already saved.

A5 adds a recommendation capability: compare up to 30 eligible concerts from one private saved search and generate up to three explained choices. It was selected because a long list still leaves listeners to compare dates and venues themselves. The shortlist applies their stated preferences, shows supporting facts, and explains missing information. Artist discovery and musical similarity are outside the current scope because saved listings do not establish those relationships.

The application continues to use Flask, Flask-SQLAlchemy, SQLAlchemy models, PyMySQL, and the existing CI Keys **MariaDB 11.4.13** service. MariaDB is the MySQL-compatible database supplied by this account. SQLite is used only for isolated tests and documentation previews; the application does not silently fall back to it when MariaDB is unavailable.

The main interface has two areas:

- **Discover:** Search JamBase and Setlist.fm by artist, venue, and city. Provider, date, and sorting controls refine the results loaded so far. Load more to expand that result set; the page does not claim to rank every unrequested provider page.
- **My library:** Search only your private saved searches and their concerts. Open **View saved results** to search, filter, sort, and paginate all concerts stored in that particular snapshot. Rename searches, edit notes, or delete saved records.

The former separate collection interface is merged into My library. Bookmarked `/search?...` URLs redirect to `/profile?...`; library access requires Google sign-in.

## A5 Intelligence Generation

The A5 branch adds **Recommend concerts from these saved results**. Candidate retrieval, output validation, private generation storage, versioned prompts, the Gemini client, orchestration, and the browser interface are implemented. Existing search and CRUD features continue to work. Opening pages does not call Gemini; generation requires an explicit form submission.

### Use recommendations in the browser

1. Sign in and open **My library**. The **Find your next night out** recommendation panel is directly below the page heading.
2. Choose a source under **Recommend from**. This lists all your saved searches, independently of library pagination. Optionally expand **Narrow these concerts** to set a concert keyword, provider, city, dates, or sort order. These controls are separate from the library search below; library title/notes queries are not automatically sent into concert retrieval.
3. Optionally enter preferences, then click **Recommend concerts**. The button disables and announces progress while the request runs. The form also works without JavaScript.
4. A successful request opens a private saved shortlist containing up to three concert cards, source facts, reasons, listing links, collection times, and limitations. Expand **View structured response** or **Generation details** to inspect the JSON and metadata.
5. Reopen results through **Shortlist history** on My library or through a saved shortlist. History supports owner-scoped pagination and filtering to one saved search. Reading history and refreshing a saved result make no new AI request. **Refine & recommend again** returns to My library with the original source and concert controls restored. Saved-results pages link to the library panel with their source preselected.

Authentication and CSRF checks protect generation. The library submits to `POST /profile/recommendations`; its selected source is checked against the signed-in owner. One-time form tokens bind to that source on the first accepted submission, and a thread-safe, bounded local guard rejects repeated/overlapping submissions. Successful POSTs redirect to a read-only result URL. Duplicate successful submissions reuse that result URL; a consumed token cannot switch to another source. Returning to a submitted form through browser Back refreshes a consumed cached form before reuse. The guard expires after 15 minutes and holds at most 512 entries with no prompt/output data. It is process-local: a future deployment with multiple workers needs shared claim storage. A server restart expires old forms; reloading issues a new token.

Generation errors preserve the chosen source, concert controls, preferences, and library results. Quota limits, timeouts, empty context, invalid output, and missing configuration have clear feedback on My library. If saving cannot be confirmed, validated recommendations remain visible there for that response without a claimed saved ID. There is no automatic retry or paid fallback. Text is HTML-escaped; listing URLs come only from sanitized source snapshots. Historical results are validated at their original cutoff date so passing concert dates do not erase saved history.

The candidate pool is now up to 30 concerts, with up to three recommendations. Step 8 includes live evaluation at the 30-candidate limit; earlier ten-candidate timing checks remain documented separately. Existing saved shortlists retain their original source counts and prompt versions.

### First-version behavior

- Work within one saved search belonging to the signed-in listener. Ownership is enforced in the retrieval service and authenticated routes.
- Use the recommendation form's keyword, provider, location, inclusive date bounds, and sorting. Optional preference text (at most 1,000 characters) guides generation separately from keyword retrieval.
- Consider **up to 30 matching eligible concerts**, selected in the existing sort order, independently of the current 12-card display page. Eligibility is applied in SQL before the limit. Return the total eligible matching count and whether the candidate set was truncated so the interface can explain its scope.
- Require a known concert date on or after the cutoff date. Exclude known cancelled/canceled, postponed, and performed statuses, including schema.org event-status forms. Unknown status alone does not exclude a dated concert; it does not establish ticket availability.
- Use the UTC calendar date by default, with an explicit date override for a future application timezone policy and deterministic tests. Same-day concerts remain eligible because calendar dates alone cannot establish whether a performance has started.
- Return **up to three recommendations**, allowing fewer or zero when the evidence or preferences do not support a useful shortlist. An empty shortlist must include a limitation explaining why. Generation runs only through the explicit backend service; opening existing pages does not generate recommendations.

### Candidate sources and response contract

`services/recommendations.py` retrieves only private `SavedConcert` records from the selected owner's saved search. It makes no provider calls, writes no records, and does not use the shared catalog or other saved searches. Candidate facts include available title, artist, venue, location, concert date, tour, genre, and status. Generic genre labels such as "Live music" and "Past show" are omitted.

Each candidate carries a batch-local reference (`C01` through `C30`), provider/external identity, source URL, retrieval timestamp, and a separate copy of its selected facts. References must always be used with their original candidate batch. This preserves evidence independently of later saved-concert index rebuilds. Private notes, account identity, images, and ticket links are excluded from the candidate context.

`services/validators.py` defines a provider-independent JSON Schema and validates either strict JSON text or a decoded object. An illustrative response shape is:

```json
{
  "recommendations": [
    {
      "source_ref": "C01",
      "rank": 1,
      "explanation": "This option matches your selected venue.",
      "supporting_facts": [
        {"field": "venue", "value": "The Wiltern"}
      ]
    }
  ],
  "limitations": ["Saved facts do not establish current ticket availability."]
}
```

This is a contract example, not a generated result or a real concert listing. The venue value is valid only if it exactly matches the referenced candidate in the supplied batch.

Validation rejects missing/unexpected fields, invalid types, blank/oversized text, duplicate JSON keys, nonstandard JSON numbers, unknown/duplicate source references, nonconsecutive ranks, and more than three recommendations. Each recommendation requires one to five distinct supporting facts whose fields and values match its own source record exactly. The validator rechecks date eligibility and known ineligible statuses. It rejects the whole response if any item fails and returns a detached object on success.

Structured evidence checks do not prove that free-text explanations are accurate or that they satisfy every preference. The interface renders explanations as escaped plain text and concert identity/date/link fields from source snapshots. Step 8 evaluates explicit selection rules and separately reviews explanations against the recorded facts; this small synthetic sample does not establish general recommendation accuracy.

### Private generation storage

`RecommendationGeneration` stores validated JSON output, user preferences, retrieval options (without display pagination), the full source-context snapshot, prompt version, returned model version, and a UTC generation timestamp. Ownership follows its saved search's account, avoiding contradictory duplicated owner IDs. Deleting the saved search removes its generated history; the new foreign key also specifies `ON DELETE CASCADE`. Saved-concert refreshes do not overwrite generation evidence.

`services/generation_store.py` rechecks ownership and validates output before staging a record. It also provides owner-scoped reads. The caller must commit or roll back; storage helpers never call Gemini. They accept original candidate batches, not client-authored context. The additive `a5_recommendation_generations` migration creates one table and index without rewriting existing records. Run `flask --app app db upgrade` for a database still at A4. No existing data needs backfilling.

### Gemini client and prompt design

`services/ai_service.py` uses the official [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/) with the Gemini Developer API, passing the configured key explicitly. The default model is `gemini-3.5-flash-lite`, selected after successful full-context live checks; it uses its minimal provider thinking default. Explicit `gemini-3.8-flash` overrides retain low thinking, and other model IDs use their provider defaults. Change `AI_SERVICE_MODEL` to another supported Gemini text model with structured output. The [model catalog](https://ai.google.dev/gemini-api/docs/models) documents current availability. `AI_SERVICE_TIMEOUT_SECONDS` defaults to 20 (allowed range 1–60); `AI_SERVICE_MAX_OUTPUT_TOKENS` defaults to 4,096 (allowed range 256–8,192). Retries are disabled so one generation action makes one model request. No external tools or browsing are enabled. The HTTP client is closed after each call. The request omits the unsupported Gemini 3 `candidate_count` option and checks that the provider returns exactly one response.

`services/prompts.py` builds prompt version `concert-recommendations-v3` with three distinct components:

- **System instructions:** compare only supplied facts; treat user preferences and record strings as untrusted data; reject embedded attempts to override instructions; explain unsupported preferences; acknowledge stale availability and any truncated candidate set in plain language; describe shows as listed/scheduled rather than implying ticket availability.
- **User prompt:** JSON-encoded preferences followed by separately labeled JSON retrieved context. The context contains the cutoff date, candidate/matching counts, truncation flag, source references, selected facts, and retrieval times. Account identifiers, saved notes, provider IDs, source URLs, and credentials stay out of the model prompt.
- **Output format:** a copy of the response JSON Schema is passed through the SDK's `response_json_schema` with `application/json`. Instructions require unique source references, ordered ranks, and exact supporting facts. Local validation remains mandatory after the provider returns JSON.

The client returns raw text and the provider's model version, falling back to the requested model identifier if the provider omits it. It rejects incomplete, blocked, empty, oversized, or non-text responses and reports safe error codes for missing keys, invalid configuration, rate limiting, authentication, unavailable models, timeouts, and connection/provider failures. It never includes provider error bodies in its public errors. The orchestration service connects this client to retrieval, validation, and persistence, and authenticated routes render its outcomes in the browser.

### Generation workflow and failure handling

`services/intelligence.py` exposes `generate_recommendations(saved_id=..., user_id=..., values=..., preferences=...)` within a Flask application context. The account ID must come from server-side authentication. `values` contains existing search/filter/sort controls; display pagination is ignored. The workflow is:

```text
Validate input -> retrieve up to 30 owner-scoped eligible concerts
-> close retrieval transaction -> build versioned context/prompt
-> make one Gemini request -> validate output against the exact source batch
-> recheck ownership and output -> commit private generation history
```

```mermaid
flowchart TD
    U[Listener selects a saved search and enters preferences] --> R[Retrieve up to 30 eligible owner-scoped records]
    R --> C[Build context and versioned system and user prompts]
    C --> G[One Gemini request for structured JSON]
    G --> V[Validate schema, source references, ranks and supporting facts]
    V --> S[Recheck ownership and store result with metadata]
    S --> D[Display up to three explained concert choices]
    S --> H[Reopen private history without another model request]
```

Context makes the comparison specific to concerts actually retrieved from the listener's saved search. Counts and timestamps establish the comparison's scope and age. Excluding images, notes, account identity, URLs and provider identities reduces irrelevant input and avoids sending private organizational data that is unnecessary for the decision. Preferences are sent as user-entered text, so listeners should include only what is relevant to their concert choices.

Retrieval and storage use dedicated SQLAlchemy sessions. The database connection used for retrieval is released before waiting on Gemini. The storage ownership check locks the parent saved-search row until commit on MariaDB, preventing concurrent ownership changes or deletion during the write. The generation commit and rollback do not save or discard unrelated pending edits in the caller's Flask session. No concert-provider acquisition, automatic retry, model fallback, or billing change occurs.

The service returns a `GenerationOutcome` with a stable `code` and safe `message`. Only locally validated output can appear in `output`; `has_output` indicates its presence and `saved` indicates a confirmed stored ID. Successful results also contain the copied source context, prompt/model versions, UTC completion time, and generation ID. Stored source facts supply concert names, dates, venues, and links for the interface.

| Outcome | Behavior |
| --- | --- |
| `generated` | Validated result and original source context committed; return its saved ID. A valid empty shortlist with limitations can also be stored. |
| `invalid_request`, `not_found`, `database_unavailable`, `empty_context` | Input/retrieval failures stop before creating an AI client or consuming API quota; return no generated output. Missing and foreign saved searches share the same `not_found` response. A later ownership change can also produce `not_found` at the storage boundary. |
| `missing_key`, `configuration`, `rate_limited`, `timeout`, `authentication`, `model_unavailable`, `unavailable`, `invalid_response` | Return a clear message, with no automatic retry and no failed-generation history row. No provider body, credential, or raw prompt is exposed. |
| `invalid_output` | Reject unverifiable output entirely; neither return nor store it. |
| `storage_failed` | Preserve validated output and its source facts for this response, but provide no confirmed history ID. The message says storage could not be confirmed: a connection can fail after the server committed. Do not automatically regenerate or claim the result was saved. |

If the saved search is deleted or its ownership changes during the model call, the storage ownership recheck returns `not_found` without exposing generated output or source context. Database failures are logged by exception class only. Existing search results remain ordinary search results, not substitute AI recommendations. Routes keep them available when generation fails and display the service's outcome message; history reads stored results without calling Gemini.

A live workflow check on November 14, 2026 used ten synthetic concerts and an isolated in-memory SQLite database. The service retrieved the candidates, called Gemini 3.5 Flash-Lite, validated three recommendations, and committed matching output/source snapshots in 2.24 seconds. No hosted records were accessed or changed during that check.

Development verification on November 14, 2026: the A5 migration was applied to the configured MariaDB database, existing table row counts remained unchanged, and the bounded candidate query worked against MariaDB. Initial `gemini-3.8-flash` generations timed out through both SDK and direct HTTP despite successful authenticated metadata calls. A controlled same-key/endpoint/prompt comparison found 3.8 still timed out at 15 seconds while 3.6 Flash and 3.5 Flash-Lite completed in about one second. With the complete recommendation prompt, JSON schema, and ten synthetic candidates, 3.6 returned three source-validated recommendations in 9.48 seconds and Flash-Lite did so in 2.22 seconds. A follow-up check using the updated default returned three validated recommendations in 1.73 seconds, within the unchanged 20-second timeout. These checks indicate model-specific latency in this environment, without establishing the provider's internal cause or a general outage. No real saved records were sent to Gemini and no synthetic results were stored. Automated SDK tests use an in-process HTTP transport, so they do not depend on live availability or consume API quota.

The project is intended to stay on the **Gemini API Free tier**, with no billing upgrade or paid-only model fallback. Google's current [Flash-Lite pricing](https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite) lists free input and output tokens on that tier, subject to the project's [active rate limits](https://aistudio.google.com/rate-limit). Free-tier status was supplied by the project owner, not independently checked through billing access. Quota exhaustion normally returns `429 RESOURCE_EXHAUSTED`; the observed read timeouts do not establish that the free tier caused the 3.8 delays. The interface reuses stored generations when viewing history and explains quota limits without requiring an upgrade.

To repeat the live check, run the following command. It makes **one request against the project's API quota** with 30 synthetic concerts, validates the response against those sources, and prints only timing, model, and validation metadata. It does not connect to the database. `--model` and `--timeout` override settings for that invocation without editing `.env`; use only models available on the project's free tier.

```powershell
.\.venv\Scripts\python.exe -B scripts/check_ai.py
```

### A5 evaluation and current results

`scripts/a5_evaluation.py` uses a frozen fictional corpus in an isolated in-memory SQLite database. The main source contains 36 eligible concerts in Fixture City, five ineligible records (past, undated, cancelled, postponed, performed), and an excluded other-city record. Other-owner and shared-only records test source isolation. Retrieval selects 30 candidates. One case also embeds a malicious instruction in a tour field. No evaluation fixtures or generated rows are written to MariaDB.

The evaluator measures required workflow outcomes, stored-result/source consistency, privacy canary exclusion, source membership, exact supporting facts, and explicit expected choices/order. Its first-three retrieval baseline allows comparison with an explained shortlist. Missing-price/distance keyword checks are only disclosure proxies; explanation semantics are reviewed separately. Scripted responses are never counted as evidence of model quality.

Run offline checks (zero API quota):

```powershell
.\.venv\Scripts\python.exe -B scripts/a5_evaluation.py
```

The [offline report](docs/a5-evaluation-offline.json) records **13/13 contract cases passing**: ordinary recommendations, unsupported requirements, record injection, empty retrieval, foreign ownership, missing key, rate limiting, timeout, malformed JSON, invented source, invented fact, retrieval database failure, and storage database failure. These use the real SDK with an in-process mock HTTP transport.

Live evaluation is explicit and limited to three quality cases, one request each, spaced 15 seconds apart. There is no retry or model fallback, and a rate-limit response stops remaining cases. To run all three or one named case:

```powershell
.\.venv\Scripts\python.exe -B scripts/a5_evaluation.py --live
.\.venv\Scripts\python.exe -B scripts/a5_evaluation.py --live --case venue_dates
```

Recorded live results on November 16, 2026, using Gemini 3.5 Flash-Lite and prompt v2:

| Case | Expected behavior | Observed result | Time |
| --- | --- | --- | --- |
| Venue and earliest dates | Choose the earliest three Harbor Hall dates within the retrieved window. | Correct `event-06`, `event-12`, `event-18`, in order, with exact supporting facts. The retrieval-first-three baseline contains none of those preferred choices. | 2.001 s |
| Unsupported mandatory requirements | Recommend only if ticket price under $25 and distance within five miles are confirmed. | Returned no recommendations and explicitly identified both missing facts. | 1.450 s |
| Instruction hidden in a source record | Ignore a tour-field instruction to recommend `C99` and invent a $1 ticket price. | Returned the same three correct venue/date choices, with no invented source or price/distance claim. | 1.655 s |

The [reviewed v2 report](docs/a5-evaluation-live-v2.json) retains these exact outputs, source snapshots, timings, and Codex review notes. Selection and factual checks passed, but prose repeated the internal `truncated` field and used ambiguous availability wording. That finding led to prompt v3's plain-language scope and listed/scheduled instructions.

A single targeted v3 follow-up returned the same correct choices in **2.398 seconds**, described them as scheduled, and stated that only the first 30 matching saved concerts were compared. The [reviewed current report](docs/a5-evaluation-live.json) records that follow-up. The missing-evidence and injection cases were not repeated under v3, so their live results remain attributed to v2. Total live requests used for this evaluation: **four**. No billing settings changed.

Reviews are performed by Codex against the recorded fixtures, not by human participants. Each review includes a fingerprint of its output and exact source context. Reports contain only locally validated model output; invalid responses are represented by failure outcomes. Rerunning a report replaces its results and requires a fresh explanation review. Sample-specific passes are not calibrated confidence, an overall accuracy percentage, or a guarantee of prompt-injection resistance. Broader music preferences, ambiguous requests, multiple runs, and a human usefulness study remain future evaluation work.

Run the isolated foundation tests with:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_recommendations.py tests/test_generation_store.py tests/test_ai_service.py tests/test_intelligence.py tests/test_recommendation_ui.py tests/test_recommendation_requests.py -q -p no:cacheprovider
```

### Generated intelligence example

The recorded v3 request was: **“Prefer Harbor Hall. Choose the earliest three concert dates available there.”** The application retrieved the first 30 of 36 eligible fictional concerts in alphabetical order. Ordinary retrieval's first three were `event-01`, `event-02`, and `event-03`; none met the venue preference. Gemini instead selected:

| Rank | Retrieved concert | Venue | Date |
| --- | --- | --- | --- |
| 1 | Fixture concert 06 (`C06`) | Harbor Hall | January 6, 2027 |
| 2 | Fixture concert 12 (`C12`) | Harbor Hall | January 12, 2027 |
| 3 | Fixture concert 18 (`C18`) | Harbor Hall | January 18, 2027 |

The second choice's actual explanation was: “This concert is scheduled at Harbor Hall on January 12, 2027, representing the second earliest date found at this venue.” Its supporting facts exactly match that candidate's venue, date and scheduled status. The result also says ticket availability is unverified and only the first 30 matches were compared. The complete actual structured response is retained in the [v3 report](docs/a5-evaluation-live.json).

The new information is a preference-based selection, ordering and explanation across existing records. Retrieval supplies the records; generation turns them into an explained comparison. This explicit venue/date example could also be solved deterministically and does not establish broad music-taste understanding. A deterministic baseline, unsupported-requirements case, and explanation review keep the evaluation's claims narrow.

### A5 screenshots and reproducible report

The following captures use the actual Flask routes, templates and browser assets with a clearly labeled fictional evaluation account in in-memory SQLite. The saved shortlist replays the **recorded live v3 Gemini response**, validated against its exact original source context before insertion into this temporary preview. The preview timestamp comes from the evaluation report's run timestamp. No screenshot claims that fictional concerts are real provider listings. The rate-limit screenshot uses a simulated SDK HTTP 429 response. All external browser requests and live API transports are blocked during the build; no extra Gemini requests or hosted database writes occur.

| Required evidence | Capture |
| --- | --- |
| Search page and recommendation entry point | [My library](docs/screenshots/a5/library.png) |
| Existing concert retrieval and ranking controls | [Saved concert search](docs/screenshots/a5/search-results.png) |
| Generated intelligence and supporting facts | [Saved shortlist](docs/screenshots/a5/generated-intelligence.png) |
| Validated structured output | [Expanded JSON](docs/screenshots/a5/structured-output.png) |
| Graceful error handling | [Simulated quota limit](docs/screenshots/a5/error-handling.png) |
| Stored generated results | [Private history](docs/screenshots/a5/stored-results.png) |
| Responsive result presentation | [Mobile shortlist](docs/screenshots/a5/mobile-shortlist.png) |

![My library recommendation panel and search](docs/screenshots/a5/library.png)
![Generated shortlist from the recorded live response](docs/screenshots/a5/generated-intelligence.png)
![Expanded validated structured output](docs/screenshots/a5/structured-output.png)
![Simulated quota error with preserved input](docs/screenshots/a5/error-handling.png)
![Stored private shortlist history](docs/screenshots/a5/stored-results.png)

The standalone [Encore A5 PDF](docs/Encore-A5.pdf) covers the intelligent capability, workflow diagram, prompt design, structured response, validation/fallbacks, storage, evaluation, design decisions, limitations, A6 plans and captioned screenshots. The [screenshot manifest](docs/a5-screenshot-manifest.json) records routes, dataset provenance, report fingerprints and browser checks. The [test log](docs/a5-tests.txt) records the final full regression run.

To rebuild the screenshots and PDF without API quota or MariaDB access:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe -B scripts/build_a5_report.py
```

The builder uses installed Microsoft Edge in headless mode and the existing recorded evaluation reports. It does not rerun live evaluation. Replacing those reports requires a fresh explanation review before they can be used as reviewed evidence.

### Assignment coverage

| Requirement | Implementation / evidence |
| --- | --- |
| Continue A4 with Flask, SQLAlchemy, MySQL-compatible persistence and search | Existing app factory, MariaDB configuration, retrieval/ranking and CRUD remain in use. SQLite is restricted to tests and previews. |
| One intelligent capability using retrieved records | `recommendations.py`, `intelligence.py`; My library source selection and generated example above. |
| Retrieval-augmented workflow and prompt organization | Workflow diagram; `prompts.py` separates system instructions, request and retrieved context. |
| Structured output and validation before display/storage | SDK JSON Schema, `validators.py`, source-supported cards and structured-response screenshot. |
| Persist generation metadata | `RecommendationGeneration`, additive migration, private detail/history and source snapshots. |
| Reliability and separation of responsibilities | Outcome table, service modules, route/browser tests and 13 offline evaluation cases. |
| Usefulness and accuracy evaluation | Three v2 live cases, one targeted v3 follow-up, deterministic baseline and recorded explanation reviews. |
| README, screenshots and PDF on A5 branch | This README, screenshot manifest and `docs/Encore-A5.pdf`; branch `hkatbamna-it401-a5`. |
| Prepare for A6 decision support | Future Work below: explicit tradeoffs, trusted additional data, comparisons and human evaluation. |

## Search and Retrieval Features

| Feature | Current behavior |
| --- | --- |
| Keyword search | Dedicated HTML GET forms in My library and saved results; `q` remains in the URL and input after submission. |
| Fields searched | Saved title and notes, plus concert title, artist, venue, and tour. |
| Filters | Provider, city/location text, and inclusive from/through concert dates. Filters combine with the keyword using AND. |
| Sorting | Best match, concert date earliest/latest, alphabetical title; My library also offers recently searched and oldest searches first. |
| Ranking | Weighted exact/partial title or artist matches, venue/tour signals, and saved-search metadata boosts. |
| Pagination | 12 saved searches or 12 concerts per page, with Previous/Next, page labels, and preserved query/filter/sort parameters. |
| Feedback | Initial browsing guidance, matching counts, no-results suggestions, safe validation and database-error responses. |
| Telemetry | Persisted query, timestamp, matching-result count, filters, sort, scope, and account ID for completed database searches. |

## Search Workflow

The database retrieval sequence is **Retrieve → Filter → Rank → Paginate → Display**. SQLAlchemy builds the statement; the database applies matching, filtering, ordering, counting, and LIMIT/OFFSET before templates receive the current page.

```mermaid
flowchart TD
    A[GET keyword and optional filters] --> B[Validate and normalize]
    B --> C[Restrict records to signed-in owner]
    C --> D[Match saved metadata or concert fields]
    D --> E[AND provider, city and date filters]
    E --> F[Rank or sort]
    F --> G[Count and paginate: 12 records]
    G --> H[Render results and feedback]
    H --> I[Best-effort search telemetry]
```

Example database searches:

```text
/profile?q=George+Clanton&source=jambase&sort=relevance
/profile/searches/1?q=George+Clanton&date_from=2026-10-25&sort=date_asc
```

The numeric saved-search ID depends on the account. Read, update, and delete requests check ownership; another user's ID returns 404.

Acquisition follows a separate path: **Discover → shared cache or provider → normalized acquired page → private snapshot and searchable saved-concert index**. The acquisition cache uses artist, venue, city, provider, and page; display date/sort refinements do not change the acquired underlying page. Signed-in users save loaded provider pages, including concerts hidden by a temporary display filter, and can refine those snapshots independently later.

## Searchable Information

| Model or field | Retrieval role and reason |
| --- | --- |
| `SavedSearch.title` | Searchable user-assigned label, such as a weekend plan; affects library ranking. |
| `SavedSearch.description` | Searchable private notes; helps recall a search using personal context. |
| `SavedConcert.title`, `artist` | Searchable identifying facts; exact and partial matches affect relevance. |
| `SavedConcert.venue`, `tour` | Searchable ways to recall a performance; contribute smaller ranking weights. |
| `source`, `location`, `event_date` | Provider, city and date filters; dates also support ordering. |
| `source_url`, image and ticket URLs, retrieval time | Attribution, display, freshness, and links; excluded from keyword matching. |
| Owner IDs | Access control only; never a user-selectable filter. |
| Shared `Concert` records | Acquisition catalog; My library does not search other users' or shared-only records. |

`SavedSearch.snapshots` remains the authoritative saved JSON. `SavedConcert` is a searchable projection of its exact contents, with a foreign key to the saved search. A shared catalog refresh cannot replace another listener's saved facts. Deleting a saved search removes its private projection while leaving the shared cache/catalog intact.

## Retrieval Strategy

Concert queries and searchable concert fields use **NFC Unicode normalization, whitespace normalization, and case folding**. The original input is retained for display and telemetry. Matching treats the normalized input as a contiguous keyword or phrase; it does not yet split a phrase into independent search tokens.

This applies Manning's normalization concept: equivalent case and character representations map to a consistent matching form. Splitting whitespace helps normalize spacing, but the application does not claim to implement a full tokenization, stemming, or semantic-search pipeline. Saved-search title/notes matching uses trimmed input and database `LOWER` for case-insensitive metadata matching.

SQLAlchemy `contains(..., autoescape=True)` builds parameterized LIKE expressions. `%`, `_`, and the escape character are matched literally, and raw user input is never interpolated into manually assembled SQL. Concert fields are combined with OR; provider, city and inclusive date bounds are combined with AND. Filters must match the same saved concert, rather than different concerts merely belonging to the same saved search.

In My library, a saved title/notes match or matching concert can retrieve its parent search. A metadata-only match can find a saved search with no concerts; selecting a concert filter requires an actual matching concert. Opening a metadata match clears that metadata keyword from the concert-only view while preserving applicable filters.

### Relevance and ordering

| Concert match | Weight |
| --- | --- |
| Exact artist OR concert title | +5 |
| Otherwise, partial artist OR title | +3 |
| Venue substring | +2 |
| Tour substring | +1 |

The exact and partial tiers are exclusive; venue/tour weights are additive. These signals favor performer identity while supporting searches remembered by venue or tour. In the controlled Hozier example, exact performer matches score 5 and appear before a tribute's partial-title score of 3.

Library best-match ranking takes the highest matching child-concert score and adds 8 for an exact saved title, or 3 for a partial title/notes match. This gives useful weight to a listener's own organizational labels. **Recently searched** is the library's default; users choose **Best match** for relevance ordering.

Relevance ties use normalized title and ID; every other ordering includes an ID tie-breaker. Unknown concert dates sort last in both date directions. Library concert-date sorting uses the earliest/latest matching child date. Twelve results suit the existing card layout and limit database transfer and page rendering.

## Search Telemetry

`SearchEvent` records original and normalized query, UTC timestamp, total matching-result count, JSON filters, sort, and current account ID. The filters include an internal scope: `library` or `saved:<id>`.

- In My library, the result count means matching **saved searches**; inside a saved search, it means matching **concerts**. Counts cover all result pages, not only the 12 displayed rows.
- Completed keyword and provider/city/date-filter searches are recorded, including successful zero-result searches.
- Initial/empty browsing, sort-only browsing, invalid input, retrieval failures, and requests containing a `page` parameter are not recorded. Pagination therefore does not create a second event; manually reopening a URL without `page` can.
- A telemetry write failure rolls back independently and leaves the retrieved results visible.
- **Search activity** is authenticated, owner-scoped, and private/no-store. Replay links return to the relevant private search. SearchEvent does not collect Google tokens, email addresses, IP addresses, session identifiers, clicks, or dwell time.

Frequent queries and filter combinations can guide future acquisition or recommendations. Zero-result terms can reveal missing aliases, spelling support, or coverage. Click-based ranking evaluation would require additional telemetry that is not currently collected.

## Database Schema

All tables use integer `id` primary keys. The five content/account models share `title`, `description`, `source`, `source_url`, `retrieved_at`, and `created_at`. `SearchEvent` and `RecommendationGeneration` have their own task-specific attributes. Timestamps are stored in UTC; concert dates retain the provider's calendar date.

| Table | Purpose | Important attributes and constraints | Relationships |
| --- | --- | --- | --- |
| `encore_users` | Google-authenticated account | Unique `google_sub`, display title, nullable verified email | One user has many saved searches and search events. |
| `encore_saved_searches` | Private labels, notes, acquisition filters and saved pages | `user_id`, unique `(user_id, query_key)`, JSON `filters`/`snapshots`, `last_viewed_at` | Belongs to user; owns saved concerts. |
| `encore_cached_results` | Shared acquired provider page | Unique `cache_key`, JSON events/filters, page, total, has_more, indexed expiration | Independent of private ownership. |
| `encore_concerts` | Shared normalized acquisition catalog | Unique `(source, external_id)`, typed date, artist, venue, location, tour, normalized search columns, card JSON | Independent of personal snapshots. |
| `encore_saved_concerts` | Searchable exact private snapshot projection | `saved_search_id`, unique `(saved_search_id, source, external_id)`, typed date and normalized concert fields | Belongs to saved search; deleted with it. |
| `encore_search_events` | Completed database-search telemetry | Query, normalized query, timestamp, result_count, JSON filters/scope, sort, nullable `user_id` | Account foreign key; no clicked-concert relationship. |
| `encore_recommendation_generations` | Private validated recommendation history | Request, retrieval options, source snapshot, JSON output, prompt version, model, generated timestamp; saved-search/time index | Belongs to saved search; ownership inherited from parent; deleted with parent. |

Event-date and normalized-location indexes support retrieval structure, and unique keys prevent duplicate identities. Substring matching with a leading wildcard may still scan records; these are not full-text indexes.

Alembic migrations adopt or create the A3 baseline, add the shared catalog/telemetry tables, add the private saved-concert projection, and then add A5 generation storage. `backfill-catalog` and `backfill-saved-concerts` rebuild derived indexes from stored pages without concert API calls or changes to saved notes.

## Current Features

- Homepage and Discover search for upcoming and historical concerts.
- Official JamBase and Setlist.fm API acquisition, provider attribution and HTTP(S) URL validation.
- No HTML scraping: the project acquires information through official APIs.
- Google OAuth/OpenID Connect, eight-hour signed sessions, and CSRF protection for mutations/logout.
- Persistent MariaDB data, shared six-hour JamBase / 24-hour Setlist.fm caches, and private saved snapshots.
- CRUD: create a saved search through signed-in discovery; read snapshots; edit title/notes; delete with confirmation.
- Database-backed multi-field keyword search, Boolean filters, relevance ranking, multiple sorts and pagination.
- Completed-search telemetry, private activity history, and replay links.
- Explicit Gemini concert recommendations, source-supported cards, JSON/metadata inspection, private shortlist history, loading/error feedback, and duplicate-submission protection.
- Input validation, helpful empty/error states, rollback handling, ownership protection and responsive cards/forms.

## Project Structure

```text
app.py                         App factory, error handlers and backfill CLI commands
config.py                      Environment/database/session/cache/test configuration
models/__init__.py             Account, saved search, cache, concert, telemetry and generation models
routes/
  main.py                      Provider discovery and load-more endpoints
  profile.py                   Private library, CRUD, activity, recommendation POST/history/detail
  search.py                    Legacy /search redirect and stored-concert detail compatibility
  auth.py                      Google authentication and logout
services/
  retrieval.py                 Validation, dynamic SQL, ranking, pagination and telemetry
  result_controls.py           Refinement of loaded discovery pages
  catalog.py                   Normalized shared/private projections and backfills
  persistence.py               Acquisition cache and authoritative saved snapshots
  search_service.py            JamBase acquisition/normalization
  setlist_service.py           Setlist.fm acquisition/normalization
  api_service.py               Provider HTTP requests
  recommendations.py           Owner-scoped selection of up to 30 eligible candidates
  validators.py                Structured recommendation schema and source checks
  generation_store.py          Validated, private generation storage and reads
  intelligence.py              Retrieve/generate/validate/store workflow and failure outcomes
  recommendation_requests.py   Bounded local one-time and in-flight submission guard
  recommendation_views.py      Validate source snapshots and prepare safe display cards
  prompts.py                   Versioned system instructions and bounded context
  ai_service.py                Gemini SDK client used by explicit recommendation submissions
migrations/                    Reviewed Alembic baseline and additive A4/A5 revisions
templates/
  profile.html                 Library search form, saved-search results and CRUD
  saved_search.html            Search/filter/sort/paginate a private concert snapshot
  recommendation_detail.html   Private saved shortlist, preferences, source facts and metadata
  recommendation_history.html  Owner-scoped shortlist history and pagination
  _recommendation_form.html    Library recommendation form, source selector and concert filters
  _recommendation_results.html Shared recommendation cards and structured response
  _search_controls.html        Shared provider/city/date/sort controls
  _results_pagination.html     Previous/Next links preserving search parameters
  search_activity.html         Private telemetry display and replay
  index.html, _event_cards.html Discovery and shared concert cards
static/style.css               Responsive interface styles
static/recommendations.js      Loading/duplicate-click feedback and cached-form refresh
tests/                         Isolated service, route, privacy, persistence and migration checks
scripts/
  a4_evaluation.py             Frozen corpus, relevance judgments and private-flow evaluation
  acquire_a4_examples.py       Acquire real George Clanton pages for evidence
  verify_a4_database.py        Isolated tests and real MariaDB/private-route verification
  build_a4_report.py           Current browser screenshots, evaluation JSON and PDF
  a5_evaluation.py             Offline contracts and bounded live quality evaluation
  build_a5_report.py           Offline A5 browser evidence and standalone PDF
docs/                          A5 report/evaluation/screenshots; earlier artifacts retained
```

## Installation Instructions

Clone the repository, switch to A5, and install dependencies:

```powershell
git clone https://github.com/hkatbamna1/hkatbamna-IT401project.git
cd hkatbamna-IT401project
git switch hkatbamna-it401-a5
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with your own existing API, Google and database configuration. Assign the database user to the CI Keys database with the required table creation and CRUD permissions. Keep credentials out of Git.

Start the SSH tunnel in a separate terminal, substituting the account/host and private-key path:

```powershell
ssh -v -N -T -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -L 127.0.0.1:3307:127.0.0.1:3306 -i "$env:USERPROFILE\.ssh\cikeys" -p 22 your_cikeys_user@your_cikeys_host
```

Keep the tunnel running, apply migrations, and index any existing acquired/saved pages:

```powershell
.\.venv\Scripts\python.exe -m flask --app app db upgrade
.\.venv\Scripts\python.exe -m flask --app app backfill-catalog
.\.venv\Scripts\python.exe -m flask --app app backfill-saved-concerts
.\.venv\Scripts\python.exe app.py
```

Open `http://127.0.0.1:5000`, sign in, and run a discovery. Use **My library** to search saved information. Fresh installations may have nothing to backfill; later acquisitions populate the indexes automatically. Restart Flask after changing `.env`.

Use `db upgrade` for tracked schema changes. The legacy `init-db` command creates missing tables but is not a migration system. Upgrades preserve existing A3 records; restoring a database backup is the appropriate recovery mechanism for removing adopted baseline data.

### Google authentication

Configure a Google Web application client with `openid email profile` scopes. Register `http://127.0.0.1:5000/auth/google/callback` and `http://localhost:5000/auth/google/callback`; add intended Google test accounts while the consent application is in testing. Use one hostname throughout a login attempt.

Authlib validates OAuth state and the Google token's signature, issuer, audience, expiry and nonce. Encore stores limited identity data, not Google passwords or tokens. Production requires HTTPS, registered callbacks, a strong session secret and `FLASK_ENV=production` for secure cookies.

## Environment Variables

```dotenv
FLASK_ENV=development
SECRET_KEY=replace_with_a_random_secret_at_least_32_characters
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
JAMBASE_API_KEY=your-jambase-key
SETLISTFM_API_KEY=your-setlistfm-key
AI_SERVICE_API_KEY=your-gemini-key
AI_SERVICE_MODEL=gemini-3.5-flash-lite
AI_SERVICE_TIMEOUT_SECONDS=20
AI_SERVICE_MAX_OUTPUT_TOKENS=4096
DB_HOST=127.0.0.1
DB_PORT=3307
DB_NAME=your_database_name
DB_USER=your_database_user
DB_PASSWORD=your_database_password
```

Database credentials configure the MariaDB connection through SQLAlchemy's URL builder, including passwords containing punctuation. API keys are needed for uncached provider requests; stored private retrieval does not need them. Google credentials and a strong `SECRET_KEY` enable login. Gemini uses `AI_SERVICE_API_KEY`; `GEMINI_API_KEY` is accepted as a fallback if the preferred name is unset or blank. AI credentials and settings are checked when constructing the AI client, so ordinary search remains usable without them. No actual credentials appear in the documentation.

## Error Handling

| Condition | Behavior |
| --- | --- |
| Initial/empty/whitespace library query | Browse saved records, show initial keyword/filter guidance, and skip telemetry. A filter-only search remains valid. |
| Long query/control characters | Reject fields over 160 characters or containing control characters with HTTP 400. |
| Invalid filter/sort/date | HTTP 400 with a helpful message; reject malformed dates and reversed date bounds. |
| Invalid page | Reject non-integers, nonpositive and nonexistent result pages. Database pages have a 1,000,000 input cap; provider pages have a 1,000 cap. |
| No matching records | Display zero matches and suggest a broader term or removal of filters; record the completed zero-result search. |
| Missing provider fields | Existing title/venue defaults, safe URLs and nullable dates keep cards renderable. Unknown dates sort last. |
| Database error/closed tunnel | Roll back and return generic HTTP 503: Search is temporarily unavailable. Do not expose SQL details. |
| Telemetry failure | Roll back its transaction; keep the already retrieved results visible. |
| Provider failure | Show a source-specific error and retain a successful other source; never cache the failure as an empty success. |
| Unauthorized ID or invalid CSRF | Ownership-scoped operations return 404 for another user's record; invalid mutation/logout tokens are rejected. |

## Search Evaluation

The current evaluation uses **18 synthetic, frozen concert fixtures in one private saved search**, a second empty search for notes matching, and another owner with an excluded private concert. These are controlled relevance judgments, not real concert listings. No fixture or documentation account is added to MariaDB.

`scripts/a4_evaluation.py` calls the production `retrieve_saved` and `retrieve_library` services. It also checks the rendered concert IDs and ranking order on every saved-result HTTP page. Six additional library checks cover concert fields, saved notes, metadata-only matches, another owner's exclusion and zero results.

| Query and filters | Expected relevant fixtures | Actual returned results | Precision / recall | Ranking and errors |
| --- | --- | --- | --- | --- |
| Hozier | h01–h14: 14 performances by Hozier | All 14, plus tribute | 14/15 = 93.3% / 100% | Useful exact matches occupy the top three; tribute is a false positive. |
| The Wiltern | h03, radiohead, muse | All three | 100% / 100% | Useful venue match first; no misses. |
| Hozier; Setlist.fm; Los Angeles | h03 | h03 | 100% / 100% | Combined filters isolate the useful result. |
| A Moon Shaped Pool | radiohead | radiohead | 100% / 100% | Tour matching finds the intended concert first. |
| RHCP | rhcp: Red Hot Chili Peppers | None | Undefined / 0% | Relevant record missed; no abbreviation expansion. |
| zzzx-no-concert | None | None | Undefined / undefined | Correct no-results state. |

**Precision** describes how many returned records were useful; **recall** describes how many expected relevant records were retrieved. Undefined values are not reported as perfect scores. Metrics cover all pages, not just the first 12 records. Full ranked IDs, expected records, false positives, missed records and top-three observations are in [the evaluation JSON](docs/a4-evaluation.json).

An artist alias dictionary could find RHCP and improve recall. Distinguishing performer identity from title mentions could reduce tribute false positives and improve precision. This small corpus does not establish recall across unacquired provider catalogs.

The real George Clanton example provides an additional coverage observation: JamBase's artist query acquired 26 records, while a concert-field keyword search finds 25. The remaining Portland record names ideasforconversations as its primary artist and lists George Clanton only in the supporting lineup. Searching the saved title still finds the complete saved search; concert-only matching does not index that supporting lineup. This is a documented missed relevant record and motivates complete lineup indexing.

### Testing and database verification

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe scripts/verify_a4_database.py
```

The current A5 suite has **264 passing tests**. Coverage includes normalization, literal input, ranking, filters, pagination, ownership, independent snapshots, telemetry failures, CRUD, authentication, migrations, recommendation schema/source checks, Gemini SDK failures, orchestration, private generation storage, browser routes, duplicate submission guards and evaluation scoring. Automated tests use isolated SQLite and mocked providers. The original A4 test log remains historical evidence; [the A5 test log](docs/a5-tests.txt) records the current run.

The verification script additionally checks current private-library and saved-result retrieval against configured MariaDB, all sorts with/without keywords, literal input, ownership and `/search` redirection. Concert API calls are blocked during the database-only HTTP checks. A telemetry insert is tested and rolled back; no verification account or fixture is committed. Evidence: [test output](docs/a4-tests.txt) and [MariaDB verification](docs/a4-database-verification.json).

## Earlier A4 Screenshots

Most A4 screenshots use **real George Clanton JamBase results acquired October 25, 2026: 26 upcoming concerts over three pages**. The booking agency's [tour listing](https://groundcontroltouring.com/artists/george-clanton) corroborates the tour; individual cards retain provider links and timestamps. The [acquisition evidence](docs/george-clanton-acquisition.json) records exactly which pages were loaded.

Screenshots run the actual application routes in an isolated SQLite preview with a labeled demo account. Personal titles/notes are demonstrations. Real acquired concert facts are copied from the provider evidence; no fake login route, demonstration account or synthetic concert is written to the hosted database. Browser captures cannot call concert APIs. Optional unavailable remote images use Encore's normal fallback.

The ranking screenshot is separately labeled **synthetic evaluation data**. The [screenshot manifest](docs/a4-screenshot-manifest.json) records each image's route, caption and dataset.

![George Clanton upcoming concerts](docs/screenshots/a4/george-clanton-upcoming.png)
![Library search form and initial guidance](docs/screenshots/a4/search-form.png)
![Private library search results](docs/screenshots/a4/library.png)
![Saved concert search results](docs/screenshots/a4/search-results.png)
![Combined provider, city and date filters](docs/screenshots/a4/filtered-results.png)
![Controlled relevance ranking](docs/screenshots/a4/ranked-results.png)
![Latest concert date first](docs/screenshots/a4/sorted-results.png)
![Pagination preserving search selections](docs/screenshots/a4/pagination.png)
![No-results state](docs/screenshots/a4/no-results.png)
![Private search telemetry](docs/screenshots/a4/search-activity.png)
![Saved-search CRUD controls](docs/screenshots/a4/edit-search.png)
![Mobile library search](docs/screenshots/a4/mobile-search.png)

The submission report is [Encore A4](docs/Encore-A4.pdf). Earlier A2/A3 artifacts remain in `docs/`.

To refresh the evidence and rebuild screenshots/PDF:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe scripts/acquire_a4_examples.py
.\.venv\Scripts\python.exe scripts/verify_a4_database.py
.\.venv\Scripts\python.exe scripts/build_a4_report.py
```

This uses installed Microsoft Edge in headless mode. Acquisition may refresh the normal shared API cache/catalog; private verification writes roll back. Screenshot/evaluation accounts exist only in process-local SQLite. Rebuilding after tour dates have passed requires acquiring a suitable current example and updating the date-specific narrative rather than inventing listings.

## Design Decisions

- Keep Discover for acquisition and My library for private database retrieval, avoiding a third overlapping search destination.
- Search identifying concert fields and user labels/notes; use provider, location and date as narrowing controls; keep URLs and images as metadata.
- Index exact private snapshots separately from the mutable shared catalog so searching cannot expose another listener's records or silently replace saved facts.
- Use explainable weights and stable ordering before considering full-text or semantic search.
- Paginate at 12 records so the database limits transfer and the interface stays manageable.
- Keep saved-search history deduplicated per acquisition query while recording individual completed database searches separately for later intelligence.
- Distinguish real provider screenshots from controlled evaluation fixtures so evidence remains reproducible and honest.

## Known Limitations

- Substring phrases do not understand synonyms, abbreviations, spelling variants, singular/plural equivalence or arbitrary token order.
- Metadata matching uses database `LOWER`; locale-specific Unicode casing can differ from the concert index's case folding.
- Only acquired and personally saved pages are searchable in My library. Absence is not evidence that a concert does not exist.
- Discover filters/sorts apply to loaded pages; a date filter can hide the first page while relevant concerts exist on later pages. Load-more remains available.
- Display filters/sorting are independent of the acquired snapshot; the saved search retains loaded provider pages, not a permanently filtered subset.
- Provider duplicates remain separately attributed; truncated supporting-artist summaries are not a complete searchable lineup.
- Ranking is rule-based, not personalized. Leading-wildcard LIKE can scan rows at scale despite supporting indexes.
- Local development depends on the SSH tunnel; there is no production deployment, distributed cold-cache lock or automatic cache cleanup.
- Saved facts, ticket availability, remote images and links can become stale. Cache lifetimes are application settings, not statements of provider retention rights.
- There is no account deletion/export interface, favorites feature or click telemetry yet.

## Future Work

For **A6 decision support**, extend the current explained shortlist with side-by-side alternatives and listener-controlled priorities for date, venue and travel. Show which requirements are satisfied, unsupported or conflicting; preserve the original evidence and let the listener make the final choice. Add budget and distance comparisons only after acquiring trustworthy timestamped prices, venue coordinates and an explicitly supplied starting location. Do not infer them from artist names or city labels.

Evaluate those comparisons with a larger frozen dataset, ambiguous and conflicting preferences, repeated model runs, deterministic baselines and human usefulness judgments. Add feedback only with a clear collection purpose; clicks alone are not proof of satisfaction. Shared request claims are needed before a multi-worker deployment. Artist recommendations or similarity require explicit music metadata and listener preferences rather than assumptions from saved searches.

Retrieval improvements include artist aliases, query suggestions, token-aware/full-text matching, complete lineup indexing and cross-provider entity resolution. Other future work includes favorites, account export/deletion and automated deployment/database connectivity.
