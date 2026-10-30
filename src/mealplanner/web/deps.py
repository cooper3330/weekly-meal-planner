"""FastAPI dependencies."""

from fastapi import Request

from mealplanner.llm.client import LLMClient


def get_llm_client(request: Request) -> LLMClient:
    client: LLMClient = request.app.state.llm_client
    return client
