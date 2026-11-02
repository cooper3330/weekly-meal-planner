"""FastAPI dependencies."""

import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import Request

from mealplanner.db import connect
from mealplanner.llm.client import LLMClient
from mealplanner.nutrition.catalog import Catalog


def get_llm_client(request: Request) -> LLMClient:
    client: LLMClient = request.app.state.llm_client
    return client


async def get_db(request: Request) -> AsyncIterator[sqlite3.Connection]:
    """One connection per request, closed afterwards.

    Async so the connection is made and used on the event loop thread (sqlite3 objects are
    bound to their creating thread); the queries are short and local.
    """
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_catalog(request: Request) -> Catalog:
    catalog: Catalog = request.app.state.catalog
    return catalog


def get_now(request: Request) -> datetime:
    now: datetime = request.app.state.clock()
    return now
