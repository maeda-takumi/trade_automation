from __future__ import annotations


def map_status_to_badge(status: object) -> tuple[str, str]:
    normalized = str(status or "").strip().upper()
    mapping = {
        "WAITING": ("待機", "neutral"),
        "UNSENT": ("未送信", "neutral"),
        "READY": ("準備完了", "neutral"),
        "NEW": ("新規", "info"),
        "WORKING": ("発注中", "info"),
        "PARTIAL": ("部分約定", "warning"),
        "FILLED": ("完了", "success"),
        "CANCELLED": ("取消", "neutral"),
        "ERROR": ("エラー", "danger"),
        "UNKNOWN": ("不明", "neutral"),
        "-": ("-", "neutral"),
    }
    return mapping.get(normalized, ("不明", "neutral"))
