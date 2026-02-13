from __future__ import annotations

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QLabel, QListWidget, QAbstractItemView,
    QHBoxLayout, QPushButton, QComboBox, QDateTimeEdit, QListWidgetItem
)
from ui.widgets.order_row_widget import OrderRowWidget

DEFAULT_EXCHANGE_CODE = 27


class TradeOrderPage(QWidget):
    def __init__(self, request_symbol_lookup):
        super().__init__()
        self._request_symbol_lookup_signal = request_symbol_lookup

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.order_group = self._build_order_group()
        layout.addWidget(self.order_group, 1)

    def _build_order_group(self) -> QGroupBox:
        g = QGroupBox("注文設定")
        v = QVBoxLayout(g)
        v.setContentsMargins(14, 18, 14, 12)
        v.setSpacing(14)

        lead = QLabel("1行ずつ条件を設定して、最後に右下の『送信』でまとめて登録します。")
        lead.setObjectName("muted")
        v.addWidget(lead)

        row_tools = QHBoxLayout()
        row_tools.setSpacing(10)
        self.btn_add_row = QPushButton("＋ 行追加")
        self.btn_remove_row = QPushButton("選択行を削除")
        row_tools.addWidget(self.btn_add_row)
        row_tools.addWidget(self.btn_remove_row)
        row_tools.addStretch(1)
        v.addLayout(row_tools)

        self.orders_list = QListWidget()
        self.orders_list.setObjectName("orderList")
        self.orders_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.addWidget(self.orders_list, 1)

        run_row = QHBoxLayout()
        run_row.setSpacing(12)
        self.order_run_mode = QComboBox()
        self.order_run_mode.addItem("即時実行", "immediate")
        self.order_run_mode.addItem("予約実行", "scheduled")
        run_row.addWidget(self._build_form_row("実行方式", self.order_run_mode))

        self.order_scheduled_at = QDateTimeEdit()
        self.order_scheduled_at.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.order_scheduled_at.setDateTime(QDateTime.currentDateTime())
        self.order_scheduled_at.setCalendarPopup(True)
        self.schedule_row = self._build_form_row("実行日時", self.order_scheduled_at)
        run_row.addWidget(self.schedule_row)
        run_row.addStretch(1)
        v.addLayout(run_row)

        hint = QLabel("損切/利確は差額入力です（買い: 利確＋/損切−、売り: 利確−/損切＋ を自動計算）。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.order_error_label = QLabel("")
        self.order_error_label.setObjectName("error")
        self.order_error_label.setWordWrap(True)
        v.addWidget(self.order_error_label)

        tool = QHBoxLayout()
        tool.setSpacing(12)
        self.btn_clear = QPushButton("入力をクリア")
        self.btn_clear.setObjectName("danger")
        tool.addWidget(self.btn_clear)
        tool.addStretch(1)

        self.btn_submit = QPushButton("注文を送信（DB保存）")
        self.btn_submit.setObjectName("primary")
        self.btn_submit.setMinimumWidth(220)
        tool.addWidget(self.btn_submit)
        v.addLayout(tool)

        self._add_order_row()
        self._update_order_field_visibility()
        self._validate_order_form()
        return g

    def _build_form_row(self, label_text: str, widget: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        label = QLabel(label_text)
        label.setMinimumWidth(80)
        label.setObjectName("muted")
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return row


    def wire_events(self, request_clear_orders, request_submit_orders):
        self.btn_clear.clicked.connect(request_clear_orders)
        self.btn_submit.clicked.connect(request_submit_orders)

        self.order_run_mode.currentIndexChanged.connect(self._handle_run_mode_change)
        self.order_run_mode.currentIndexChanged.connect(self._validate_order_form)
        self.order_scheduled_at.dateTimeChanged.connect(self._validate_order_form)

        self.btn_add_row.clicked.connect(self._add_order_row)
        self.btn_remove_row.clicked.connect(self._remove_selected_rows)

    def _iter_order_row_widgets(self):
        for index in range(self.orders_list.count()):
            item = self.orders_list.item(index)
            widget = self.orders_list.itemWidget(item)
            if widget is not None:
                yield widget

    def _update_order_field_visibility(self):
        is_scheduled = self.order_run_mode.currentData() == "scheduled"
        self.schedule_row.setVisible(is_scheduled)

    def _handle_run_mode_change(self):
        self._update_order_field_visibility()
        self._validate_order_form()

    def _request_symbol_lookup(self, row_widget: OrderRowWidget):
        symbol = row_widget.symbol_input.text().strip()
        if not symbol:
            row_widget.set_symbol_name("")
            row_widget.set_symbol_price("")
            return
        row_widget.set_symbol_name("取得中...")
        row_widget.set_symbol_price("取得中...")
        self._request_symbol_lookup_signal.emit(symbol, row_widget)

    def _on_symbol_text_change(self, _row_widget: OrderRowWidget):
        self._validate_order_form()

    def _add_order_row(self):
        row_widget = OrderRowWidget(self._validate_order_form, self._request_symbol_lookup, self._on_symbol_text_change)
        item = QListWidgetItem()
        item.setSizeHint(row_widget.sizeHint())
        self.orders_list.addItem(item)
        self.orders_list.setItemWidget(item, row_widget)
        self._validate_order_form()

    def _remove_selected_rows(self):
        selected_items = self.orders_list.selectedItems()
        if not selected_items:
            selected_items = [
                item for item in (self.orders_list.item(i) for i in range(self.orders_list.count()))
                if (widget := self.orders_list.itemWidget(item)) is not None
                and getattr(widget, "select_box", None)
                and widget.select_box.isChecked()
            ]
        selected_rows = sorted({self.orders_list.row(item) for item in selected_items}, reverse=True)
        for row in selected_rows:
            self.orders_list.takeItem(row)
        if self.orders_list.count() == 0:
            self._add_order_row()
        self._validate_order_form()

    def clear_orders(self):
        self.orders_list.clear()
        self._add_order_row()
        self.order_run_mode.setCurrentIndex(0)
        self.order_scheduled_at.setDateTime(QDateTime.currentDateTime())
        self._update_order_field_visibility()
        self._validate_order_form()

    def get_orders_payload(self):
        orders = []
        for row_widget in self._iter_order_row_widgets():
            symbol = row_widget.symbol_input.text().strip()
            if not symbol:
                continue

            entry_type = row_widget.entry_type_input.currentData()
            side = row_widget.side_input.currentData()
            tp_diff = int(row_widget.tp_diff_input.value())
            sl_diff = int(row_widget.sl_diff_input.value())

            if side == "buy":
                tp_signed = tp_diff
                sl_signed = -sl_diff
            else:
                tp_signed = -tp_diff
                sl_signed = sl_diff

            entry_price = None
            if entry_type == "limit":
                entry_price = int(row_widget.limit_price_input.value())

            orders.append({
                "symbol": symbol,
                "exchange": int(row_widget.exchange_input.currentData() or DEFAULT_EXCHANGE_CODE),
                "product": row_widget.product_input.currentData(),
                "side": side,
                "qty": int(row_widget.qty_input.value()),
                "entry_type": entry_type,
                "entry_price": entry_price,
                "tp_price": float(tp_signed),
                "sl_trigger_price": float(sl_signed),
                "batch_name": "手動バッチ",
                "memo": "",
                "run_mode": self.order_run_mode.currentData(),
                "scheduled_at": self.order_scheduled_at.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            })

        return orders

    def get_order_validation_errors(self) -> list[str]:
        errors = []
        if self.orders_list.count() == 0:
            errors.append("注文行を追加してください。")
            return errors

        for index, row_widget in enumerate(self._iter_order_row_widgets()):
            symbol = row_widget.symbol_input.text().strip()
            if not symbol:
                errors.append(f"{index + 1}行目: 銘柄コードを入力してください。")
                continue

            entry_type = row_widget.entry_type_input.currentData()
            if entry_type == "limit" and row_widget.limit_price_input.value() < 1:
                errors.append(f"{index + 1}行目: 指値価格は1円以上で指定してください。")

            if row_widget.sl_diff_input.value() < 1:
                errors.append(f"{index + 1}行目: 損切差額は1円以上で指定してください。")

            if row_widget.tp_diff_input.value() < 1:
                errors.append(f"{index + 1}行目: 利確差額は1円以上で指定してください。")

            if row_widget.qty_input.value() < 1:
                errors.append(f"{index + 1}行目: 数量は1以上で指定してください。")
        return errors

    def _validate_order_form(self):
        errors = self.get_order_validation_errors()
        if errors:
            self.order_error_label.setText(" / ".join(errors))
            self.btn_submit.setEnabled(False)
        else:
            self.order_error_label.setText("")
            self.btn_submit.setEnabled(True)


    def set_execution_status(self, target: str, entry: str, tp: str, sl: str):
        _ = (target, entry, tp, sl)

    def set_symbol_name(self, row_widget: QWidget, name: str):
        if getattr(row_widget, "set_symbol_name", None) is not None:
            row_widget.set_symbol_name(name)

    def set_symbol_price(self, row_widget: QWidget, price_text: str):
        if getattr(row_widget, "set_symbol_price", None) is not None:
            row_widget.set_symbol_price(price_text)

