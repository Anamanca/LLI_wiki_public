"""Unit tests for fact merger (P2 reduce step)."""

from __future__ import annotations

from llm_wiki.application.use_cases.ingestion.fact_merger import merge_chunk_facts


def _chunk1() -> dict:
    return {
        "classification": {
            "main_topic": "Thị trường BĐS",
            "domain": "real_estate",
            "subtopics": ["Giá nhà"],
            "key_entities": ["VHM"],
            "language": "vi",
            "summary_3sentences": "Tóm tắt 1.",
        },
        "entities": {
            "companies": [{"name": "VHM", "ticker": "VHM", "sector": "BĐS",
                              "type": "stock_ticker"}],
            "people": [{"name": "Nguyễn Văn A", "role": "CEO", "type": "person"}],
        },
        "numbers": [{"value": "850 tỷ", "unit": "VND", "context": "bán ròng"}],
        "events": [
            {
                "description": "Khối ngoại bán ròng",
                "normalized_date": "2025-03-15",
                "category": "chung_khoan",
                "impact_direction": "negative",
                "confidence": 0.9,
                "attribution": {"speaker": "A", "is_opinion": False, "certainty": "certain"},
            }
        ],
        "relationships": [
            {"source": "VND", "target": "VHM", "relation_type": "depreciates", "confidence": 0.8}
        ],
        "entity_relations": [
            {"from": "VHM", "from_type": "stock_ticker", "to": "BĐS",
             "to_type": "sector",
             "predicate": "belongs_to_sector", "confidence": 0.95}
        ],
        "key_claims": [
            {"claim": "Thị trường sẽ hồi phục", "speaker": "A", "claim_type": "prediction"}
        ],
        "market_context": "Bối cảnh 1",
        "chunk_summary": "Chunk 1: ...",
    }


def _chunk2() -> dict:
    return {
        "classification": {
            "main_topic": "Thị trường BĐS",
            "domain": "real_estate",
            "subtopics": ["Giá nhà", "Lãi suất"],
            "key_entities": ["VHM", "VCB"],
            "language": "vi",
            "summary_3sentences": "Tóm tắt 2.",
        },
        "entities": {
            "companies": [{"name": "VHM", "ticker": "VHM", "sector": "BĐS",
                              "type": "stock_ticker"}],
            "people": [{"name": "Nguyễn Văn A", "role": "CEO", "type": "person"}],
        },
        "numbers": [{"value": "850 tỷ", "unit": "VND", "context": "bán ròng"}],
        "events": [
            {
                "description": "Khối ngoại bán ròng mạnh trên sàn HOSE",
                "normalized_date": "2025-03-15",
                "category": "chung_khoan",
                "impact_direction": "negative",
                "confidence": 0.7,
                "attribution": {"speaker": "B", "is_opinion": True, "certainty": "probable"},
            }
        ],
        "relationships": [
            {"source": "VND", "target": "VHM", "relation_type": "depreciates", "confidence": 0.8}
        ],
        "entity_relations": [
            {"from": "VHM", "from_type": "stock_ticker", "to": "BĐS",
             "to_type": "sector",
             "predicate": "belongs_to_sector", "confidence": 0.95}
        ],
        "key_claims": [
            {"claim": "Thị trường sẽ hồi phục", "speaker": "A", "claim_type": "prediction"}
        ],
        "market_context": "",
        "chunk_summary": "Chunk 2: ...",
    }


def test_merge_dedup_entities_by_type_and_name() -> None:
    """Same name in different categories must NOT collapse."""
    c1 = _chunk1()
    c2 = _chunk2()
    c2["entities"]["companies"] = [{"name": "A", "ticker": None, "sector": None, "type": "company"}]
    c1["entities"]["people"] = [{"name": "A", "role": None, "type": "person"}]
    merged = merge_chunk_facts([c1, c2])
    # company A and person A both survive (different categories)
    assert any(e["name"] == "A" for e in merged["entities"]["companies"])
    assert any(e["name"] == "A" for e in merged["entities"]["people"])


def test_merge_dedup_numbers() -> None:
    merged = merge_chunk_facts([_chunk1(), _chunk2()])
    assert len(merged["numbers"]) == 1
    assert merged["numbers"][0]["value"] == "850 tỷ"


def test_merge_events_field_preserving() -> None:
    """Near-duplicate events merge into one, keeping rich description + max confidence."""
    merged = merge_chunk_facts([_chunk1(), _chunk2()])
    assert len(merged["events"]) == 1
    ev = merged["events"][0]
    assert ev["confidence"] == 0.9  # max kept
    assert ev["impact_direction"] == "negative"
    # richer description kept (chunk2's longer one)
    assert "mạnh trên sàn HOSE" in ev["description"]


def test_merge_relationships_and_entity_relations_separate() -> None:
    merged = merge_chunk_facts([_chunk1(), _chunk2()])
    assert len(merged["relationships"]) == 1
    assert merged["relationships"][0]["relation_type"] == "depreciates"
    assert len(merged["entity_relations"]) == 1
    assert merged["entity_relations"][0]["predicate"] == "belongs_to_sector"


def test_merge_claims_dedup() -> None:
    merged = merge_chunk_facts([_chunk1(), _chunk2()])
    assert len(merged["key_claims"]) == 1


def test_merge_classification_hint_primary() -> None:
    """Caller hint is primary; chunk data only fills missing fields."""
    hint = {
        "main_topic": "Hint topic",
        "domain": "finance",
        "subtopics": [],
        "key_entities": [],
        "language": "vi",
        "summary_3sentences": "",
    }
    merged = merge_chunk_facts([_chunk1(), _chunk2()], classification_hint=hint)
    assert merged["classification"]["main_topic"] == "Hint topic"
    assert merged["classification"]["domain"] == "finance"
    # subtopics filled by union from chunks
    assert "Lãi suất" in merged["classification"]["subtopics"]


def test_merge_empty_input() -> None:
    merged = merge_chunk_facts([])
    assert merged["entities"]["companies"] == []
    assert merged["entities"]["people"] == []
    assert merged["classification"] == {}
    assert merged["numbers"] == []
    assert merged["events"] == []


def test_merge_chunk_summaries_collected() -> None:
    merged = merge_chunk_facts([_chunk1(), _chunk2()])
    assert len(merged["chunk_summaries"]) == 2
