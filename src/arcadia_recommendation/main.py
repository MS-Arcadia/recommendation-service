from fastapi import FastAPI

from arcadia_recommendation.composition import build_container
from arcadia_recommendation.infrastructure.config.settings import get_settings
from arcadia_recommendation.presentation.http.app import create_app

app: FastAPI = create_app(build_container(get_settings()))
