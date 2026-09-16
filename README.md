# HubSpot Agreement Extraction Pipeline

Pulls signed agreements off closed-won deals in HubSpot and turns them into
structured billing terms. Stdlib-only Python, no framework, no build step.

```
HubSpot closed deal -> resolve signed agreement (Note attachment or Quote)
  -> download the PDF -> extract text -> extract structured content (LLM)
```

## Run it

```bash
python run_pipeline.py
```

Runs with **zero credentials** out of the box — `HUBSPOT_MODE` and `LLM_MODE`
both default to mock, so it reads a local sample agreement
(`fixtures/sample_agreement.txt`) and extracts fields with a regex
heuristic instead of calling any real API.

## What's here

| File | Does |
|---|---|
| `integrations/hubspot.py` | Real HubSpot client — searches closed deals, resolves each one's signed agreement (a Note's attachment first, a fully e-signed Quote as fallback), downloads it via the `/signed-url` endpoint (required for non-public files) |
| `integrations/_http.py` | Stdlib JSON-over-HTTP helper — retry/backoff on 429/5xx, and an honest `User-Agent` (some providers' CDN/Cloudflare layers block urllib's default signature outright) |
| `pdf_text.py` | Agreement bytes -> plain text: `pdfplumber` for real PDFs, direct decode for text-bearing files, `needs_ocr` flagging for scanned documents |
| `content_extractor.py` | Plain text -> structured fields (customer, billing email, effective date, payment terms, total, currency, signed) via a real LLM call, or a regex heuristic with zero credentials |
| `config.py` | Loads every setting from `.env` — HubSpot and LLM modes are independent toggles |
| `run_pipeline.py` | The CLI entry point — resolves every closed deal with a signed agreement (not just the first), runs extraction on each, prints a formatted table + raw JSON per deal, and a summary table across all of them |
| `fixtures/sample_agreement.txt` | The mock-mode fixture |
| `fixtures/sample_contracts/*.pdf` | Three real sample agreements (different customers, USD totals, payment terms) used to test the live HubSpot path |

## Going live

Copy `.env.example` to `.env` and fill in:

- **`HUBSPOT_MODE=live`** + `HUBSPOT_TOKEN` — a HubSpot Private App token. Required scopes: `crm.objects.deals.read`, `crm.objects.companies.read`, `crm.objects.contacts.read`, `crm.objects.quotes.read`, `files.read`. (`crm.objects.notes.read` is a known HubSpot UI gap — it doesn't appear in the scope picker for anyone; reading a Note's attachment works fine under `crm.objects.contacts.read` instead.)
- **`LLM_MODE=live`** + either:
  - `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` — needs a funded Anthropic account.
  - `LLM_PROVIDER=groq` + `GROQ_API_KEY` — free-tier friendly alternative; same prompt, different provider. Check `GET https://api.groq.com/openai/v1/models` against your own key before assuming a model name — Groq's catalog varies by account and changes over time.

The two toggles are independent — mix real HubSpot data with the mock heuristic, or the local fixture with a real LLM call, however's useful while testing.

## Verified

Tested end-to-end against a real HubSpot developer test account and a live Groq model: three closed deals, three different signed agreements, all fields extracted correctly at 99-100% confidence, including the private-file `/signed-url` download path and both User-Agent fixes.
