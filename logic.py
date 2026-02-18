# logic.py
from __future__ import annotations

import json
import time
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time as dt_time
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from ui_main import MainWindow
from logic_types import ApiAccount
from logic_ui_mixin import AppUiMixin
from logic_worker_mixin import AppWorkerMixin


class AppLogic(AppUiMixin, AppWorkerMixin, QObject):
    def __init__(self, window: MainWindow, db_path: str):
        super().__init__()
        self.window = window
        self.db_path = db_path
        self._api_token: Optional[str] = None
        self._api_token_base_url: Optional[str] = None
        self._last_api_token_error: Optional[Exception] = None
        self._last_api_token_error_detail: Optional[str] = None
        self._worker_timer: Optional[QTimer] = None
        self._worker_busy = False
        self._notified_error_keys: set[str] = set()
        self._init_db()
        self._prime_notified_error_keys()

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        if normalized.endswith("/token"):
            normalized = normalized[: -len("/token")]
        return normalized
    
    def bind(self):
        w = self.window
        w.request_save_api.connect(self.save_api_account)
        w.request_load_api.connect(self.load_api_account)
        w.request_symbol_lookup.connect(self.fetch_symbol_name)

        w.request_clear_orders.connect(self.clear_orders)
        w.request_submit_orders.connect(self.submit_orders_to_db)
        w.request_manual_close.connect(self.manual_close_item)
        w.request_cancel_scheduled.connect(self.cancel_scheduled_item)

        self._worker_timer = QTimer(self)
        self._worker_timer.timeout.connect(self._worker_tick)
        self._worker_timer.start(2_000)
    # ---------- DB ----------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _run_with_db_retry(self, action, retries: int = 3, sleep_seconds: float = 0.15):
        for attempt in range(retries + 1):
            try:
                with self._conn() as conn:
                    return action(conn)
            except sqlite3.OperationalError as e:
                if "database is locked" not in str(e).lower() or attempt >= retries:
                    raise
                time.sleep(sleep_seconds * (attempt + 1))

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_password_enc TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
                    updated_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours'))
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
                    created_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
                    updated_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
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
                    created_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
                    updated_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
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
                    created_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
                    FOREIGN KEY (batch_job_id) REFERENCES batch_jobs(id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_item_id INTEGER NOT NULL,
                    order_role TEXT NOT NULL,
                    api_order_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    order_type TEXT NOT NULL,
                    price REAL,
                    trigger_price REAL,
                    hold_id TEXT,
                    status TEXT NOT NULL,
                    cum_qty INTEGER NOT NULL DEFAULT 0,
                    avg_price REAL,
                    sent_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
                    last_sync_at DATETIME,
                    raw_json TEXT,
                    created_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
                    updated_at DATETIME NOT NULL DEFAULT (datetime('now','+9 hours')),
                    UNIQUE(api_order_id),
                    FOREIGN KEY (batch_item_id) REFERENCES batch_items(id)
                );
                """
            )

            self._ensure_column(conn, "batch_items", "entry_order_id", "entry_order_id TEXT")
            self._ensure_column(conn, "batch_items", "tp_order_id", "tp_order_id TEXT")
            self._ensure_column(conn, "batch_items", "sl_order_id", "sl_order_id TEXT")
            self._ensure_column(conn, "batch_items", "eod_order_id", "eod_order_id TEXT")
            self._ensure_column(conn, "batch_items", "entry_filled_qty", "entry_filled_qty INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "batch_items", "entry_avg_price", "entry_avg_price REAL")
            self._ensure_column(conn, "batch_items", "closed_qty", "closed_qty INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "batch_items", "hold_id", "hold_id TEXT")

    def _log_event(
        self,
        batch_job_id: int,
        level: str,
        event_type: str,
        message: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        if conn is not None:
            conn.execute(
                "INSERT INTO event_logs (batch_job_id, level, event_type, message) VALUES (?, ?, ?, ?)",
                (batch_job_id, level, event_type, message),
            )
            return

        with self._conn() as local_conn:
            local_conn.execute(
                "INSERT INTO event_logs (batch_job_id, level, event_type, message) VALUES (?, ?, ?, ?)",
                (batch_job_id, level, event_type, message),
            )

    def _get_active_api_account(self) -> Optional[ApiAccount]:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT id, name, base_url, api_password_enc, is_active
                    FROM api_accounts
                    WHERE is_active = 1
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
            if not row:
                return None
            return ApiAccount(
                id=int(row["id"]),
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
        base_url = self._normalize_base_url(api.base_url)
        if self._api_token and self._api_token_base_url == base_url:
            self._last_api_token_error = None
            self._last_api_token_error_detail = None
            return self._api_token
        self._last_api_token_error = None
        self._last_api_token_error_detail = None
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
            self._last_api_token_error_detail = f"Tokenがレスポンスに含まれていません: {data}"
        except Exception as e:
            self._api_token = None
            self._api_token_base_url = None
            self._last_api_token_error = e
            return None

    def _build_last_token_error_message(self, message: str) -> str:
        if self._last_api_token_error is not None:
            return self._build_api_error_message(message, self._last_api_token_error)
        if self._last_api_token_error_detail:
            return f"{message}（{self._last_api_token_error_detail}）"
        return message

    def _build_api_error_message(self, message: str, err: Exception) -> str:
        details = []
        hint = None
        if isinstance(err, urllib.error.HTTPError):
            details.append(f"HTTP {err.code}")
            try:
                body = err.read().decode("utf-8", errors="replace")
                if body:
                    try:
                        body_json = json.loads(body)
                        code = body_json.get("Code") or body_json.get("code")
                        api_message = body_json.get("Message") or body_json.get("message")
                        if code is not None:
                            details.append(f"Code={code}")
                        if api_message:
                            details.append(str(api_message))
                        if code in (4001013, "4001013"):
                            hint = (
                                "APIパスワード不一致の可能性があります。"
                                "kabuステーション側のAPIパスワードと、本アプリに保存したパスワード、"
                                "およびBase URL（本番: http://localhost:18080/kabusapi / 検証: http://localhost:18081/kabusapi）"
                                "を確認してください。"
                            )
                        if code is None and not api_message:
                            details.append(body)
                    except json.JSONDecodeError:
                        details.append(body)
            except Exception:
                pass
        elif isinstance(err, urllib.error.URLError):
            details.append(f"URLError: {err.reason}")
        else:
            details.append(f"{type(err).__name__}: {err}")

        if not details:
            return message
        result = f"{message}（{' / '.join(details)}）"
        if hint:
            result = f"{result} {hint}"
        return result

    @staticmethod
    def _read_http_error_body(err: urllib.error.HTTPError) -> str:
        try:
            body = err.read().decode("utf-8", errors="replace")
            return body.strip()
        except Exception:
            return ""

    @staticmethod
    def _parse_error_json(body: str) -> Optional[dict]:
        if not body:
            return None
        try:
            parsed = json.loads(body)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    def _build_http_error_with_body(self, message: str, err: urllib.error.HTTPError, body: Optional[str] = None) -> str:
        details = [f"HTTP {err.code}"]
        body = body if body is not None else self._read_http_error_body(err)
        if body:
            payload = self._parse_error_json(body)
            if payload is None:
                details.append(body)
            else:
                code = payload.get("Code") or payload.get("code")
                api_message = payload.get("Message") or payload.get("message")
                if code is not None:
                    details.append(f"Code={code}")
                if api_message:
                    details.append(str(api_message))
                if code is None and not api_message:
                    details.append(body)
        return f"{message}（{' / '.join(details)}）"

    @staticmethod
    def _normalize_exchange(exchange_value) -> int:
        exchange = int(exchange_value)
        if exchange not in {1, 3, 5, 6, 9, 27}:
            raise ValueError(f"Exchangeが不正です: {exchange}")
        return exchange
    
    @staticmethod
    def _is_market_session_open(now: Optional[datetime] = None) -> bool:
        current = now or datetime.now()
        if current.weekday() >= 5:
            return False
        current_time = current.time()
        is_morning = dt_time(9, 0) <= current_time < dt_time(11, 30)
        is_afternoon = dt_time(12, 30) <= current_time < dt_time(15, 30)
        return is_morning or is_afternoon

    def fetch_symbol_name(self, symbol: str, row_widget):
        w = self.window
        symbol = symbol.strip()
        api = self._get_active_api_account()
        if not api:
            w.set_symbol_name(row_widget, "API未設定")
            w.set_symbol_price(row_widget, "-")
            w.status_label.setText("API設定が未登録のため銘柄名を取得できません。")
            return

        token = self._get_api_token(api)
        if not token:
            w.set_symbol_name(row_widget, "取得失敗")
            w.set_symbol_price(row_widget, "-")
            w.status_label.setText(self._build_last_token_error_message("APIトークン取得に失敗しました。"))
            return

        base_url = self._normalize_base_url(api.base_url)
        exchange_candidates = (1, 3, 5, 6, 9)

        def request_symbol_with_token(current_token: str):
            last_error: Optional[Exception] = None
            for exchange in exchange_candidates:
                candidate_urls = (
                    f"{base_url}/symbol/{symbol}@{exchange}",
                    f"{base_url}/symbol/{symbol}?{urllib.parse.urlencode({'Exchange': exchange})}",
                )
                for candidate_url in candidate_urls:
                    try:
                        data = self._request_json("GET", candidate_url, headers={"X-API-KEY": current_token})
                        if data.get("SymbolName") or data.get("DisplayName"):
                            return data, exchange, candidate_url
                    except urllib.error.HTTPError as e:
                        last_error = e
                        if e.code == 401:
                            raise
                        continue
                    except Exception as e:
                        last_error = e
                        continue
            if last_error:
                raise last_error
            raise RuntimeError("銘柄情報取得に失敗しました。")


        try:
            data, used_exchange, used_url = request_symbol_with_token(token)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self._api_token = None
                token = self._get_api_token(api)
                if token:
                    try:
                        data, used_exchange, used_url = request_symbol_with_token(token)
                    except Exception as retry_error:
                        w.set_symbol_name(row_widget, "取得失敗")
                        w.set_symbol_price(row_widget, "-")
                        w.status_label.setText(self._build_api_error_message("銘柄名の取得に失敗しました。", retry_error))
                        return
                else:
                    w.set_symbol_name(row_widget, "取得失敗")
                    w.set_symbol_price(row_widget, "-")
                    w.status_label.setText("APIトークン再取得に失敗しました。")
                    return
            else:
                w.set_symbol_name(row_widget, "取得失敗")
                w.set_symbol_price(row_widget, "-")
                w.status_label.setText(self._build_api_error_message("銘柄名の取得に失敗しました。", e))
                return
        except Exception as e:
            w.set_symbol_name(row_widget, "取得失敗")
            w.set_symbol_price(row_widget, "-")
            w.status_label.setText(self._build_api_error_message("銘柄名の取得に失敗しました。", e))
            return

        symbol_name = data.get("SymbolName") or data.get("DisplayName") or ""
        if not symbol_name:
            w.set_symbol_name(row_widget, "未取得")
            w.set_symbol_price(row_widget, "-")
            w.status_label.setText("銘柄名が見つかりませんでした。")
            return
        board_price_text = "-"
        try:
            board_data = self._request_json(
                "GET",
                f"{base_url}/board/{symbol}@{used_exchange}",
                headers={"X-API-KEY": token},
            )
            current_price = board_data.get("CurrentPrice")
            if current_price is not None:
                board_price_text = f"{current_price} 円"
        except Exception:
            board_price_text = "取得失敗"
        w.set_symbol_name(row_widget, symbol_name)
        w.set_symbol_price(row_widget, board_price_text)
        w.status_label.setText(
            f"銘柄情報を取得しました: {symbol_name} / 現在値={board_price_text} (Exchange={used_exchange}, URL={used_url})"
        )
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

        api = ApiAccount(id=0, name=name, base_url=base_url, api_password_enc=pw, is_active=active)

        try:
            with self._conn() as conn:
                conn.execute("UPDATE api_accounts SET is_active=0 WHERE is_active=1;")
                conn.execute(
                    """
                    INSERT INTO api_accounts (name, base_url, api_password_enc, is_active)
                    VALUES (?, ?, ?, ?)
                    """,
                    (api.name, api.base_url, api.api_password_enc, 1 if api.is_active else 0),
                )
            self._api_token = None
            self._api_token_base_url = None
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

    def manual_close_item(self, item_id: int):
        api = self._get_active_api_account()
        if not api:
            self.window.toast("成行決済失敗", "API設定が未登録です。", error=True)
            return

        with self._conn() as conn:
            item = conn.execute(
                """
                SELECT bi.*
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                WHERE bi.id=?
                """,
                (int(item_id),),
            ).fetchone()

        if not item:
            self.window.toast("成行決済失敗", f"対象注文が見つかりません: id={item_id}", error=True)
            return
        if str(item["status"] or "") == "CLOSED":
            self.window.toast("成行決済", f"既に決済済みです: id={item_id}")
            return
        if item["product"] == "margin" and not item["hold_id"]:
            self.window.toast("成行決済失敗", "信用建玉のHoldIDが未取得です。", error=True)
            return

        remaining = max(int(item["entry_filled_qty"] or 0) - int(item["closed_qty"] or 0), 0)
        if remaining <= 0:
            self.window.toast("成行決済", f"残数量がありません: id={item_id}")
            return

        try:
            self._cancel_order_if_needed(api, item["tp_order_id"])
            self._cancel_order_if_needed(api, item["sl_order_id"])
            payload = self._build_exit_payload(item, "market", remaining, None, None, item["hold_id"])
            with self._conn() as conn:
                self._log_payload_debug(int(item["batch_job_id"]), "MANUAL_CLOSE_PAYLOAD", payload, conn)
            order_id, _ = self._api_post_order(api, payload)
        except Exception as e:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE batch_items SET status='ERROR', last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                    (f"manual_close: {e}", int(item_id)),
                )
            self.window.toast("成行決済失敗", str(e), error=True)
            return

        close_side = "sell" if item["side"] == "buy" else "buy"
        with self._conn() as conn:
            self._record_order(conn, int(item_id), "manual", order_id, close_side, remaining, "market", None, None, item["hold_id"])
            conn.execute(
                """
                UPDATE batch_items
                SET eod_order_id=?, status='EOD_MARKET_SENT', updated_at=datetime('now','+9 hours')
                WHERE id=?
                """,
                (order_id, int(item_id)),
            )
            self._log_event(int(item["batch_job_id"]), "INFO", "MANUAL_MARKET_CLOSE", f"item={item_id} order_id={order_id}", conn=conn)

        self.window.toast("成行決済", f"成行決済を送信しました: id={item_id}")
    def cancel_scheduled_item(self, item_id: int):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT bi.id, bi.batch_job_id, bi.status AS item_status, bj.status AS job_status, bj.run_mode
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                WHERE bi.id=?
                """,
                (int(item_id),),
            ).fetchone()

            if not row:
                self.window.toast("予約キャンセル失敗", f"対象注文が見つかりません: id={item_id}", error=True)
                return

            if row["run_mode"] != "scheduled":
                self.window.toast("予約キャンセル不可", "予約注文のみキャンセルできます。", error=True)
                return

            if row["job_status"] != "SCHEDULED" or row["item_status"] != "READY":
                self.window.toast("予約キャンセル不可", "既に実行フェーズに入っているためキャンセルできません。", error=True)
                return

            conn.execute(
                "UPDATE batch_items SET status='CANCELLED', updated_at=datetime('now','+9 hours') WHERE id=?",
                (int(item_id),),
            )
            self._log_event(int(row["batch_job_id"]), "INFO", "SCHEDULE_CANCELLED", f"item={item_id} を予約キャンセル", conn=conn)

            remain = conn.execute(
                "SELECT COUNT(*) FROM batch_items WHERE batch_job_id=? AND status='READY'",
                (int(row["batch_job_id"]),),
            ).fetchone()[0]
            if int(remain) == 0:
                conn.execute(
                    "UPDATE batch_jobs SET status='CANCELLED', updated_at=datetime('now','+9 hours') WHERE id=?",
                    (int(row["batch_job_id"]),),
                )

        self.window.toast("予約キャンセル", f"予約注文をキャンセルしました: id={item_id}")
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
        initial_status = "SCHEDULED"
        try:
            def _write_batch(conn: sqlite3.Connection):
                cur = conn.execute(
                    """
                    INSERT INTO batch_jobs (batch_code, api_account_id, name, status, run_mode, scheduled_at, eod_close_time, eod_force_close)
                    VALUES (?, ?, ?, ?, ?, ?, '14:30', 1)
                    """,
                    (batch_code, api_account_id, batch_name, initial_status, run_mode, scheduled_at_value),
                )
                batch_job_id = cur.lastrowid

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
                        ),
                    )

                self._log_event(
                    batch_job_id,
                    "INFO",
                    "BATCH_CREATED",
                    f"Batch created: {batch_code} / {batch_name} / items={len(orders)}",
                    conn=conn,
                )

                return batch_job_id

            self._run_with_db_retry(_write_batch)

            w.toast("送信完了", f"バッチを作成しDBに保存しました。（items={len(orders)}）")
        except Exception as e:
            w.toast("送信失敗", f"DB保存に失敗: {e}", error=True)

    def _get_active_api_account_id(self) -> Optional[int]:
        api = self._get_active_api_account()
        return api.id if api else None

    # ---------- Worker loop ----------
    def _worker_tick(self):
        if self._worker_busy:
            return
        self._worker_busy = True
        try:
            self._scheduler_step()
            self._execution_step()
            self._sync_orders_step()
            self._oco_step()
            self._eod_step()
            self._finalize_jobs_step()
            self._refresh_execution_status_ui()
            self._notify_new_item_errors()
        except Exception as e:
            self.window.status_label.setText(f"監視ループでエラー: {e}")
        finally:
            self._worker_busy = False
