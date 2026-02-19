from __future__ import annotations

import json
import sqlite3
import urllib.error
from datetime import datetime
from typing import Optional

from logic_types import ApiAccount


class AppWorkerMixin:
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
                retry_errors: list[str] = []
                for retry_exchange in retry_exchanges:
                    retry_payload = dict(payload)
                    retry_payload["Exchange"] = retry_exchange
                    try:
                        data = self._request_json("POST", f"{base_url}/sendorder", headers={"X-API-KEY": token}, payload=retry_payload)
                        resolved_exchange = self._normalize_exchange(retry_exchange)
                        break
                    except urllib.error.HTTPError as retry_error:
                        retry_body = self._read_http_error_body(retry_error)
                        retry_errors.append(
                            f"exchange={retry_exchange} {self._build_http_error_with_body('発注API呼び出しに失敗', retry_error, retry_body)}"
                        )
                        if retry_exchange == retry_exchanges[-1]:
                            retry_trace = " | retry=" + " ; ".join(retry_errors) if retry_errors else ""
                            raise RuntimeError(
                                f"{self._build_http_error_with_body('発注API呼び出しに失敗', retry_error, retry_body)}{retry_trace} / payload={payload_ctx}"
                            ) from retry_error
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
            # 現物の発注は DelivType=2（お預り金）を基本に統一する。
            # DelivType=0 の決済系指定だと、環境により 4001005（パラメータ変換エラー）
            # が返るケースがあるため、利確/損切（現物売却）でも 2 を使う。
            payload["DelivType"] = 2
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
            "ClosePositions": payload.get("ClosePositions"),
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

                if len(matched) != 1:
                    candidate_ids = ",".join(str(c["id"]) for c in side_filtered) if side_filtered else "-"
                    for candidate in candidates:
                        self._log_event(
                            int(candidate["batch_job_id"]),
                            "WARN",
                            "HOLD_ID_MATCH_NOT_FOUND",
                            f"symbol={symbol} hold_id={hold_id} source={hold_id_source or '<unknown>'} leaves_qty={leaves_qty} side={position_side or '<unknown>'} matched={len(matched)} candidates=[{candidate_ids}]",
                            conn=conn,
                        )
                        conn.execute(
                            "UPDATE batch_items SET last_error=?, updated_at=datetime('now','+9 hours') WHERE id=?",
                            (f"HoldID紐付け不可: symbol={symbol} leaves_qty={leaves_qty} matched={len(matched)}", int(candidate["id"])),
                        )
                    continue

                target = matched[0]
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
