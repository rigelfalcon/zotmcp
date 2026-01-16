"""Semantic search engine using ChromaDB and sentence transformers.

Provides:
- Vector embeddings for Zotero items
- Similarity search with configurable models
- Batched embedding generation
- Persistent storage
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Try to import optional dependencies
try:
    import chromadb
    from chromadb.config import Settings

    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False
    logger.warning("ChromaDB not installed. Semantic search unavailable.")

try:
    from sentence_transformers import SentenceTransformer

    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False
    logger.warning("sentence-transformers not installed. Semantic search unavailable.")


@dataclass
class SemanticResult:
    """Result from semantic search.

    Attributes:
        item_key: Zotero item key
        title: Item title
        similarity: Similarity score (0.0 to 1.0, higher is better)
        metadata: Additional metadata from the item
    """

    item_key: str
    title: str
    similarity: float
    metadata: dict[str, Any]

    def __str__(self) -> str:
        return f"{self.title} (similarity: {self.similarity:.3f})"


class SemanticEngine:
    """ChromaDB-based semantic search engine.

    Features:
    - Batched embedding generation (50 items per batch)
    - Lazy model loading
    - Persistent storage
    - Graceful degradation if dependencies unavailable

    Example:
        engine = SemanticEngine(
            model_name="all-MiniLM-L6-v2",
            persist_directory="./chroma_db"
        )
        await engine.initialize()

        # Index items
        await engine.update_embeddings(items)

        # Search
        results = await engine.search("machine learning papers", limit=10)
        for result in results:
            print(f"{result.title}: {result.similarity:.3f}")

        await engine.close()
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        persist_directory: str | Path | None = None,
        collection_name: str = "zotero_items",
        batch_size: int = 50,
    ) -> None:
        """Initialize semantic engine.

        Args:
            model_name: Sentence transformer model name (default: all-MiniLM-L6-v2)
            persist_directory: Directory for persistent storage (None = in-memory)
            collection_name: ChromaDB collection name
            batch_size: Items per embedding batch (default 50)
        """
        self.model_name = model_name
        self.persist_directory = Path(persist_directory) if persist_directory else None
        self.collection_name = collection_name
        self.batch_size = batch_size

        self._model: Optional[SentenceTransformer] = None
        self._client: Optional[chromadb.ClientAPI] = None
        self._collection: Optional[chromadb.Collection] = None
        self._initialized = False

    @property
    def available(self) -> bool:
        """Check if semantic search is available."""
        return HAS_CHROMADB and HAS_SENTENCE_TRANSFORMERS

    async def initialize(self) -> None:
        """Initialize ChromaDB and load model.

        Raises:
            RuntimeError: If dependencies not installed
        """
        if self._initialized:
            return

        if not self.available:
            raise RuntimeError(
                "Semantic search unavailable. Install: pip install chromadb sentence-transformers"
            )

        # Initialize ChromaDB client
        if self.persist_directory:
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            settings = Settings(
                persist_directory=str(self.persist_directory),
                anonymized_telemetry=False,
            )
            self._client = chromadb.PersistentClient(settings=settings)
            logger.info("ChromaDB initialized with persistence: %s", self.persist_directory)
        else:
            self._client = chromadb.Client()
            logger.info("ChromaDB initialized (in-memory)")

        # Get or create collection
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},  # Use cosine similarity
        )

        # Load model lazily (in thread pool to avoid blocking)
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(
            None, SentenceTransformer, self.model_name
        )
        logger.info("Loaded sentence transformer model: %s", self.model_name)

        self._initialized = True

    async def update_embeddings(
        self, items: list[dict[str, Any]], force: bool = False
    ) -> int:
        """Update embeddings for items.

        Args:
            items: List of items with 'key', 'title', 'abstract', etc.
            force: If True, re-embed existing items

        Returns:
            Number of items embedded

        Raises:
            RuntimeError: If not initialized
        """
        if not self._initialized:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        if not items:
            return 0

        # Filter items that need embedding
        if not force:
            existing_ids = set(self._collection.get()["ids"])
            items = [item for item in items if item["key"] not in existing_ids]

        if not items:
            logger.debug("No new items to embed")
            return 0

        logger.info("Embedding %d items in batches of %d", len(items), self.batch_size)

        embedded_count = 0
        loop = asyncio.get_event_loop()

        # Process in batches
        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]

            # Prepare texts for embedding
            texts = []
            for item in batch:
                # Combine title and abstract for better semantic representation
                text_parts = [item.get("title", "")]
                if item.get("abstract"):
                    text_parts.append(item["abstract"])
                texts.append(" ".join(text_parts))

            # Generate embeddings (in thread pool)
            def _encode():
                return self._model.encode(texts, show_progress_bar=False)
            embeddings = await loop.run_in_executor(None, _encode)

            # Prepare metadata
            ids = [item["key"] for item in batch]
            metadatas = [
                {
                    "title": item.get("title", "Untitled"),
                    "item_type": item.get("item_type", ""),
                    "date": item.get("date", ""),
                    "creators": str(item.get("creators", [])),
                }
                for item in batch
            ]

            # Add to collection
            self._collection.add(
                ids=ids,
                embeddings=embeddings.tolist(),
                documents=texts,
                metadatas=metadatas,
            )

            embedded_count += len(batch)
            logger.debug("Embedded batch %d-%d", i, i + len(batch))

        logger.info("Successfully embedded %d items", embedded_count)
        return embedded_count

    async def search(
        self, query: str, limit: int = 10, filter_metadata: dict[str, Any] | None = None
    ) -> list[SemanticResult]:
        """Search for similar items.

        Args:
            query: Search query text
            limit: Maximum results to return (default 10)
            filter_metadata: Optional metadata filters (e.g., {"item_type": "journalArticle"})

        Returns:
            List of SemanticResult ordered by similarity (highest first)

        Raises:
            RuntimeError: If not initialized
        """
        if not self._initialized:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        # Generate query embedding
        loop = asyncio.get_event_loop()
        def _encode_query():
            return self._model.encode([query], show_progress_bar=False)
        query_embedding = await loop.run_in_executor(None, _encode_query)

        # Search collection
        results = self._collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=limit,
            where=filter_metadata,
        )

        # Convert to SemanticResult objects
        semantic_results = []
        if results["ids"] and results["ids"][0]:
            for i, item_key in enumerate(results["ids"][0]):
                # ChromaDB returns distances, convert to similarity (1 - distance for cosine)
                distance = results["distances"][0][i]
                similarity = 1.0 - distance  # Cosine distance to similarity

                metadata = results["metadatas"][0][i]
                semantic_results.append(
                    SemanticResult(
                        item_key=item_key,
                        title=metadata.get("title", "Untitled"),
                        similarity=max(0.0, min(1.0, similarity)),  # Clamp to [0, 1]
                        metadata=metadata,
                    )
                )

        logger.debug("Found %d results for query: %s", len(semantic_results), query)
        return semantic_results

    async def delete_item(self, item_key: str) -> bool:
        """Delete an item from the index.

        Args:
            item_key: Zotero item key

        Returns:
            True if deleted, False if not found
        """
        if not self._initialized:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        try:
            self._collection.delete(ids=[item_key])
            logger.debug("Deleted item: %s", item_key)
            return True
        except Exception as e:
            logger.warning("Failed to delete item %s: %s", item_key, e)
            return False

    async def clear(self) -> None:
        """Clear all embeddings from the collection."""
        if not self._initialized:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        self._client.delete_collection(name=self.collection_name)
        self._collection = self._client.create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Cleared collection: %s", self.collection_name)

    def get_stats(self) -> dict[str, Any]:
        """Get collection statistics.

        Returns:
            Dictionary with stats (count, etc.)
        """
        if not self._initialized:
            return {"initialized": False, "count": 0}

        count = self._collection.count()
        return {
            "initialized": True,
            "count": count,
            "model": self.model_name,
            "collection": self.collection_name,
            "persistent": self.persist_directory is not None,
        }

    async def close(self) -> None:
        """Close the semantic engine and cleanup resources."""
        if self._initialized:
            # ChromaDB client doesn't need explicit closing
            self._client = None
            self._collection = None
            self._model = None
            self._initialized = False
            logger.debug("Semantic engine closed")

    async def __aenter__(self) -> "SemanticEngine":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
