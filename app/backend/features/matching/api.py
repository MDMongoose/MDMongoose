"""Matching: rule editor, runs, review queue, decisions — endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()

@router.get("/api/match-rules/{uid}")
def api_match_rule(uid: str, request: Request):
    """uid: a Match Rule or a Master Data node (its Match Rule)."""
    return svc().match_rule_editor(me(request), uid)


@router.put("/api/match-rules/{uid}")
def api_match_rule_save(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().save_match_rule(me(request), uid, body)


@router.post("/api/match-rules/{uid}/clean-preview")
def api_match_clean(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().match_clean_preview(me(request), uid, body)


@router.post("/api/match-rules/{uid}/test-pair")
def api_match_test_pair(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().match_test_pair(me(request), uid, body)


@router.post("/api/match-rules/{uid}/blocking")
def api_match_blocking(uid: str, request: Request, body: dict[str, Any] = Body(default={})):
    return svc().match_blocking(me(request), uid, body)


@router.post("/api/match-rules/{uid}/activate-model")
def api_match_activate(uid: str, request: Request):
    return svc().match_activate(me(request), uid)


@router.get("/api/matching/{uid}/rule")
def api_matching_rule(uid: str, request: Request):
    return svc().match_rule_for(me(request), uid)


@router.get("/api/matching/{uid}/review")
def api_match_review(uid: str, request: Request):
    return svc().match_review(me(request), uid)


@router.get("/api/matching/{uid}/details")
def api_match_details(uid: str, a: str, b: str, request: Request):
    return svc().match_details(me(request), uid, a, b)


@router.get("/api/matching/{uid}/record")
def api_match_record(uid: str, request: Request):
    return svc().record_matching(me(request), uid)


@router.get("/api/matching/{uid}/candidates")
def api_match_candidates(uid: str, request: Request, q: str = ""):
    return svc().match_candidates(me(request), uid, q)


@router.post("/api/matching/{uid}/decide")
def api_match_decide(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().match_decide(me(request), uid, body)


@router.post("/api/matching/{uid}/split")
def api_match_split(uid: str, request: Request, body: dict[str, Any] = Body(default={})):
    return svc().match_split(me(request), uid, body)


@router.post("/api/matching/{uid}/merge")
def api_match_merge(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().match_merge(me(request), uid, body)


@router.get("/api/matching/{uid}/log")
def api_match_log(uid: str, request: Request, limit: int = 20):
    return svc().match_log(me(request), uid, limit)
