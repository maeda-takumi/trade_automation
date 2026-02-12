# logic.py
from __future__ import annotations

import json
import time
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
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
        self._init_db()

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
        except Exception as e:
            self.window.status_label.setText(f"監視ループでエラー: {e}")
        finally:
            self._worker_busy = False

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

    def _api_post_order(self, api: ApiAccount, payload: dict) -> str:
        token = self._get_api_token(api)
        if not token:
            raise RuntimeError(self._build_last_token_error_message("APIトークン取得に失敗"))
        base_url = self._normalize_base_url(api.base_url)
        try:
            data = self._request_json("POST", f"{base_url}/sendorder", headers={"X-API-KEY": token}, payload=payload)
        except urllib.error.HTTPError as e:
            body = self._read_http_error_body(e)
            err_payload = self._parse_error_json(body)
            err_code = (err_payload or {}).get("Code") or (err_payload or {}).get("code")
            current_exchange = payload.get("Exchange")
            if str(err_code) == "4001005" and current_exchange == 1:
                for retry_exchange in (9, 27):
                    retry_payload = dict(payload)
                    retry_payload["Exchange"] = retry_exchange
                    try:
                        data = self._request_json("POST", f"{base_url}/sendorder", headers={"X-API-KEY": token}, payload=retry_payload)
                        break
                    except urllib.error.HTTPError as retry_error:
                        retry_body = self._read_http_error_body(retry_error)
                        if retry_exchange == 27:
                            raise RuntimeError(self._build_http_error_with_body("発注API呼び出しに失敗", retry_error, retry_body)) from retry_error
                else:
                    raise RuntimeError(self._build_http_error_with_body("発注API呼び出しに失敗", e, body)) from e
            else:
                raise RuntimeError(self._build_http_error_with_body("発注API呼び出しに失敗", e, body)) from e
        order_id = data.get("OrderId") or data.get("OrderID")
        if not order_id:
            raise RuntimeError(f"注文IDが返却されませんでした: {data}")
        return str(order_id)

    @staticmethod
    def _side_to_kabu(side: str) -> str:
        return "2" if side == "buy" else "1"

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
        payload = {
            "Symbol": item["symbol"],
            "Exchange": self._normalize_exchange(item["exchange"]),
            "SecurityType": 1,
            "Side": self._side_to_kabu("sell" if item["side"] == "buy" else "buy"),
            "Qty": int(qty),
            "ExpireDay": 0,
            "AccountType": 4,
            "DelivType": 2,
        }
        if item["product"] == "cash":
            payload["CashMargin"] = 1
        else:
            payload["CashMargin"] = 3
            payload["MarginTradeType"] = 3
            if hold_id:
                payload["ClosePositions"] = [{"HoldID": hold_id, "Qty": int(qty)}]

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
                order_id = self._api_post_order(api, payload)
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
                    SET status='ENTRY_SENT', entry_order_id=?, updated_at=datetime('now','+9 hours')
                    WHERE id=?
                    """,
                    (order_id, item["id"]),
                )
                self._record_order(conn, int(item["id"]), "entry", order_id, item["side"], int(item["qty"]), item["entry_type"], item["entry_price"])
                self._log_event(
                    int(item["batch_job_id"]),
                    "INFO",
                    "ENTRY_SENT",
                    f"item={item['id']} order_id={order_id}",
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
                    avg_price = api_order.get("Price") or api_order.get("Details", [{}])[-1].get("RecPrice") if api_order.get("Details") else None
                    conn.execute(
                        """
                        UPDATE orders
                        SET status=?, cum_qty=?, avg_price=?, raw_json=?, last_sync_at=datetime('now','+9 hours'), updated_at=datetime('now','+9 hours')
                        WHERE api_order_id=?
                        """,
                        (status, cum_qty, float(avg_price) if avg_price else None, json.dumps(api_order, ensure_ascii=False), str(oid)),
                    )
                    if role == "entry":
                        new_status = "ENTRY_SENT"
                        if status == "FILLED":
                            new_status = "ENTRY_FILLED"
                        elif status == "PARTIAL":
                            new_status = "ENTRY_PARTIAL"
                        conn.execute(
                            """
                            UPDATE batch_items
                            SET status=?, entry_filled_qty=?, entry_avg_price=?, updated_at=datetime('now','+9 hours')
                            WHERE id=?
                            """,
                            (new_status, cum_qty, float(avg_price) if avg_price else None, item_id),
                        )

            for p in positions:
                symbol = str(p.get("Symbol") or "")
                hold_id = p.get("HoldID") or p.get("HoldId")
                leaves_qty = int(p.get("LeavesQty") or p.get("Qty") or 0)
                if not symbol or not hold_id or leaves_qty <= 0:
                    continue
                conn.execute(
                    """
                    UPDATE batch_items
                    SET hold_id=?
                    WHERE product='margin' AND symbol=? AND status IN ('ENTRY_FILLED','BRACKET_SENT','ENTRY_PARTIAL') AND (hold_id IS NULL OR hold_id='')
                    """,
                    (str(hold_id), symbol),
                )
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
                  AND bi.status='ENTRY_FILLED'
                  AND bi.tp_order_id IS NULL
                  AND bi.sl_order_id IS NULL
                """
            ).fetchall()

        for item in rows:
            if item["product"] == "margin" and not item["hold_id"]:
                continue
            qty = int(item["entry_filled_qty"] or item["qty"])
            if qty <= 0:
                continue
            avg = float(item["entry_avg_price"] or item["entry_price"] or 0)
            if avg <= 0:
                continue
            tp_abs = avg + float(item["tp_price"])
            sl_abs = avg + float(item["sl_trigger_price"])
            try:
                tp_payload = self._build_exit_payload(item, "limit", qty, tp_abs, None, item["hold_id"])
                tp_order_id = self._api_post_order(api, tp_payload)
                sl_payload = self._build_exit_payload(item, "stop", qty, None, sl_abs, item["hold_id"])
                sl_order_id = self._api_post_order(api, sl_payload)
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
                    SET status='BRACKET_SENT', tp_order_id=?, sl_order_id=?, updated_at=datetime('now','+9 hours')
                    WHERE id=?
                    """,
                    (tp_order_id, sl_order_id, item["id"]),
                )
                close_side = "sell" if item["side"] == "buy" else "buy"
                self._record_order(conn, int(item["id"]), "tp", tp_order_id, close_side, qty, "limit", tp_abs, None, item["hold_id"])
                self._record_order(conn, int(item["id"]), "sl", sl_order_id, close_side, qty, "stop", None, sl_abs, item["hold_id"])
                self._log_event(
                    int(item["batch_job_id"]),
                    "INFO",
                    "OCO_SENT",
                    f"item={item['id']} tp={tp_order_id} sl={sl_order_id}",
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
                    continue
                payload = self._build_exit_payload(item, "market", remaining, None, None, item["hold_id"])
                eod_order_id = self._api_post_order(api, payload)
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
