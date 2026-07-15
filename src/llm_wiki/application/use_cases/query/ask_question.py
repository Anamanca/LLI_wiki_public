from llm_wiki.application.dto.query_dto import QueryInput, QueryResult


class AskQuestionUseCase:
    def __init__(self, pipeline):
        self._pipeline = pipeline

    async def execute(self, input: QueryInput) -> QueryResult:
        result = await self._pipeline.execute(input)
        return QueryResult(
            answer=result["answer"],
            sources=result["sources"],
            tokens_used=result.get("tokens_used", 0),
            cache_hit=result.get("cache_hit", False),
            pipeline_steps=result.get("pipeline_steps", {}),
        )
