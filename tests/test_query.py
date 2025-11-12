import asyncio
from app.services.rag.rag_service import RagService
from app.core.config import settings

async def test_query():
    index_name = settings.AZURE_SEARCH_INDEX_NAME
    query = "please show me the latest messages"
    top_k = 3

    results = RagService.query_index(
        query=query,
        index_name=index_name,
        embedding_model=settings.AZURE_SEARCH_DEPLOYMENT_NAME,
        top_k=top_k
    )

    print(results)

if __name__ == "__main__":
    asyncio.run(test_query())
