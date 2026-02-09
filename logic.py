# logic.py
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
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
        self._api_token: Optional[str] = None
        self._api_token_base_url: Optional[str] = None
        self._init_db()

    def bind(self):
        w = self.window
        w.request_save_api.connect(self.save_api_account)
        w.request_load_api.connect(self.load_api_account)
        w.request_symbol_lookup.connect(self.fetch_symbol_name)

        w.request_clear_orders.connect(self.clear_orders)
        w.request_submit_orders.connect(self.submit_orders_to_db)

    # ---------- DB ----------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_password_enc TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_code TEXT NOT NULL,
                    api_account_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    run_mode TEXT NOT NULL,
                    scheduled_at DATETIME,
                    eod_close_time TEXT NOT NULL,
                    eod_force_close INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_account_id) REFERENCES api_accounts(id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_job_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    exchange INTEGER NOT NULL,
                    product TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    entry_type TEXT NOT NULL,
                    entry_price REAL,
                    tp_price REAL,
                    sl_trigger_price REAL,
                    status TEXT NOT NULL,
                    last_error TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (batch_job_id) REFERENCES batch_jobs(id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_job_id INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (batch_job_id) REFERENCES batch_jobs(id)
                );
                """
            )

    def _get_active_api_account(self) -> Optional[ApiAccount]:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT name, base_url, api_password_enc, is_active
                    FROM api_accounts
                    WHERE is_active = 1
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
            if not row:
                return None
            return ApiAccount(
                name=row["name"],
                base_url=row["base_url"],
                api_password_enc=row["api_password_enc"],
                is_active=bool(row["is_active"]),
            )
        except Exception:
            return None

    def _request_json(self, method: str, url: str, headers: Optional[dict] = None, payload: Optional[dict] = None):
        data = None
        request_headers = headers or {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers = {"Content-Type": "application/json", **request_headers}
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get_api_token(self, api: ApiAccount) -> Optional[str]:
        base_url = api.base_url.rstrip("/")
        if self._api_token and self._api_token_base_url == base_url:
            return self._api_token
        try:
            data = self._request_json(
                "POST",
                f"{base_url}/token",
                payload={"APIPassword": api.api_password_enc},
            )
            token = data.get("Token")
            if token:
                self._api_token = token
                self._api_token_base_url = base_url
            return token
        except Exception:
            self._api_token = None
            self._api_token_base_url = None
            return None

    def fetch_symbol_name(self, symbol: str, row_widget):
        w = self.window
        api = self._get_active_api_account()
        if not api:
            w.set_symbol_name(row_widget, "API未設定")
            w.status_label.setText("API設定が未登録のため銘柄名を取得できません。")
            return

        token = self._get_api_token(api)
        if not token:
            w.set_symbol_name(row_widget, "取得失敗")
            w.status_label.setText("APIトークン取得に失敗しました。")
            return

        base_url = api.base_url.rstrip("/")
        query = urllib.parse.urlencode({"Exchange": 9})
        url = f"{base_url}/symbol/{symbol}?{query}"

        try:
            data = self._request_json("GET", url, headers={"X-API-KEY": token})
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self._api_token = None
                token = self._get_api_token(api)
                if token:
                    try:
                        data = self._request_json("GET", url, headers={"X-API-KEY": token})
                    except Exception:
                        w.set_symbol_name(row_widget, "取得失敗")
                        w.status_label.setText("銘柄名の取得に失敗しました。")
                        return
                else:
                    w.set_symbol_name(row_widget, "取得失敗")
                    w.status_label.setText("APIトークン再取得に失敗しました。")
                    return
            else:
                w.set_symbol_name(row_widget, "取得失敗")
                w.status_label.setText("銘柄名の取得に失敗しました。")
                return
        except Exception:
            w.set_symbol_name(row_widget, "取得失敗")
            w.status_label.setText("銘柄名の取得に失敗しました。")
            return

        symbol_name = data.get("SymbolName") or data.get("DisplayName") or ""
        if not symbol_name:
            w.set_symbol_name(row_widget, "未取得")
            w.status_label.setText("銘柄名が見つかりませんでした。")
            return
        w.set_symbol_name(row_widget, symbol_name)
        w.status_label.setText(f"銘柄名を取得しました: {symbol_name}")            
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



    def clear_orders(self):
        self.window.clear_orders()
        self.window.toast("クリア", "注文内容をクリアしました。")

    # ---------- SUBMIT ORDERS ----------
    def submit_orders_to_db(self):
        w = self.window
        errors = w.get_order_validation_errors()
        if errors:
            w.toast("入力エラー", " / ".join(errors), error=True)
            return
        orders = w.get_orders_payload()

        # バリデーション（最低限）
        if not orders:
            w.toast("送信不可", "注文行がありません。", error=True)
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
        run_mode = orders[0].get("run_mode") or "immediate"
        scheduled_at = orders[0].get("scheduled_at")
        scheduled_at_value = scheduled_at if run_mode == "scheduled" else None

        try:
            with self._conn() as conn:
                # batch_jobs
                cur = conn.execute(
                    """
                    INSERT INTO batch_jobs (batch_code, api_account_id, name, status, run_mode, scheduled_at, eod_close_time, eod_force_close)
                    VALUES (?, ?, ?, 'SCHEDULED', ?, ?, '14:30', 1)
                    """,
                    (batch_code, api_account_id, batch_name, run_mode, scheduled_at_value)
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
