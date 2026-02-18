from __future__ import annotations

import sqlite3
from typing import Optional


class AppUiMixin:
    @staticmethod
    def _render_order_status(status: Optional[str], fallback_waiting: str = "WAITING") -> str:
        if not status:
            return fallback_waiting
        normalized = str(status).strip().upper()
        known = {"NEW", "WORKING", "PARTIAL", "FILLED", "CANCELLED", "ERROR", "UNKNOWN", "WAITING"}
        return normalized if normalized in known else "UNKNOWN"

    def _refresh_execution_status_ui(self) -> None:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT bi.id,
                       bi.symbol,
                       bi.side,
                       bi.qty,
                       bi.status AS item_status,
                       bi.last_error,
                       bi.entry_filled_qty,
                       bi.closed_qty,
                       bj.run_mode,
                       bj.status AS job_status,
                       oe.status AS entry_order_status,
                       oe.sent_at AS entry_sent_at,
                       oe.avg_price AS entry_avg_price,
                       oe.cum_qty AS entry_cum_qty,
                       otp.status AS tp_order_status,
                       otp.sent_at AS tp_sent_at,
                       otp.avg_price AS tp_avg_price,
                       otp.cum_qty AS tp_cum_qty,
                       osl.status AS sl_order_status,
                       osl.sent_at AS sl_sent_at,
                       osl.avg_price AS sl_avg_price,
                       osl.cum_qty AS sl_cum_qty
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                LEFT JOIN orders oe ON oe.api_order_id = bi.entry_order_id
                LEFT JOIN orders otp ON otp.api_order_id = bi.tp_order_id
                LEFT JOIN orders osl ON osl.api_order_id = bi.sl_order_id
                WHERE bj.status IN ('SCHEDULED', 'RUNNING')
                  AND bi.status != 'CLOSED'
                ORDER BY bi.updated_at DESC, bi.id DESC
                """
            ).fetchall()

        if not rows:
            self.window.set_execution_status("監視対象なし", "WAITING", "WAITING", "WAITING")
            self.window.set_open_order_cards([])
            return

        cards: list[dict] = []
        def _fmt_sent_at(value: object) -> str:
            return str(value) if value else "-"

        def _fmt_amount(avg: object, qty: object) -> str:
            if avg is None or not qty:
                return "-"
            try:
                amount = float(avg) * int(qty)
            except Exception:
                return "-"
            return f"{amount:,.0f}円"
        for row in rows:
            item_status = str(row["item_status"] or "")
            entry_status = self._render_order_status(row["entry_order_status"], fallback_waiting="UNSENT")
            tp_status = self._render_order_status(row["tp_order_status"], fallback_waiting="WAITING")
            sl_status = self._render_order_status(row["sl_order_status"], fallback_waiting="WAITING")

            if item_status in {"READY", "ENTRY_SENT", "ENTRY_PARTIAL", "ENTRY_FILLED", "ENTRY_FILLED_WAIT_PRICE"}:
                if item_status == "READY":
                    entry_status = "READY"
                if item_status == "ENTRY_FILLED_WAIT_PRICE":
                    tp_status = "WAIT_PRICE"
                    sl_status = "WAIT_PRICE"
                else:
                    tp_status = "WAITING"
                    sl_status = "WAITING"

            if item_status == "BRACKET_SENT":
                tp_status = self._render_order_status(row["tp_order_status"], fallback_waiting="NEW")
                sl_status = self._render_order_status(row["sl_order_status"], fallback_waiting="NEW")

            if item_status == "ERROR":
                entry_status = "ERROR"
                tp_status = "ERROR"
                sl_status = "ERROR"
                
            cards.append({
                "id": row["id"],
                "symbol": row["symbol"],
                "side_label": "買" if row["side"] == "buy" else "売",
                "qty": int(row["qty"] or 0),
                "item_status_label": item_status,
                "entry_status_label": entry_status,
                "tp_status_label": tp_status,
                "sl_status_label": sl_status,
                "entry_filled_qty": int(row["entry_filled_qty"] or 0),
                "closed_qty": int(row["closed_qty"] or 0),
                "entry_sent_at": _fmt_sent_at(row["entry_sent_at"]),
                "tp_sent_at": _fmt_sent_at(row["tp_sent_at"]),
                "sl_sent_at": _fmt_sent_at(row["sl_sent_at"]),
                "entry_fill_amount_text": _fmt_amount(row["entry_avg_price"], row["entry_cum_qty"]),
                "tp_fill_amount_text": _fmt_amount(row["tp_avg_price"], row["tp_cum_qty"]),
                "sl_fill_amount_text": _fmt_amount(row["sl_avg_price"], row["sl_cum_qty"]),
                "can_manual_close": item_status in {"ENTRY_PARTIAL", "ENTRY_FILLED", "ENTRY_FILLED_WAIT_PRICE", "BRACKET_SENT", "EOD_MARKET_SENT"},
                "can_cancel_scheduled": row["run_mode"] == "scheduled" and row["job_status"] == "SCHEDULED" and item_status == "READY",
                "last_error": row["last_error"] or "",
            })

        latest = rows[0]
        target = f"#{latest['id']} {latest['symbol']}"
        if str(latest["item_status"] or "") == "ERROR":
            self.window.set_execution_status(target, "ERROR", "-", "-")
        else:
            self.window.set_execution_status(
                target,
                cards[0]["entry_status_label"],
                cards[0]["tp_status_label"],
                cards[0]["sl_status_label"],
            )

        self.window.set_open_order_cards(cards)

    def _notify_new_item_errors(self) -> None:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT bi.id,
                       bi.symbol,
                       bi.last_error,
                       bi.updated_at,
                       bj.id AS batch_job_id,
                       bj.status AS batch_status,
                       bj.run_mode
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                WHERE bi.status='ERROR'
                  AND COALESCE(TRIM(bi.last_error), '') != ''
                ORDER BY bi.updated_at DESC, bi.id DESC
                LIMIT 20
                """
            ).fetchall()

        fresh_rows: list[sqlite3.Row] = []
        active_keys: set[str] = set()
        for row in rows:
            key = f"{int(row['id'])}:{row['updated_at']}"
            active_keys.add(key)
            if key in self._notified_error_keys:
                continue
            fresh_rows.append(row)
            self._notified_error_keys.add(key)

        if self._notified_error_keys:
            self._notified_error_keys.intersection_update(active_keys)

        if not fresh_rows:
            return

        lines = []
        for row in fresh_rows[:3]:
            lines.append(
                f"・注文#{int(row['id'])} ({row['symbol']}) / バッチ#{int(row['batch_job_id'])} [{row['batch_status']}, {row['run_mode']}]: {str(row['last_error']).splitlines()[0]}"
            )
        remaining = len(fresh_rows) - len(lines)
        if remaining > 0:
            lines.append(f"…ほか {remaining} 件")

        message = "注文処理で発注エラーを検出しました（予約キャンセル失敗ではありません）。\n" + "\n".join(lines)
        self.window.toast("注文処理エラー", message, error=True)


    def _prime_notified_error_keys(self) -> None:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT bi.id,
                       bi.updated_at
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                WHERE bi.status='ERROR'
                  AND COALESCE(TRIM(bi.last_error), '') != ''
                """
            ).fetchall()

        for row in rows:
            self._notified_error_keys.add(f"{int(row['id'])}:{row['updated_at']}")
            
