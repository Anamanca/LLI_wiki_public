"""GraphRAG traversal adapter — queries the knowledge graph via SQL.

Traverses entity→event links and event→event timeline chains
to enrich retrieval with structured knowledge that vector search
alone cannot capture.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.graph_rag_port import GraphRAGPort
from llm_wiki.domain.value_objects.embedding import SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange

logger = logging.getLogger(__name__)


class PostgresGraphRAGAdapter(GraphRAGPort):
    """Traverse the Postgres-backed knowledge graph.

    Uses two traversal patterns:
    1. **Entity → Event** (1-hop): entity → event_entity_links → event_canonicals
    2. **Event → Timeline** (1-hop): event → event_timeline_chains → neighbor events
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def traverse(
        self,
        entities: list[dict],
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        if not entities:
            return []

        entity_names = [e["name"] for e in entities if e.get("name")]
        if not entity_names:
            return []

        try:
            params: dict = {"entity_names": entity_names, "limit": top_k}
            where_parts = ["e.name = ANY(:entity_names)"]
            if time_range:
                where_parts.append("ec.normalized_date >= :start_date")
                params["start_date"] = time_range.start.date()
                if time_range.end:
                    where_parts.append("ec.normalized_date <= :end_date")
                    params["end_date"] = time_range.end.date()
            where_sql = " AND ".join(where_parts)

            sql = (  # nosec B608
                text(f"""
                SELECT DISTINCT ON (ec.id)
                    ec.id, ec.title, ec.consensus_summary AS content,
                    ec.normalized_date AS event_date,
                    ec.observation_count, ec.importance_score,
                    e.name AS entity_name, e.type AS entity_type,
                    eel.relationship_type
                FROM event_canonicals ec
                JOIN event_entity_links eel ON ec.id = eel.event_id
                JOIN entities e ON eel.entity_id = e.id
                WHERE {where_sql}
                ORDER BY ec.id, ec.importance_score DESC
                LIMIT :limit
            """)
            )
            result = await self._session.execute(sql, params)
            rows = result.mappings().all()

            graph_results = []
            for row in rows:
                score = float(row.get("importance_score") or 0.5)
                entity_info = f" (liên quan: {row['entity_name']}"
                if row.get("entity_type"):
                    entity_info += f", loại: {row['entity_type']}"
                entity_info += ")"

                graph_results.append(
                    SearchResult(
                        content_id=f"graph-{row['id']}",
                        content_type="graph_event",
                        title=row["title"] or "",
                        content=f"{(row.get('content') or '').strip()}{entity_info}",
                        score=score,
                        metadata={
                            "event_canonical_id": str(row["id"]),
                            "event_date": str(row.get("event_date"))
                            if row.get("event_date")
                            else None,
                            "observation_count": row.get("observation_count"),
                            "importance_score": score,
                            "source": "knowledge_graph",
                        },
                    )
                )

            if graph_results:
                logger.debug(
                    "GraphRAG: %d events found for entities=%s", len(graph_results), entity_names
                )

            # ── Entity → Entity relations (64-predicate taxonomy) ──────
            # Surfaces explicit relationships between matched entities and
            # their neighbors (e.g. "FED tightens interest_rate",
            # "VCB belongs_to_sector banking"), which vector search cannot
            # capture. Read-only expansion — never modifies the graph.
            try:
                rel_where = ["(e1.name = ANY(:entity_names) OR e2.name = ANY(:entity_names))"]
                if time_range:
                    rel_where.append(
                        "(ec.normalized_date IS NULL"
                        " OR (ec.normalized_date >= :start_date"
                        + (" AND ec.normalized_date <= :end_date" if time_range.end else "")
                        + "))"
                    )
                rel_sql = text(
                    f"""
                    SELECT er.id, e1.name AS from_name, e1.type AS from_type,
                           e2.name AS to_name, e2.type AS to_type,
                           er.predicate, er.confidence, er.properties,
                           ec.title AS evidence_event
                    FROM entity_relations er
                    JOIN entities e1 ON er.from_entity_id = e1.id
                    JOIN entities e2 ON er.to_entity_id = e2.id
                    LEFT JOIN event_canonicals ec ON ec.id = er.source_event_id
                    WHERE {' AND '.join(rel_where)}
                    ORDER BY er.confidence DESC NULLS LAST
                    LIMIT :limit
                    """
                )
                rel_result = await self._session.execute(rel_sql, params)
                for row in rel_result.mappings().all():
                    from_name = row["from_name"] or ""
                    to_name = row["to_name"] or ""
                    predicate = row["predicate"] or ""
                    evidence = row.get("evidence_event")
                    rel_content = f"{from_name} {predicate} {to_name}"
                    if evidence:
                        rel_content += f" (bằng chứng: {evidence})"
                    graph_results.append(
                        SearchResult(
                            content_id=f"graph-rel-{row['id']}",
                            content_type="graph_relation",
                            title=rel_content,
                            content=rel_content,
                            score=float(row.get("confidence") or 0.4),
                            metadata={
                                "relation_id": str(row["id"]),
                                "from_entity": from_name,
                                "to_entity": to_name,
                                "predicate": predicate,
                                "source": "knowledge_graph_relations",
                            },
                        )
                    )
                logger.debug(
                    "GraphRAG: %d total graph results (events + relations) for entities=%s",
                    len(graph_results), entity_names,
                )
            except Exception:
                logger.debug("GraphRAG relation traversal failed, continuing with events", exc_info=True)

            return graph_results

        except Exception:
            logger.debug("GraphRAG traversal failed, returning empty")
            return []

    async def traverse_timeline(
        self,
        event_ids: list[str],
        max_hop: int = 2,
    ) -> list[SearchResult]:
        if not event_ids:
            return []

        try:
            # 1-hop: direct neighbors in timeline chain
            sql = text("""
                SELECT DISTINCT ec.id, ec.title, ec.consensus_summary AS content,
                    ec.normalized_date AS event_date,
                    ec.observation_count, ec.importance_score,
                    etc.relation_type
                FROM event_timeline_chains etc
                JOIN event_canonicals ec ON (
                    (etc.to_event_id = ec.id AND etc.from_event_id = ANY(:event_ids))
                    OR (etc.from_event_id = ec.id AND etc.to_event_id = ANY(:event_ids))
                )
                WHERE ec.id != ALL(:event_ids)
                ORDER BY ec.importance_score DESC
                LIMIT :limit
            """)
            result = await self._session.execute(
                sql,
                {
                    "event_ids": event_ids,
                    "limit": max_hop * 5,
                },
            )
            rows = result.mappings().all()

            timeline_results = []
            for row in rows:
                relation = row.get("relation_type", "liên quan")
                timeline_results.append(
                    SearchResult(
                        content_id=f"timeline-{row['id']}",
                        content_type="timeline_event",
                        title=row["title"] or "",
                        content=f"{(row.get('content') or '').strip()} (mối quan hệ: {relation})",
                        score=float(row.get("importance_score") or 0.4),
                        metadata={
                            "event_canonical_id": str(row["id"]),
                            "event_date": str(row.get("event_date"))
                            if row.get("event_date")
                            else None,
                            "relation_type": relation,
                            "source": "timeline_chain",
                        },
                    )
                )

            logger.debug(
                "Timeline traversal: %d neighbor events from %d seeds",
                len(timeline_results),
                len(event_ids),
            )
            return timeline_results

        except Exception:
            logger.debug("Timeline traversal failed, returning empty")
            return []
