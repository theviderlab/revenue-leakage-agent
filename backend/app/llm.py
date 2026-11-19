"""Chat model factory. The rest of the app does not know the provider."""

from langchain_openai import ChatOpenAI

from app.config import settings


def get_llm() -> ChatOpenAI:
    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY is empty: copy .env.example to .env at the project root and fill it in."
        )
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0,
    )
