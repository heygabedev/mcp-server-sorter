from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from mcp_sorter.collection_drift import ApplyPreview, DriftPreview, apply_preview, preview_drift
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import (
    CollectionConflict,
    CollectionEdit,
    CollectionRevision,
    RevisionRequest,
    current_revision,
    deleted_selections,
    edit_selection,
    history,
    set_deleted,
)


def _response[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except CollectionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        code = 404 if str(exc) == "Collection not found" else 422
        raise HTTPException(code, str(exc)) from exc


def router(runtime: Callable[[], Runtime]) -> APIRouter:
    routes = APIRouter(prefix="/api/v1/collections")

    @routes.get("/deleted")
    def deleted() -> list[CollectionRevision]:
        return deleted_selections(runtime())

    @routes.get("/{identifier}")
    def detail(identifier: str) -> CollectionRevision:
        return _response(lambda: current_revision(runtime(), identifier))

    @routes.get("/{identifier}/history")
    def revisions(identifier: str) -> list[CollectionRevision]:
        return _response(lambda: history(runtime(), identifier))

    @routes.post("/{identifier}/edit")
    def edit(identifier: str, request: CollectionEdit) -> CollectionRevision:
        return _response(lambda: edit_selection(runtime(), identifier, request))

    @routes.post("/{identifier}/delete")
    def delete(identifier: str, request: RevisionRequest) -> CollectionRevision:
        return _response(
            lambda: set_deleted(runtime(), identifier, request.expected_revision, deleted=True)
        )

    @routes.post("/{identifier}/restore")
    def restore(identifier: str, request: RevisionRequest) -> CollectionRevision:
        return _response(
            lambda: set_deleted(runtime(), identifier, request.expected_revision, deleted=False)
        )

    @routes.get("/{identifier}/drift")
    def drift(identifier: str) -> DriftPreview:
        return _response(lambda: preview_drift(runtime(), identifier))

    @routes.post("/{identifier}/update-catalog")
    def update_catalog(identifier: str, request: ApplyPreview) -> CollectionRevision:
        return _response(lambda: apply_preview(runtime(), identifier, request))

    return routes
