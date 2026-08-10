from typing import Annotated

from fastapi import Depends, Request

from arcadia_recommendation.composition import Container, UseCases
from arcadia_recommendation.infrastructure.config.settings import Settings
from arcadia_recommendation.infrastructure.response_cache import ResponseCache


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_use_cases(container: Annotated[Container, Depends(get_container)]) -> UseCases:
    return container.use_cases


def get_settings(container: Annotated[Container, Depends(get_container)]) -> Settings:
    return container.settings


def get_response_cache(container: Annotated[Container, Depends(get_container)]) -> ResponseCache:
    return container.adapters.response_cache


ContainerDep = Annotated[Container, Depends(get_container)]
UseCasesDep = Annotated[UseCases, Depends(get_use_cases)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ResponseCacheDep = Annotated[ResponseCache, Depends(get_response_cache)]
