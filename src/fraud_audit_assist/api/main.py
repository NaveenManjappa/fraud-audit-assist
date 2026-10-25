"""ASGI entry point: `uvicorn fraud_audit_assist.api.main:app`. Settings are read and validated at import."""

from fraud_audit_assist.api.app import create_app

app = create_app()
