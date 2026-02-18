from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApiAccount:
    id: int
    name: str
    base_url: str
    api_password_enc: str
    is_active: bool = True