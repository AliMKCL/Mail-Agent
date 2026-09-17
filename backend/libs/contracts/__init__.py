"""Pydantic v2 DTOs for the frozen cross-service HTTP contracts (Spec 3.2-3.7).

These DTOs are transport shapes only. Response *formatting* (``.isoformat()``,
``or "No Subject"``, ``or "Unknown"``, ``bool(...)``) stays in the handlers so
public JSON stays byte-identical (R7). Do not add serializers, aliases, or
validators here.
"""
