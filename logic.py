# logic.py
from __future__ import annotations

import json
import time
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from ui_main import MainWindow


@dataclass
class ApiAccount:
    id: int
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

    @staticmethod
    def _render_order_status(status: Optional[str], fallback_waiting: str = "WAITING") -> str:
        if not status:
            return fallback_waiting
        normalized = str(status).strip().upper()
        known = {"NEW", "WORKING", "PARTIAL", "FILLED", "CANCELLED", "ERROR", "UNKNOWN", "WAITING"}
        return normalized if normalized in known else "UNKNOWN"
        return mapping.get(status, status)

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
            
    def _scheduler_step(self):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id FROM batch_jobs
                WHERE status='SCHEDULED' AND run_mode='scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= ?
                """,
                (now_str,),
            ).fetchall()
            for row in rows:
                conn.execute("UPDATE batch_jobs SET status='RUNNING', updated_at=datetime('now','+9 hours') WHERE id=?", (row["id"],))
                self._log_event(int(row["id"]), "INFO", "SCHEDULE_TRIGGERED", "予約時刻到達でRUNNINGに遷移", conn=conn)

            immediate_rows = conn.execute(
                "SELECT id FROM batch_jobs WHERE status='SCHEDULED' AND run_mode='immediate'"
            ).fetchall()
            for row in immediate_rows:
                conn.execute("UPDATE batch_jobs SET status='RUNNING', updated_at=datetime('now','+9 hours') WHERE id=?", (row["id"],))
                self._log_event(int(row["id"]), "INFO", "IMMEDIATE_TRIGGERED", "即時実行バッチを開始", conn=conn)

    def _api_post_order(self, api: ApiAccount, payload: dict) -> tuple[str, int]:
        token = self._get_api_token(api)
        if not token:
            raise RuntimeError(self._build_last_token_error_message("APIトークン取得に失敗"))
        base_url = self._normalize_base_url(api.base_url)
        requested_exchange = self._normalize_exchange(payload.get("Exchange"))
        resolved_exchange = requested_exchange
        try:
            data = self._request_json("POST", f"{base_url}/sendorder", headers={"X-API-KEY": token}, payload=payload)
        except urllib.error.HTTPError as e:
            body = self._read_http_error_body(e)
            payload_ctx = self._payload_error_context(payload)
            err_payload = self._parse_error_json(body)
            err_code = (err_payload or {}).get("Code") or (err_payload or {}).get("code")
            current_exchange = payload.get("Exchange")
            retry_candidates_by_exchange = {
                1: (9, 27),
                9: (27, 1),
                27: (9, 1),
            }
            retry_exchanges = retry_candidates_by_exchange.get(current_exchange, (1, 9, 27))
            retry_exchanges = tuple(exchange for exchange in retry_exchanges if exchange != current_exchange)

            if str(err_code) == "4001005" and retry_exchanges:
                for retry_exchange in retry_exchanges:
                    retry_payload = dict(payload)
                    retry_payload["Exchange"] = retry_exchange
                    try:
                        data = self._request_json("POST", f"{base_url}/sendorder", headers={"X-API-KEY": token}, payload=retry_payload)
                        resolved_exchange = self._normalize_exchange(retry_exchange)
                        break
                    except urllib.error.HTTPError as retry_error:
                        retry_body = self._read_http_error_body(retry_error)
                        if retry_exchange == retry_exchanges[-1]:
                            raise RuntimeError(f"{self._build_http_error_with_body('発注API呼び出しに失敗', retry_error, retry_body)} / payload={payload_ctx}") from retry_error
                else:
                    raise RuntimeError(f"{self._build_http_error_with_body('発注API呼び出しに失敗', e, body)} / payload={payload_ctx}") from e
            else:
                raise RuntimeError(f"{self._build_http_error_with_body('発注API呼び出しに失敗', e, body)} / payload={payload_ctx}") from e
        order_id = data.get("OrderId") or data.get("OrderID")
        if not order_id:
            raise RuntimeError(f"注文IDが返却されませんでした: {data}")
        return str(order_id), resolved_exchange

    @staticmethod
    def _side_to_kabu(side: str) -> str:
        return "2" if side == "buy" else "1"

    @staticmethod
    def _kabu_side_to_internal(side: object) -> Optional[str]:
        value = str(side or "").strip()
        if value == "1":
            return "sell"
        if value == "2":
            return "buy"
        return None

    @staticmethod
    def _parse_int(value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_hold_id(hold_id: object) -> str:
        return str(hold_id or "").strip()

    def _extract_position_hold_id(self, position: dict) -> tuple[str, str]:
        # /positions の建玉IDは HoldID を優先し、未提供時のみ ExecutionID をフォールバック利用する
        for key in ("HoldID", "HoldId", "ExecutionID", "ExecutionId"):
            hold_id = self._normalize_hold_id(position.get(key))
            if hold_id:
                source = "HoldID" if key in {"HoldID", "HoldId"} else "ExecutionID"
                return hold_id, source
        return "", ""

    @staticmethod
    def _is_valid_hold_id(hold_id: object) -> bool:
        normalized = str(hold_id or "").strip()
        return normalized.startswith("E")
    
    def _build_entry_payload(self, item: sqlite3.Row) -> dict:
        market = item["entry_type"] == "market"
        exchange = self._normalize_exchange(item["exchange"])
        payload = {
            "Symbol": item["symbol"],
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": self._side_to_kabu(item["side"]),
            "Qty": int(item["qty"]),
            "FrontOrderType": 10 if market else 20,
            "Price": 0 if market else int(item["entry_price"] or 0),
            "ExpireDay": 0,
            "AccountType": 4,
        }
        if item["product"] == "cash":
            payload.update({"CashMargin": 1, "DelivType": 2, "FundType": "AA"})
        else:
            payload.update({"CashMargin": 2, "MarginTradeType": 3, "DelivType": 0})
        return payload

    def _build_exit_payload(self, item: sqlite3.Row, order_type: str, qty: int, price: Optional[float], trigger: Optional[float], hold_id: Optional[str]) -> dict:
        close_side = "sell" if item["side"] == "buy" else "buy"
        payload = {
            "Symbol": item["symbol"],
            "Exchange": self._normalize_exchange(item["exchange"]),
            "SecurityType": 1,
            "Side": self._side_to_kabu(close_side),
            "Qty": int(qty),
            "ExpireDay": 0,
            "AccountType": 4,
        }
        
        if item["product"] == "cash":
            payload["CashMargin"] = 1
            # 現物の決済系注文（保有現物の売却）は FundType を付与しない。
            # FundType は現物買付で利用する項目で、決済売りに付与すると
            # 4001005（パラメータ変換エラー）になるケースがあるため。
            # DelivType は決済系で安定している 0 を利用する。
            payload["DelivType"] = 0
            if close_side != "sell":
                payload["FundType"] = "AA"
        else:
            
            payload["CashMargin"] = 3
            payload["MarginTradeType"] = 3
            payload["DelivType"] = 0
            normalized_hold_id = self._normalize_hold_id(hold_id)
            if not self._is_valid_hold_id(normalized_hold_id):
                raise RuntimeError(
                    f"信用返済に必要なHoldIDが不正です: item={item['id']} symbol={item['symbol']} hold_id={normalized_hold_id or '<empty>'}"
                )
            payload["ClosePositions"] = [{"HoldID": normalized_hold_id, "Qty": int(qty)}]

        if order_type == "market":
            payload["FrontOrderType"] = 10
            payload["Price"] = 0
        elif order_type == "limit":
            payload["FrontOrderType"] = 20
            payload["Price"] = int(price or 0)
        else:
            payload["FrontOrderType"] = 30
            payload["Price"] = 0
            payload["ReverseLimitOrder"] = {
                "TriggerSec": 1,
                "TriggerPrice": int(trigger or 0),
                "UnderOver": 1,
                "AfterHitOrderType": 1,
                "AfterHitPrice": 0,
            }
        return payload
    
    @staticmethod
    def _validate_oco_prices(side: str, avg: float, tp_abs: float, sl_abs: float) -> Optional[str]:
        if tp_abs <= 0 or sl_abs <= 0:
            return f"TP/SL価格が不正です: tp={tp_abs}, sl={sl_abs}"
        if side == "buy":
            if not (tp_abs > avg and sl_abs < avg):
                return f"買い注文のTP/SL方向が不正です: avg={avg}, tp={tp_abs}, sl={sl_abs}"
        elif side == "sell":
            if not (tp_abs < avg and sl_abs > avg):
                return f"売り注文のTP/SL方向が不正です: avg={avg}, tp={tp_abs}, sl={sl_abs}"
        else:
            return f"売買方向が不正です: side={side}"
        return None
    
    @staticmethod
    def _payload_error_context(payload: dict) -> str:
        context = {
            "Symbol": payload.get("Symbol"),
            "Exchange": payload.get("Exchange"),
            "Side": payload.get("Side"),
            "Qty": payload.get("Qty"),
            "CashMargin": payload.get("CashMargin"),
            "DelivType": payload.get("DelivType"),
            "FundType": payload.get("FundType"),
            "MarginTradeType": payload.get("MarginTradeType"),
            "FrontOrderType": payload.get("FrontOrderType"),
            "Price": payload.get("Price"),
            "TriggerPrice": (payload.get("ReverseLimitOrder") or {}).get("TriggerPrice"),
        }
        return json.dumps(context, ensure_ascii=False)
    
    @staticmethod
    def _to_positive_float(value: object) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _extract_order_avg_price(self, order: dict) -> Optional[float]:
        primary = self._to_positive_float(order.get("Price"))
        if primary:
            return primary

        details = order.get("Details")
        if not isinstance(details, list):
            return None

        weighted_price = 0.0
        weighted_qty = 0
        fallback = None
        for detail in details:
            if not isinstance(detail, dict):
                continue
            price = None
            for key in ("RecPrice", "ExecutionPrice", "Price"):
                price = self._to_positive_float(detail.get(key))
                if price:
                    break
            if not price:
                continue
            fallback = price
            qty = detail.get("RecQty") or detail.get("ExecutionQty") or detail.get("Qty")
            try:
                qty_int = int(qty)
            except (TypeError, ValueError):
                qty_int = 0
            if qty_int > 0:
                weighted_price += price * qty_int
                weighted_qty += qty_int

        if weighted_qty > 0:
            return weighted_price / weighted_qty
        return fallback

    def _log_payload_debug(self, batch_job_id: int, event_type: str, payload: dict, conn: sqlite3.Connection) -> None:
        details = {
            "Symbol": payload.get("Symbol"),
            "Exchange": payload.get("Exchange"),
            "Side": payload.get("Side"),
            "Qty": payload.get("Qty"),
            "CashMargin": payload.get("CashMargin"),
            "DelivType": payload.get("DelivType"),
            "FundType": payload.get("FundType"),
            "MarginTradeType": payload.get("MarginTradeType"),
            "FrontOrderType": payload.get("FrontOrderType"),
            "Price": payload.get("Price"),
            "TriggerPrice": (payload.get("ReverseLimitOrder") or {}).get("TriggerPrice"),
            "AccountType": payload.get("AccountType"),
            "ReverseLimitOrder": payload.get("ReverseLimitOrder"),
        }
        self._log_event(batch_job_id, "DEBUG", event_type, json.dumps(details, ensure_ascii=False), conn=conn)

    def _record_order(self, conn: sqlite3.Connection, item_id: int, role: str, api_order_id: str, side: str, qty: int, order_type: str, price: Optional[float] = None, trigger_price: Optional[float] = None, hold_id: Optional[str] = None):
        conn.execute(
            """
            INSERT OR REPLACE INTO orders
            (batch_item_id, order_role, api_order_id, side, qty, order_type, price, trigger_price, hold_id, status, raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', '{}', datetime('now','+9 hours'))
            """,
            (item_id, role, api_order_id, side, qty, order_type, price, trigger_price, hold_id),
        )

    def _execution_step(self):
        api = self._get_active_api_account()
        if not api:
            return
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT bi.*, bj.id AS batch_job_id
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                WHERE bj.status='RUNNING' AND bi.status='READY'
                ORDER BY bi.id
                """
            ).fetchall()
        for item in rows:
            try:
                payload = self._build_entry_payload(item)
                with self._conn() as conn:
                    self._log_payload_debug(int(item["batch_job_id"]), "ENTRY_PAYLOAD", payload, conn)
                order_id, resolved_exchange = self._api_post_order(api, payload)
            except Exception as e:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE batch_items SET status='ERROR', last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                        (str(e), item["id"]),
                    )
                    self._log_event(
                        int(item["batch_job_id"]),
                        "ERROR",
                        "ENTRY_FAILED",
                        f"item={item['id']} err={e}",
                        conn=conn,
                    )
                continue

            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE batch_items
                    SET status='ENTRY_SENT', entry_order_id=?, exchange=?, updated_at=datetime('now','+9 hours')
                    WHERE id=?
                    """,
                    (order_id, resolved_exchange, item["id"]),
                )
                self._record_order(conn, int(item["id"]), "entry", order_id, item["side"], int(item["qty"]), item["entry_type"], item["entry_price"])
                self._log_event(
                    int(item["batch_job_id"]),
                    "INFO",
                    "ENTRY_SENT",
                    f"item={item['id']} order_id={order_id} exchange={resolved_exchange}",
                    conn=conn,
                )

    def _fetch_orders_snapshot(self, api: ApiAccount) -> list[dict]:
        token = self._get_api_token(api)
        if not token:
            return []
        base_url = self._normalize_base_url(api.base_url)
        data = self._request_json("GET", f"{base_url}/orders", headers={"X-API-KEY": token})
        return data if isinstance(data, list) else []

    def _fetch_positions_snapshot(self, api: ApiAccount) -> list[dict]:
        token = self._get_api_token(api)
        if not token:
            return []
        base_url = self._normalize_base_url(api.base_url)
        data = self._request_json("GET", f"{base_url}/positions", headers={"X-API-KEY": token})
        return data if isinstance(data, list) else []

    @staticmethod
    def _order_status_from_api(order: dict) -> str:
        state = str(order.get("State") or order.get("state") or "")
        if state in {"1", "2"}:
            return "WORKING"
        if state in {"3", "4"}:
            return "PARTIAL"
        if state in {"5"}:
            return "FILLED"
        if state in {"6", "7"}:
            return "CANCELLED"
        return "UNKNOWN"

    def _sync_orders_step(self):
        api = self._get_active_api_account()
        if not api:
            return
        try:
            snapshots = self._fetch_orders_snapshot(api)
        except Exception:
            return
        try:
            positions = self._fetch_positions_snapshot(api)
        except Exception:
            positions = []

        by_id = {}
        for order in snapshots:
            oid = order.get("ID") or order.get("OrderId") or order.get("OrderID")
            if oid:
                by_id[str(oid)] = order

        def _sync(conn: sqlite3.Connection):
            rows = conn.execute(
                """
                SELECT bi.id AS batch_item_id, bi.batch_job_id, bi.entry_order_id, bi.tp_order_id, bi.sl_order_id, bi.eod_order_id
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                WHERE bj.status='RUNNING'
                """
            ).fetchall()

            for row in rows:
                item_id = int(row["batch_item_id"])
                for role, key in (("entry", "entry_order_id"), ("tp", "tp_order_id"), ("sl", "sl_order_id"), ("eod", "eod_order_id")):
                    oid = row[key]
                    if not oid:
                        continue
                    api_order = by_id.get(str(oid))
                    if not api_order:
                        continue
                    status = self._order_status_from_api(api_order)
                    cum_qty = int(api_order.get("CumQty") or 0)
                    avg_price = self._extract_order_avg_price(api_order)
                    conn.execute(
                        """
                        UPDATE orders
                        SET status=?, cum_qty=?, avg_price=?, raw_json=?, last_sync_at=datetime('now','+9 hours'), updated_at=datetime('now','+9 hours')
                        WHERE api_order_id=?
                        """,
                        (status, cum_qty, float(avg_price) if avg_price is not None else None, json.dumps(api_order, ensure_ascii=False), str(oid)),
                    )
                    if role == "entry":
                        new_status = "ENTRY_SENT"
                        if status == "FILLED":
                            new_status = "ENTRY_FILLED" if avg_price else "ENTRY_FILLED_WAIT_PRICE"
                        elif status == "PARTIAL":
                            new_status = "ENTRY_PARTIAL"
                        if status == "FILLED" and not avg_price:
                            self._log_event(
                                int(row["batch_job_id"]),
                                "WARN",
                                "ENTRY_PRICE_UNAVAILABLE",
                                f"item={item_id} order_id={oid}",
                                conn=conn,
                            )
                        conn.execute(
                            """
                            UPDATE batch_items
                            SET status=?, entry_filled_qty=?, entry_avg_price=?, updated_at=datetime('now','+9 hours')
                            WHERE id=?
                            """,
                            (new_status, cum_qty, float(avg_price) if avg_price is not None else None, item_id),
                        )

            for p in positions:
                symbol = str(p.get("Symbol") or "").strip()
                hold_id, hold_id_source = self._extract_position_hold_id(p)
                leaves_qty = self._parse_int(p.get("LeavesQty") or p.get("Qty"), 0)
                position_side = self._kabu_side_to_internal(p.get("Side"))
                if not symbol or not hold_id or leaves_qty <= 0:
                    continue
                candidates = conn.execute(
                    """
                    SELECT bi.id, bi.side, bi.entry_filled_qty, bi.closed_qty, bi.batch_job_id
                    FROM batch_items bi
                    JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                    WHERE product='margin'
                      AND symbol=?
                      AND bi.status IN ('ENTRY_FILLED','BRACKET_SENT','ENTRY_PARTIAL')
                      AND (bi.hold_id IS NULL OR bi.hold_id='')
                      AND bj.status='RUNNING'
                    ORDER BY bi.id ASC
                    """,
                    (symbol,),
                ).fetchall()
                if not candidates:
                    continue

                if not self._is_valid_hold_id(hold_id):
                    for candidate in candidates:
                        self._log_event(
                            int(candidate["batch_job_id"]),
                            "WARN",
                            "INVALID_HOLD_ID",
                            f"symbol={symbol} hold_id={hold_id} source_positions={hold_id_source or '<unknown>'}",
                            conn=conn,
                        )
                    continue

                matched = []
                side_filtered = []
                for candidate in candidates:
                    if position_side and str(candidate["side"]) != position_side:
                        continue
                    side_filtered.append(candidate)
                    remaining_qty = max(
                        self._parse_int(candidate["entry_filled_qty"], 0) - self._parse_int(candidate["closed_qty"], 0),
                        0,
                    )
                    if remaining_qty <= 0:
                        continue
                    if remaining_qty != leaves_qty:
                        continue
                    matched.append(candidate)

                if matched:
                    target = matched[0]
                else:
                    nearest = None
                    nearest_diff = None
                    for candidate in side_filtered:
                        remaining_qty = max(
                            self._parse_int(candidate["entry_filled_qty"], 0) - self._parse_int(candidate["closed_qty"], 0),
                            0,
                        )
                        if remaining_qty <= 0:
                            continue
                        diff = abs(remaining_qty - leaves_qty)
                        if nearest is None or diff < nearest_diff or (diff == nearest_diff and int(candidate["id"]) < int(nearest["id"])):
                            nearest = candidate
                            nearest_diff = diff

                    if nearest is None:
                        for candidate in candidates:
                            self._log_event(
                                int(candidate["batch_job_id"]),
                                "WARN",
                                "HOLD_ID_MATCH_NOT_FOUND",
                                f"symbol={symbol} hold_id={hold_id} source={hold_id_source or '<unknown>'} leaves_qty={leaves_qty} side={position_side or '<unknown>'}",
                                conn=conn,
                            )
                            conn.execute(
                                "UPDATE batch_items SET last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                                (f"HoldID紐付け不可: symbol={symbol} leaves_qty={leaves_qty}", int(candidate["id"])),
                            )
                        continue

                    target = nearest
                    self._log_event(
                        int(target["batch_job_id"]),
                        "WARN",
                        "HOLD_ID_MATCH_APPROX",
                        f"symbol={symbol} hold_id={hold_id} source={hold_id_source or '<unknown>'} leaves_qty={leaves_qty} picked={target['id']} nearest_diff={nearest_diff}",
                        conn=conn,
                    )

                conn.execute(
                    "UPDATE batch_items SET last_error=NULL, updated_at=datetime('now','+9 hours') WHERE id=?",
                    (int(target["id"]),),
                )

                self._log_event(
                    int(target["batch_job_id"]),
                    "DEBUG",
                    "HOLD_ID_ASSIGNED",
                    f"item={target['id']} symbol={symbol} hold_id={hold_id} source={hold_id_source or '<unknown>'} leaves_qty={leaves_qty}",
                    conn=conn,
                )

                conn.execute(
                    """
                    UPDATE batch_items
                    SET hold_id=?, updated_at=datetime('now','+9 hours')
                    WHERE id=?
                    """,
                    (hold_id, int(target["id"])),
                )

                if len(matched) > 1:
                    match_ids = ",".join(str(m["id"]) for m in matched)
                    self._log_event(
                        int(target["batch_job_id"]),
                        "WARN",
                        "HOLD_ID_MULTI_CANDIDATE",
                        f"symbol={symbol} hold_id={hold_id} source={hold_id_source or '<unknown>'} leaves_qty={leaves_qty} candidates={len(matched)} ids=[{match_ids}] picked={target['id']} rule=earliest_id",
                        conn=conn,
                    )
                if not matched:
                    continue
        self._run_with_db_retry(_sync)

    def _oco_step(self):
        api = self._get_active_api_account()
        if not api:
            return
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT bi.*, bj.id AS batch_job_id
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id = bi.batch_job_id
                WHERE bj.status='RUNNING'
                  AND bi.status IN ('ENTRY_FILLED','ENTRY_FILLED_WAIT_PRICE')
                  AND bi.tp_order_id IS NULL
                  AND bi.sl_order_id IS NULL
                """
            ).fetchall()

        for item in rows:
            if item["product"] == "margin" and not item["hold_id"]:
                hold_wait_message = "HoldID未取得のため利確/損切の発注を保留中"
                with self._conn() as conn:
                    if (item["last_error"] or "") != hold_wait_message:
                        conn.execute(
                            "UPDATE batch_items SET last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                            (hold_wait_message, item["id"]),
                        )
                        self._log_event(
                            int(item["batch_job_id"]),
                            "WARN",
                            "OCO_WAIT_HOLD_ID",
                            f"item={item['id']} symbol={item['symbol']} side={item['side']}",
                            conn=conn,
                        )
                continue
            filled_qty = int(item["entry_filled_qty"] or 0)
            closed_qty = int(item["closed_qty"] or 0)
            qty = max(filled_qty - closed_qty, 0)
            if qty <= 0:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE batch_items SET status='CLOSED', updated_at=datetime('now','+9 hours') WHERE id=?",
                        (item["id"],),
                    )
                    self._log_event(
                        int(item["batch_job_id"]),
                        "INFO",
                        "OCO_NO_REMAINING",
                        f"item={item['id']} filled={filled_qty} closed={closed_qty}",
                        conn=conn,
                    )
                continue
            avg = float(item["entry_avg_price"] or item["entry_price"] or 0)
            if avg <= 0:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE batch_items SET status='ENTRY_FILLED_WAIT_PRICE', last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                        ("約定価格の取得待ちのため利確/損切を保留中", item["id"]),
                    )
                    self._log_event(
                        int(item["batch_job_id"]),
                        "WARN",
                        "OCO_WAIT_PRICE",
                        f"item={item['id']}",
                        conn=conn,
                    )
                continue
            tp_abs = avg + float(item["tp_price"])
            sl_abs = avg + float(item["sl_trigger_price"])
            price_error = self._validate_oco_prices(str(item["side"]), avg, tp_abs, sl_abs)
            if price_error:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE batch_items SET status='ERROR', last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                        (price_error, item["id"]),
                    )
                    self._log_event(
                        int(item["batch_job_id"]),
                        "ERROR",
                        "OCO_PRICE_INVALID",
                        f"item={item['id']} err={price_error}",
                        conn=conn,
                    )
                continue
            try:
                tp_payload = self._build_exit_payload(item, "limit", qty, tp_abs, None, item["hold_id"])
                with self._conn() as conn:
                    self._log_payload_debug(int(item["batch_job_id"]), "TP_PAYLOAD", tp_payload, conn)
                tp_order_id, tp_exchange = self._api_post_order(api, tp_payload)
                sl_payload = self._build_exit_payload(item, "stop", qty, None, sl_abs, item["hold_id"])
                with self._conn() as conn:
                    self._log_payload_debug(int(item["batch_job_id"]), "SL_PAYLOAD", sl_payload, conn)
                sl_order_id, sl_exchange = self._api_post_order(api, sl_payload)
                if tp_exchange != sl_exchange:
                    raise RuntimeError(f"TP/SLの市場コードが不一致です: tp={tp_exchange}, sl={sl_exchange}")
            except Exception as e:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE batch_items SET status='ERROR', last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                        (str(e), item["id"]),
                    )
                    self._log_event(
                        int(item["batch_job_id"]),
                        "ERROR",
                        "OCO_FAILED",
                        f"item={item['id']} err={e}",
                        conn=conn,
                    )
                continue

            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE batch_items
                    SET status='BRACKET_SENT', tp_order_id=?, sl_order_id=?, exchange=?, updated_at=datetime('now','+9 hours')
                    WHERE id=?
                    """,
                    (tp_order_id, sl_order_id, tp_exchange, item["id"]),
                )
                close_side = "sell" if item["side"] == "buy" else "buy"
                self._record_order(conn, int(item["id"]), "tp", tp_order_id, close_side, qty, "limit", tp_abs, None, item["hold_id"])
                self._record_order(conn, int(item["id"]), "sl", sl_order_id, close_side, qty, "stop", None, sl_abs, item["hold_id"])
                self._log_event(
                    int(item["batch_job_id"]),
                    "INFO",
                    "OCO_SENT",
                    f"item={item['id']} tp={tp_order_id} sl={sl_order_id} qty={qty} exchange_tp={tp_exchange} exchange_sl={sl_exchange}",
                    conn=conn,
                )

        with self._conn() as conn:
            close_rows = conn.execute(
                """
                SELECT bi.id, bi.batch_job_id, bi.tp_order_id, bi.sl_order_id,
                       otp.status AS tp_status, otp.cum_qty AS tp_cum,
                       osl.status AS sl_status, osl.cum_qty AS sl_cum
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id=bi.batch_job_id
                LEFT JOIN orders otp ON otp.api_order_id = bi.tp_order_id
                LEFT JOIN orders osl ON osl.api_order_id = bi.sl_order_id
                WHERE bj.status='RUNNING' AND bi.status='BRACKET_SENT'
                """
            ).fetchall()

        for row in close_rows:
            item_id = int(row["id"])
            if row["tp_status"] == "FILLED":
                self._cancel_order_if_needed(api, row["sl_order_id"])
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE batch_items SET status='CLOSED', closed_qty=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                        (int(row["tp_cum"] or 0), item_id),
                    )
                    self._log_event(int(row["batch_job_id"]), "INFO", "TP_FILLED", f"item={item_id}", conn=conn)
            elif row["sl_status"] == "FILLED":
                self._cancel_order_if_needed(api, row["tp_order_id"])
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE batch_items SET status='CLOSED', closed_qty=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                        (int(row["sl_cum"] or 0), item_id),
                    )
                    self._log_event(int(row["batch_job_id"]), "INFO", "SL_FILLED", f"item={item_id}", conn=conn)
    def _cancel_order_if_needed(self, api: ApiAccount, api_order_id: Optional[str]) -> None:
        if not api_order_id:
            return
        token = self._get_api_token(api)
        if not token:
            return
        base_url = self._normalize_base_url(api.base_url)
        try:
            self._request_json("PUT", f"{base_url}/cancelorder", headers={"X-API-KEY": token}, payload={"OrderID": api_order_id})
        except urllib.error.HTTPError as e:
            raise RuntimeError(self._build_http_error_with_body("取消API呼び出しに失敗", e)) from e
        except Exception:
            return

    def _eod_step(self):
        now = datetime.now()
        if now.strftime("%H:%M") < "14:30":
            return
        api = self._get_active_api_account()
        if not api:
            return
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT bi.*, bj.id AS batch_job_id, bj.eod_force_close
                FROM batch_items bi
                JOIN batch_jobs bj ON bj.id=bi.batch_job_id
                WHERE bj.status='RUNNING'
                  AND bj.eod_force_close=1
                  AND bi.status IN ('ENTRY_PARTIAL','ENTRY_FILLED','BRACKET_SENT')
                """
            ).fetchall()

        for item in rows:
            try:
                self._cancel_order_if_needed(api, item["tp_order_id"])
                self._cancel_order_if_needed(api, item["sl_order_id"])
                remaining = max(int(item["entry_filled_qty"] or 0) - int(item["closed_qty"] or 0), 0)
                if remaining <= 0:
                    with self._conn() as conn:
                        conn.execute("UPDATE batch_items SET status='CLOSED', updated_at=datetime('now','+9 hours') WHERE id=?", (item["id"],))
                    continue
                if item["product"] == "margin" and not item["hold_id"]:
                    with self._conn() as conn:
                        msg = "EOD時点でHoldID未取得のため決済不可"
                        conn.execute(
                            "UPDATE batch_items SET last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                            (msg, item["id"]),
                        )
                        self._log_event(
                            int(item["batch_job_id"]),
                            "ERROR",
                            "EOD_HOLD_ID_MISSING",
                            f"item={item['id']} symbol={item['symbol']} side={item['side']} remaining={remaining}",
                            conn=conn,
                        )
                    continue
                payload = self._build_exit_payload(item, "market", remaining, None, None, item["hold_id"])
                with self._conn() as conn:
                    self._log_payload_debug(int(item["batch_job_id"]), "EOD_PAYLOAD", payload, conn)
                eod_order_id, _ = self._api_post_order(api, payload)
            except Exception as e:
                with self._conn() as conn:
                    conn.execute("UPDATE batch_items SET status='ERROR', last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?", (str(e), item["id"]))
                    self._log_event(
                        int(item["batch_job_id"]),
                        "ERROR",
                        "EOD_FAILED",
                        f"item={item['id']} err={e}",
                        conn=conn,
                    )
                continue

            with self._conn() as conn:
                close_side = "sell" if item["side"] == "buy" else "buy"
                self._record_order(conn, int(item["id"]), "eod", eod_order_id, close_side, remaining, "market", None, None, item["hold_id"])
                conn.execute(
                    """
                    UPDATE batch_items
                    SET eod_order_id=?, status='EOD_MARKET_SENT', updated_at=datetime('now','+9 hours')
                    WHERE id=?
                    """,
                    (eod_order_id, item["id"]),
                )
                self._log_event(
                    int(item["batch_job_id"]),
                    "WARN",
                    "EOD_FORCE_CLOSE",
                    f"item={item['id']} eod_order_id={eod_order_id}",
                    conn=conn,
                )

        with self._conn() as conn:
            done_rows = conn.execute(
                """
                SELECT bi.id, bi.batch_job_id, bi.eod_order_id, oeod.status
                FROM batch_items bi
                LEFT JOIN orders oeod ON oeod.api_order_id = bi.eod_order_id
                WHERE bi.status='EOD_MARKET_SENT'
                """
            ).fetchall()
            for row in done_rows:
                if row["status"] == "FILLED":
                    conn.execute("UPDATE batch_items SET status='CLOSED', updated_at=datetime('now','+9 hours') WHERE id=?", (row["id"],))
                    self._log_event(int(row["batch_job_id"]), "INFO", "EOD_FILLED", f"item={row['id']}", conn=conn)

    def _finalize_jobs_step(self):
        with self._conn() as conn:
            jobs = conn.execute("SELECT id FROM batch_jobs WHERE status='RUNNING'").fetchall()
            for job in jobs:
                counts = conn.execute(
                    "SELECT status, COUNT(*) AS c FROM batch_items WHERE batch_job_id=? GROUP BY status",
                    (job["id"],),
                ).fetchall()
                by_status = {row["status"]: int(row["c"]) for row in counts}
                total = sum(by_status.values())
                closed = by_status.get("CLOSED", 0)
                errors = by_status.get("ERROR", 0)
                if total > 0 and closed == total:
                    conn.execute("UPDATE batch_jobs SET status='DONE', updated_at=datetime('now','+9 hours') WHERE id=?", (job["id"],))
                    self._log_event(int(job["id"]), "INFO", "BATCH_DONE", "全銘柄が決済完了", conn=conn)
                elif errors > 0:
                    conn.execute("UPDATE batch_jobs SET status='ERROR', updated_at=datetime('now','+9 hours') WHERE id=?", (job["id"],))
                    self._log_event(int(job["id"]), "ERROR", "BATCH_ERROR", f"error_items={errors}", conn=conn)
