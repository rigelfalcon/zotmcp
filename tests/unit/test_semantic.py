"""Unit tests for semantic search engine."""

import pytest
from zotmcp.semantic import SemanticEngine, SemanticResult


@pytest.mark.asyncio
@pytest.mark.skipif(
    not SemanticEngine(persist_directory=None).available,
    reason="Semantic search dependencies not installed"
)
async def test_semantic_engine_init():
    """Test semantic engine initialization."""
    engine = SemanticEngine(persist_directory=None)
    await engine.initialize()

    assert engine._initialized
    assert engine._model is not None
    assert engine._collection is not None

    await engine.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not SemanticEngine(persist_directory=None).available,
    reason="Semantic search dependencies not installed"
)
async def test_semantic_embedding():
    """Test embedding generation."""
    engine = SemanticEngine(persist_directory=None)
    await engine.initialize()

    items = [
        {
            "key": "TEST1",
            "title": "Machine Learning Basics",
            "abstract": "Introduction to ML algorithms",
            "item_type": "article",
            "date": "2024",
            "creators": [],
        },
        {
            "key": "TEST2",
            "title": "Deep Learning Tutorial",
            "abstract": "Neural networks explained",
            "item_type": "article",
            "date": "2024",
            "creators": [],
        },
    ]

    count = await engine.update_embeddings(items)
    assert count == 2

    stats = engine.get_stats()
    assert stats["count"] == 2

    await engine.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not SemanticEngine(persist_directory=None).available,
    reason="Semantic search dependencies not installed"
)
async def test_semantic_search():
    """Test semantic search."""
    engine = SemanticEngine(persist_directory=None)
    await engine.initialize()

    items = [
        {
            "key": "TEST1",
            "title": "Python Programming",
            "abstract": "Learn Python basics",
            "item_type": "book",
            "date": "2024",
            "creators": [],
        },
        {
            "key": "TEST2",
            "title": "JavaScript Guide",
            "abstract": "Web development with JS",
            "item_type": "book",
            "date": "2024",
            "creators": [],
        },
    ]

    await engine.update_embeddings(items)

    results = await engine.search("Python programming language", limit=2)
    assert len(results) > 0
    assert isinstance(results[0], SemanticResult)
    assert results[0].item_key in ["TEST1", "TEST2"]

    await engine.close()
