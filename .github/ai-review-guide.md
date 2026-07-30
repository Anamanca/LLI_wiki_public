# AI Code Review Guide

When reviewing AI-generated PRs, check these 8 points:

## 1. Library/API Hallucination
- Does every imported library actually exist at that version?
- Does every API method call match the actual SDK?
- AI often invents `client.someMethod()` that doesn't exist

## 2. Business Logic Correctness
- Does the code solve the right problem?
- Edge cases: empty input, null values, very large input, non-English text

## 3. Architecture Compliance
- **Domain layer**: no FastAPI/SQLAlchemy/Redis imports
- **Application layer**: minimize direct SQLAlchemy imports; prefer repository ports where they exist. Existing use cases (event_linker.py, stale_recovery.py, youtube_poller.py, event_extractor.py) use SQLAlchemy directly — this pattern is acceptable for complex DB operations but new code should use repository ports when available.
- **Infrastructure layer**: implements Ports, doesn't contain business logic
- Check: `grep -r "from fastapi" src/llm_wiki/domain/` must return nothing

## 4. Port/Adapter Pattern
- New external service → new Port in `application/ports/` first
- Adapter in `infrastructure/` implements that Port
- Use case code works with the port, never the adapter (unless existing pattern)

## 5. API Contract
- Response schema matches `frontend/types/index.ts` field names and types
- No snake_case fields leaked to frontend (use Pydantic `alias` or camelCase)
- Run `tests/test_all_apis.py` contract tests

## 6. Error Handling
- Business errors → `DomainException` subclass (mapped to HTTP in middleware)
- No bare `except:` or `except Exception:` swallowing errors silently
- Cache/Redis failures → logged, not thrown (graceful degradation)
- LLM failures → retry or 502 with clear message

## 7. Security
- No hardcoded API keys, tokens, passwords
- Use `settings.XXX` from pydantic-settings
- Validate input at route layer (Pydantic schemas)

## 8. Tests
- New endpoint → contract test in `tests/test_all_apis.py`
- New use case → unit test in `tests/unit/application/`
- Edge cases tested: empty input, invalid input, timeout, error response
