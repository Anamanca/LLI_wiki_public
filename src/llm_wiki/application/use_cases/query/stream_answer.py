from llm_wiki.application.dto.query_dto import QueryInput
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline


class StreamAnswerUseCase:
    def __init__(self, pipeline: QueryPipeline):
        self._pipeline = pipeline

    async def execute(self, input: QueryInput):
        async for chunk in self._pipeline.execute_stream(input):
            yield chunk
