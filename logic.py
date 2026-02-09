# logic.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject

from ui_main import MainWindow


@dataclass
class ApiAccount:
    name: str
    base_url: str
    api_password_enc: str
    is_active: bool = True


class AppLogic(QObject):
    def __init__(self, window: MainWindow, db_path: str):
        super().__init__()
        self.window = window
        self.db_path = db_path

    def bind(self):
        w = self.window
        w.request_save_api.connect(self.save_api_account)
        w.request_load_api.connect(self.load_api_account)

        w.request_add_row.connect(self.add_order_row)
        w.request_remove_selected_rows.connect(self.remove_selected_rows)
        w.request_clear_orders.connect(self.clear_orders)
        w.request_submit_orders.connect(self.submit_orders_to_db)

    # ---------- DB ----------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # ---------- API SETTINGS ----------
    def save_api_account(self):
        w = self.window
        name = w.api_name.text().strip()
        base_url = w.api_base_url.text().strip()
        pw = w.api_password.text().strip()
        active = w.api_active.isChecked()

        if not name or not base_url or not pw:
            w.toast("入力不足", "API設定（名前/Base URL/パスワード）は必須です。", error=True)
            return

        # NOTE: ここでは “暗号化” は未実装。実運用ではOSキーチェーン or 何らかの暗号化を入れる。
        api = ApiAccount(name=name, base_url=base_url, api_password_enc=pw, is_active=active)

        try:
            with self._conn() as conn:
                # 既存の active を落としてから、最新を active にする運用（単独運用想定）
                conn.execute("UPDATE api_accounts SET is_active=0 WHERE is_active=1;")
                conn.execute(
                    """
                    INSERT INTO api_accounts (name, base_url, api_password_enc, is_active)
                    VALUES (?, ?, ?, ?)
                    """,
                    (api.name, api.base_url, api.api_password_enc, 1 if api.is_active else 0)
                )
            w.toast("保存完了", "API設定を保存しました。")
        except Exception as e:
            w.toast("保存失敗", f"DB保存に失敗: {e}", error=True)

    def load_api_account(self):
        w = self.window
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM api_accounts
                    ORDER BY is_active DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()

            if not row:
                w.toast("未登録", "API設定がまだ保存されていません。", error=True)
                return

            w.api_name.setText(row["name"] or "")
            w.api_base_url.setText(row["base_url"] or "")
            w.api_password.setText(row["api_password_enc"] or "")
            w.api_active.setChecked(bool(row["is_active"]))
            w.toast("読込完了", "API設定を読み込みました。")
        except Exception as e:
            w.toast("読込失敗", f"DB読込に失敗: {e}", error=True)

    # ---------- ORDER UI ----------
    def add_order_row(self):
        self.window.add_order_row(
            symbol="",
            exchange="1",
            product="cash",
            side="buy",
            qty=100,
            entry_type="market",
            entry_price=0.0,
            tp_price=0.0,
            sl_trigger=0.0,
            batch_name="手動バッチ",
            memo=""
        )

    def remove_selected_rows(self):
        self.window.remove_selected_rows()

    def clear_orders(self):
        self.window.clear_orders()
        self.window.toast("クリア", "注文行をすべてクリアしました。")

    # ---------- SUBMIT ORDERS ----------
    def submit_orders_to_db(self):
        w = self.window
        orders = w.get_orders_payload()

        # バリデーション（最低限）
        if not orders:
            w.toast("送信不可", "注文行がありません。", error=True)
            return

        bad = []
        for i, o in enumerate(orders, start=1):
            if not o["symbol"]:
                bad.append(f"{i}行目: 銘柄が空")
            if o["entry_type"] == "limit" and o["entry_price"] <= 0:
                bad.append(f"{i}行目: 指値なのに指値価格が0")
            if o["qty"] <= 0:
                bad.append(f"{i}行目: 数量が0以下")

        if bad:
            w.toast("入力エラー", " / ".join(bad), error=True)
            return

        # api_account を取得（有効→最新）
        api_account_id = self._get_active_api_account_id()
        if not api_account_id:
            w.toast("API未設定", "先にAPI設定を保存してください。", error=True)
            return

        # バッチ作成：batch_code は “YYYYMMDD-HHMMSS”
        now = datetime.now()
        batch_code = now.strftime("%Y%m%d-%H%M%S")
        batch_name = orders[0].get("batch_name") or "手動バッチ"

        try:
            with self._conn() as conn:
                # batch_jobs
                cur = conn.execute(
                    """
                    INSERT INTO batch_jobs (batch_code, api_account_id, name, status, run_mode, eod_close_time, eod_force_close)
                    VALUES (?, ?, ?, 'SCHEDULED', 'immediate', '14:30', 1)
                    """,
                    (batch_code, api_account_id, batch_name)
                )
                batch_job_id = cur.lastrowid

                # batch_items 一括
                for o in orders:
                    conn.execute(
                        """
                        INSERT INTO batch_items
                        (batch_job_id, symbol, exchange, product, side, qty, entry_type, entry_price,
                         tp_price, sl_trigger_price, status, last_error)
                        VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'READY', NULL)
                        """,
                        (
                            batch_job_id,
                            o["symbol"],
                            int(o["exchange"]),
                            o["product"],
                            o["side"],
                            int(o["qty"]),
                            o["entry_type"],
                            float(o["entry_price"]) if o["entry_type"] == "limit" else None,
                            float(o["tp_price"]),
                            float(o["sl_trigger_price"]),
                        )
                    )

                # event_logs
                conn.execute(
                    """
                    INSERT INTO event_logs (batch_job_id, level, event_type, message)
                    VALUES (?, 'INFO', 'BATCH_CREATED', ?)
                    """,
                    (batch_job_id, f"Batch created: {batch_code} / {batch_name} / items={len(orders)}")
                )

            w.toast("送信完了", f"バッチを作成しDBに保存しました。（items={len(orders)}）")
        except Exception as e:
            w.toast("送信失敗", f"DB保存に失敗: {e}", error=True)

    def _get_active_api_account_id(self) -> Optional[int]:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT id FROM api_accounts
                    WHERE is_active = 1
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
            return int(row["id"]) if row else None
        except Exception:
            return None
