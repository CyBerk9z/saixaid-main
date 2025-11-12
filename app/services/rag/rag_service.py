import json
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration,
    HnswParameters
)
import asyncio
import hashlib
from app.core.config import settings
from app.core.logging import get_logger
from app.core.exceptions import AppException, ErrorCode
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from app.services.azure.blob import download_csv_from_blob, preprocess_and_chunk_data
from app.services.azure.openai import get_azure_openai_client
from app.utils.db_client import tenant_client_context_by_company_id
from app.services.company.company_service import CompanyService
logger = get_logger(__name__)

class RagService:
    @staticmethod
    async def create_embeddings(text_chunks, model):
        client = get_azure_openai_client()

        embeddings = []
        for chunk in text_chunks:
            try:
                response = await asyncio.to_thread(
                client.embeddings.create,
                input=chunk, 
                model=model
                )
                embeddings.append(response.data[0].embedding)
            except Exception as e:
                logger.error("Failed to create embedding", extra={"error": str(e)}, exc_info=True)
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message="Failed to create embedding",
                    context={"error": str(e)}
                )
        return embeddings
    
    @staticmethod # blobに直接アクセスする場合
    async def build_index_from_blob(blob_url: str, index_name: str, connection_string= settings.AZURE_STORAGE_CONNECTION_STRING):
        embedding_model = settings.AZURE_SEARCH_DEPLOYMENT_NAME
        try:
            index_name = "saixgen_index"
            # BlobからCSVを取得
            try:
                df = download_csv_from_blob(blob_url, connection_string)
            except Exception as e:
                logger.error(f"Failed to download CSV from blob: {str(e)}", exc_info=True)
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message="Failed to download CSV from blob",
                    context={"error": str(e)}
                )

            # 前処理とチャンク化
            try:
                text_chunks = preprocess_and_chunk_data(df, time_window_minutes=5, target_chunk_tokens=1000)
            except Exception as e:
                logger.error(f"Failed to preprocess and chunk data: {str(e)}", exc_info=True)
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message="Failed to preprocess and chunk data",
                    context={"error": str(e)}
                )

            # Embedding生成
            try:
                embeddings = await RagService.create_embeddings(text_chunks, embedding_model)
            except Exception as e:
                logger.error(f"Failed to create embeddings: {str(e)}", exc_info=True)
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message="Failed to create embeddings",
                    context={"error": str(e)}
                )

            # Azure AI Searchへ登録
            try:
                # インデックスの存在確認と作成
                admin_client = SearchIndexClient(
                    endpoint=settings.AZURE_SEARCH_SERVICE_ENDPOINT,
                    credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY)
                )

                # Indexの存在確認と新規作成（存在しない場合）
                if index_name not in list(admin_client.list_index_names()):
                    fields = [
                        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                        SimpleField(
                            name="content",
                            type=SearchFieldDataType.String,
                            searchable=True,
                            filterable=True,
                            sortable=True,
                            facetable=True
                        ),
                        SearchField(
                            name="contentVector",
                            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                            searchable=True,
                            vector_search_dimensions=1536,
                            vector_search_profile_name="my-vector-profile"
                        )
                    ]

                    vector_search = VectorSearch(
                        algorithms=[
                            HnswAlgorithmConfiguration(
                                name="my-vector-algorithm",
                                parameters=HnswParameters(
                                    metric="cosine",
                                    m=4,
                                    ef_construction=400,
                                    ef_search=500
                                )
                            )
                        ],
                        profiles=[
                            VectorSearchProfile(
                                name="my-vector-profile",
                                algorithm_configuration_name="my-vector-algorithm"
                            )
                        ]
                    )

                    index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
                    admin_client.create_index(index)
                    logger.info(f"Created new index: {index_name}")

                search_client = SearchClient(
                    endpoint=settings.AZURE_SEARCH_SERVICE_ENDPOINT,
                    index_name=index_name,
                    credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY)
                )

                # blob_urlをハッシュ化して短い識別子にする
                blob_identifier = hashlib.md5(blob_url.encode()).hexdigest()[:8]

                # ドキュメントの形式を修正
                documents = []
                for i, (chunk, embedding) in enumerate(zip(text_chunks, embeddings)):
                    # embeddingが配列の場合は文字列に変換
                    if isinstance(embedding, list):
                        embedding = [float(x) for x in embedding]
                    
                    # Azure AI Searchの形式に合わせてドキュメントを作成
                    doc = {
                        "id": f"{index_name}_{blob_identifier}_{i}",
                        "content": str(chunk),  # 文字列に変換
                        "contentVector": embedding if isinstance(embedding, list) else [float(x) for x in embedding]
                    }
                    documents.append(doc)

                # ドキュメントをアップロード
                result = search_client.upload_documents(documents)
                logger.info(f"Uploaded {len(documents)} documents to Azure AI Search")

            except Exception as e:
                logger.error(f"Failed to upload documents to Azure AI Search: {str(e)}", exc_info=True)
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message="Failed to upload documents to Azure AI Search",
                    context={"error": str(e)}
                )

            return {"indexSize": len(documents)}

        except AppException:
            raise
        except Exception as e:
            logger.error("Failed to build index from blob", extra={"error": str(e)}, exc_info=True)
            raise AppException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message="Failed to build index from blob",
                context={"error": str(e)}
            )

    @staticmethod # postgreからblobにアクセスする場合
    async def build_index(file_id: str, company_id: str):
        base_index_name = settings.AZURE_SEARCH_INDEX_NAME
        embedding_model = settings.AZURE_SEARCH_DEPLOYMENT_NAME

        # CompanyごとのIndex名を作成
        actual_index_name = f"{base_index_name}-{company_id.lower()}"
        logger.info(f"Starting build_index for file_id: {file_id}, company_id: {company_id}")
        logger.info(f"Using actual index name: {actual_index_name}")

        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            try:
                # PostgreSQLからblob_urlを取得
                logger.info("Fetching CSV file record from database...")
                csv_file_record = await tenant_client.csvfile.find_first(where={"id": file_id})
                if not csv_file_record:
                    error_msg = f"CSV file record not found for file_id: {file_id}"
                    logger.error(error_msg)
                    raise AppException(
                        error_code=ErrorCode.NOT_FOUND,
                        message=error_msg,
                        context={"file_id": file_id}
                    )
                
                blob_url = csv_file_record.blobUrl
                logger.info(f"Found blob_url: {blob_url}")

                # BlobからCSVを取得
                logger.info("Downloading CSV from blob...")
                try:
                    df = download_csv_from_blob(blob_url, settings.AZURE_STORAGE_CONNECTION_STRING)
                    logger.info(f"Successfully downloaded CSV with {len(df)} rows")
                except Exception as e:
                    error_msg = f"Failed to download CSV from blob: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    raise AppException(
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                        message=error_msg,
                        context={"error": str(e), "blob_url": blob_url}
                    )

                # 前処理とチャンク化
                logger.info("Preprocessing and chunking data...")
                try:
                    text_chunks = preprocess_and_chunk_data(df, time_window_minutes=5, target_chunk_tokens=1000)
                    logger.info(f"Created {len(text_chunks)} chunks from data")
                except Exception as e:
                    error_msg = f"Failed to preprocess and chunk data: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    raise AppException(
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                        message=error_msg,
                        context={"error": str(e)}
                    )

                # Embedding生成
                logger.info("Generating embeddings...")
                try:
                    embeddings = await RagService.create_embeddings(text_chunks, embedding_model)
                    logger.info(f"Generated {len(embeddings)} embeddings")
                except Exception as e:
                    error_msg = f"Failed to create embeddings: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    raise AppException(
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                        message=error_msg,
                        context={"error": str(e)}
                    )
                
                # Azure AI Searchへ登録
                logger.info("Initializing Azure AI Search clients...")
                try:
                    endpoint = settings.AZURE_SEARCH_SERVICE_ENDPOINT
                    credential = AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY)

                    # クライアントを初期化（シンプルな設定で）
                    admin_client = SearchIndexClient(
                        endpoint=endpoint,
                        credential=credential,
                        logging_enable=False  # ロギングを無効化
                    )
                    logger.info("Successfully initialized Azure AI Search admin client")

                    # エイリアスを使用してインデックス名を解決
                    try:
                        index = admin_client.get_index(base_index_name)
                        actual_index_name = index.name
                        logger.info(f"Resolved index name from alias: {actual_index_name}")
                    except Exception as e:
                        logger.info(f"No existing alias found, using actual index name: {actual_index_name}")
                        actual_index_name = f"{base_index_name}-{company_id.lower()}"

                    # インデックスの存在確認
                    existing_indexes = list(admin_client.list_index_names())
                    logger.info(f"Existing indexes: {existing_indexes}")

                    target_index_name = None
                    is_new_index = False

                    # 実際のインデックス名が存在するか確認
                    if actual_index_name in existing_indexes:
                        logger.info(f"Found existing index: {actual_index_name}")
                        target_index_name = actual_index_name
                    else:
                        # インデックスが見つからない場合は新規作成
                        logger.info(f"No existing index found, creating new index: {actual_index_name}")
                        fields = [
                            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                            SimpleField(
                                name="content",
                                type=SearchFieldDataType.String,
                                searchable=True,
                                filterable=True,
                                sortable=True,
                                facetable=True
                            ),
                            SearchField(
                                name="contentVector",
                                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                                searchable=True,
                                vector_search_dimensions=1536,
                                vector_search_profile_name="my-vector-profile"
                            )
                        ]

                        vector_search = VectorSearch(
                            algorithms=[
                                HnswAlgorithmConfiguration(
                                    name="my-vector-algorithm",
                                    parameters=HnswParameters(
                                        metric="cosine",
                                        m=4,
                                        ef_construction=400,
                                        ef_search=500
                                    )
                                )
                            ],
                            profiles=[
                                VectorSearchProfile(
                                    name="my-vector-profile",
                                    algorithm_configuration_name="my-vector-algorithm"
                                )
                            ]
                        )

                        index = SearchIndex(name=actual_index_name, fields=fields, vector_search=vector_search)
                        admin_client.create_index(index)
                        logger.info(f"Successfully created new index: {actual_index_name}")
                        target_index_name = actual_index_name
                        is_new_index = True

                        # 新規インデックス作成時にエイリアスを設定
                        try:
                            # 既存のエイリアスを削除（存在する場合）
                            try:
                                admin_client.delete_index(base_index_name)
                                logger.info(f"Deleted existing index/alias: {base_index_name}")
                            except Exception as e:
                                logger.info(f"No existing index/alias to delete: {base_index_name}")

                            # 新しいインデックスをエイリアスとして設定
                            admin_client.create_index(
                                SearchIndex(
                                    name=base_index_name,
                                    fields=fields,
                                    vector_search=vector_search
                                )
                            )
                            logger.info(f"Created alias {base_index_name} pointing to {target_index_name}")
                        except Exception as e:
                            error_msg = f"Failed to manage index alias: {str(e)}"
                            logger.error(error_msg, exc_info=True)
                            # エイリアス設定の失敗は致命的ではないので、ログのみに記録

                except Exception as e:
                    error_msg = f"Failed to manage indexes: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    raise AppException(
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                        message=error_msg,
                        context={"error": str(e)}
                    )

                # ドキュメントのアップロード
                logger.info("Preparing documents for upload...")
                try:
                    # 対象のインデックス名を使用してSearchClientを初期化
                    search_client = SearchClient(
                        endpoint=endpoint,
                        index_name=target_index_name,
                        credential=credential,
                        logging_enable=False  # ロギングを無効化
                    )

                    documents = [
                        {"id": f"{file_id}_{i}", "content": chunk, "contentVector": embedding}
                        for i, (chunk, embedding) in enumerate(zip(text_chunks, embeddings))
                    ]
                    logger.info(f"Prepared {len(documents)} documents for upload")

                    logger.info("Uploading documents to Azure AI Search...")
                    result = search_client.upload_documents(documents)
                    logger.info(f"Successfully uploaded documents: {result}")
                except Exception as e:
                    error_msg = f"Failed to upload documents: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    raise AppException(
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                        message=error_msg,
                        context={"error": str(e), "document_count": len(documents)}
                    )

                # ステータス更新
                logger.info("Updating CSV file status...")
                try:
                    await tenant_client.csvfile.update(where={"id": file_id}, data={"status": "indexed"})
                    logger.info(f"Successfully updated status for file_id: {file_id}")
                except Exception as e:
                    error_msg = f"Failed to update CSV file status: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    # ステータス更新の失敗は致命的ではないので、ログのみに記録

                logger.info(f"Successfully completed build_index for file_id: {file_id}")
                return {"indexSize": len(documents)}

            except AppException:
                raise
            except Exception as e:
                error_msg = f"Unexpected error in build_index: {str(e)}"
                logger.error(error_msg, exc_info=True)
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message=error_msg,
                    context={"error": str(e), "file_id": file_id, "company_id": company_id}
                )

    @staticmethod
    async def expand_query(query: str) -> str:
        client = get_azure_openai_client()
        try:
            prompt = f"""
            以下の短いクエリを、類義語や関連するキーワードを含めて検索に適した自然な文章に書き換えてください。

            クエリ: "{query}"

            書き換え後のクエリ:
            """

            completion = await asyncio.to_thread(
                client.chat.completions.create,
                model=settings.AZURE_OPENAI_API_DEPLOYMENT_NAME,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
                temperature=0.2
            )

            expanded_query = completion.choices[0].message.content.strip()
            logger.info(f"Expanded query: '{query}' → '{expanded_query}'")
            return expanded_query
        except Exception as e:
            logger.error("Failed to expand query", extra={"error": str(e)}, exc_info=True)
            return query  # 拡張失敗時は元のクエリを使用
        
    @staticmethod
    async def rerank_documents(query: str, documents: list) -> list:
        client = get_azure_openai_client()
        reranked_results = []

        for doc in documents:
            prompt = f"""
            以下のドキュメントが、質問に回答するためにどの程度役に立つかを1〜10で評価してください。

            質問: {query}

            ドキュメント:
            {doc['contentSnippet']}

            評価 (数値のみ):
            """

            try:
                completion = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=settings.AZURE_OPENAI_API_DEPLOYMENT_NAME,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=3,
                    temperature=0.0
                )

                score_text = completion.choices[0].message.content.strip()
                relevance_score = float(score_text)
            except Exception as e:
                logger.error("Failed to rerank document", extra={"error": str(e)}, exc_info=True)
                relevance_score = 0.0

            reranked_results.append({
                "documentId": doc["documentId"],
                "originalScore": doc["relevanceScore"],
                "rerankScore": relevance_score,
                "contentSnippet": doc["contentSnippet"]
            })

        reranked_results.sort(key=lambda x: x["rerankScore"], reverse=True)

        return reranked_results


    @staticmethod
    async def query_index(query: str, company_id: str, top_k: int):
        logger.info("=== Starting RAG Query ===")
        logger.info(f"Query: {query}")
        logger.info(f"Company ID: {company_id}")
        logger.info(f"Top K: {top_k}")

        client = get_azure_openai_client()
        embedding_model = settings.AZURE_SEARCH_DEPLOYMENT_NAME

        logger.info("Creating embeddings for query...")
        embeddings = await RagService.create_embeddings([query], embedding_model)
        query_embedding = embeddings[0]
        logger.info("Embeddings created successfully")

        base_index_name = settings.AZURE_SEARCH_INDEX_NAME
        # index_name = f"{base_index_name}-{company_id.lower()}"
        index_name = base_index_name
        logger.info(f"Using index name: {index_name}")

        # エイリアスを使用するためにSearchIndexClientを初期化
        admin_client = SearchIndexClient(
            endpoint=settings.AZURE_SEARCH_SERVICE_ENDPOINT,
            credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY),
            logging_enable=False  # ロギングを無効化
        )

        # エイリアスを解決して実際のインデックス名を取得
        try:
            index = admin_client.get_index(index_name)
            actual_index_name = index.name
            logger.info(f"Resolved index name: {actual_index_name}")
        except Exception as e:
            logger.error(f"Failed to resolve index alias: {str(e)}")
            raise AppException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message="Failed to resolve index alias",
                context={"error": str(e)}
            )

        # 実際のインデックス名を使用してSearchClientを初期化
        search_client = SearchClient(
            endpoint=settings.AZURE_SEARCH_SERVICE_ENDPOINT,
            index_name=actual_index_name,
            credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY),
            logging_enable=False  # ロギングを無効化
        )

        logger.info("Performing vector search...")
        results = await asyncio.to_thread(
            search_client.search,
            search_text=None,
            vector_queries=[
                {
                    "kind": "vector",
                    "vector": query_embedding,
                    "k": top_k,
                    "fields": "contentVector"
                }
            ],
            select=["id", "content"],
            top=top_k,
        )
        if results is None:
            logger.warning("No results found")

        source_documents = [
            {
                "documentId": result["id"],
                "relevanceScore": result["@search.score"],
                "contentSnippet": result["content"]
            }
            for result in results
        ]
        logger.info(f"Processed {len(source_documents)} source documents")

        # ★ ここで再ランク付けを実行
        logger.info("Starting document reranking...")
        reranked_documents = await RagService.rerank_documents(query, source_documents)
        logger.info(f"Reranking completed. Reranked {len(reranked_documents)} documents")

        context_texts = "\n".join([doc["contentSnippet"] for doc in reranked_documents])
        logger.info("Generated context texts for completion, context_texts: ", context_texts)

        logger.info("Generating completion with OpenAI...")
        system_prompt = await CompanyService.get_prompt_template(company_id)
        chat_completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.AZURE_OPENAI_API_DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": 
                """
                あなたはFAQチャットボットです。以下の情報を元に、可能な範囲で正確に質問に回答してください。

                部分的にでも情報があれば、それに基づいて回答してください.
                コンテクストは「タイムスタンプ」「ユーザーId」「ユーザー名」「チャンネル
                」「メッセージ」「添付ファイル」の順番で表示されます。
                """
                },
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": context_texts},
                {"role": "user", "content": query}
            ]
        )
        answer = chat_completion.choices[0].message.content.strip()
        logger.info("Completion generated successfully")

        scores = [doc["relevanceScore"] for doc in source_documents]
        logger.info("=== RAG Query Completed ===")

        return {
            "answer": answer,
            "scores" : scores,
            "sourceDocuments": source_documents,
            "expandedQuery": query 
        }
    
    @staticmethod
    async def delete_index(company_id: str):
        base_index_name = settings.AZURE_SEARCH_INDEX_NAME
        index_name = f"{base_index_name}-{company_id.lower()}"

        endpoint = settings.AZURE_SEARCH_SERVICE_ENDPOINT
        credential = AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY)

        admin_client = SearchIndexClient(endpoint=endpoint, credential=credential)

        try:
            # Azure AI Searchからインデックス削除
            admin_client.delete_index(index_name)
            logger.info(f"Index '{index_name}' successfully deleted from Azure AI Search.")
        except Exception as e:
            logger.error(f"Failed to delete index '{index_name}' from Azure AI Search", extra={"error": str(e)}, exc_info=True)
            raise AppException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message=f"Failed to delete index '{index_name}' from Azure AI Search",
                context={"error": str(e)}
            )
        
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            try:
                # company_idに紐づくすべてのCSVファイルレコードのステータスを一括更新
                await tenant_client.csvfile.update_many(
                    where={"companyId": company_id},
                    data={"status": "uploaded"}
                )
                logger.info(f"All CSV file records for company '{company_id}' successfully updated.")
            except Exception as e:
                logger.error(
                    f"Failed to update CSV file records for company '{company_id}'",
                    extra={"error": str(e)},
                    exc_info=True
                )
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message=f"Failed to update CSV file records for company '{company_id}'",
                    context={"error": str(e)}
                )

