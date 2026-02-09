# ui_main.py
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize, QDateTime
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLineEdit, QCheckBox, QPushButton,
    QComboBox, QSpinBox, QMessageBox, QLabel,
    QStackedWidget, QFrame, QToolButton, QDateTimeEdit, QListWidget,
    QListWidgetItem, QAbstractItemView
)

from style import APP_QSS


class MainWindow(QMainWindow):
    # ロジック側が拾うためのシグナル
    request_save_api = Signal()
    request_load_api = Signal()
    request_submit_orders = Signal()
    request_clear_orders = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("kabuS Auto Trader (Prototype)")
        self.resize(1200, 720)

        root = QWidget()
        root.setStyleSheet(APP_QSS)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # --- Top Nav Card (Option / Kabu) ---
        self.top_nav = self._build_top_nav()
        layout.addWidget(self.top_nav)

        # --- Pages ---
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)

        # Page 0: 設定（API設定）
        self.page_settings = QWidget()
        p0 = QVBoxLayout(self.page_settings)
        p0.setContentsMargins(0, 0, 0, 0)
        p0.setSpacing(14)
        self.api_group = self._build_api_group()
        p0.addWidget(self.api_group)
        p0.addStretch(1)

        # Page 1: 自動取引（注文設定）
        self.page_trading = QWidget()
        p1 = QVBoxLayout(self.page_trading)
        p1.setContentsMargins(0, 0, 0, 0)
        p1.setSpacing(14)
        self.order_group = self._build_order_group()
        p1.addWidget(self.order_group, 1)

        self.pages.addWidget(self.page_settings)
        self.pages.addWidget(self.page_trading)

        # --- Status ---
        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

        self._wire_ui_events()

        # 初期表示：自動取引（好みで settings にしてもOK）
        self.switch_page(1)

    # ========= Top Nav =========
    def _build_top_nav(self) -> QFrame:
        card = QFrame()
        card.setObjectName("topNavCard")
        card.setFrameShape(QFrame.NoFrame)

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        self.btn_nav_settings = QToolButton()
        self.btn_nav_settings.setObjectName("navBtn")
        self.btn_nav_settings.setText("設定")
        self.btn_nav_settings.setIcon(QIcon("img/option.png"))
        self.btn_nav_settings.setIconSize(QSize(26, 26))
        self.btn_nav_settings.setCursor(Qt.PointingHandCursor)

        self.btn_nav_trading = QToolButton()
        self.btn_nav_trading.setObjectName("navBtn")
        self.btn_nav_trading.setText("自動取引")
        self.btn_nav_trading.setIcon(QIcon("img/kabu.png"))
        self.btn_nav_trading.setIconSize(QSize(26, 26))
        self.btn_nav_trading.setCursor(Qt.PointingHandCursor)

        row.addWidget(self.btn_nav_settings)
        row.addWidget(self.btn_nav_trading)
        row.addStretch(1)

        # 右側に小さく説明ラベル（任意）
        hint = QLabel("上のボタンで画面を切り替え")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        row.addWidget(hint)

        # click
        self.btn_nav_settings.clicked.connect(lambda: self.switch_page(0))
        self.btn_nav_trading.clicked.connect(lambda: self.switch_page(1))

        return card

    def switch_page(self, index: int):
        self.pages.setCurrentIndex(index)
        # active 表示
        self.btn_nav_settings.setProperty("active", index == 0)
        self.btn_nav_trading.setProperty("active", index == 1)
        # QSS反映
        self.btn_nav_settings.style().unpolish(self.btn_nav_settings)
        self.btn_nav_settings.style().polish(self.btn_nav_settings)
        self.btn_nav_trading.style().unpolish(self.btn_nav_trading)
        self.btn_nav_trading.style().polish(self.btn_nav_trading)

    # ========= API SETTINGS（中身は前のまま） =========
    def _build_api_group(self) -> QGroupBox:
        g = QGroupBox("API設定")
        form = QFormLayout(g)

        self.api_name = QLineEdit()
        self.api_base_url = QLineEdit()
        self.api_password = QLineEdit()
        self.api_password.setEchoMode(QLineEdit.Password)
        self.api_active = QCheckBox("有効")
        self.api_active.setChecked(True)

        form.addRow("名前", self.api_name)
        form.addRow("Base URL", self.api_base_url)
        form.addRow("APIパスワード", self.api_password)
        form.addRow("", self.api_active)

        btn_row = QHBoxLayout()
        self.btn_api_load = QPushButton("読込")
        self.btn_api_save = QPushButton("保存")
        self.btn_api_save.setObjectName("primary")  # 青ボタンに
        btn_row.addWidget(self.btn_api_load)
        btn_row.addWidget(self.btn_api_save)
        form.addRow(btn_row)

        api_hint = QLabel("※ api_accounts の最新(または有効)レコードを読み書きします")
        api_hint.setObjectName("muted")
        api_hint.setWordWrap(True)
        form.addRow(api_hint)

        return g

    # ========= ORDER SETTINGS（中身は前のまま） =========
    def _build_order_group(self) -> QGroupBox:
        g = QGroupBox("注文設定")
        v = QVBoxLayout(g)

        self.order_header = self._build_order_header()
        v.addWidget(self.order_header)

        self.orders_list = QListWidget()
        self.orders_list.setObjectName("orderList")
        self.orders_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.addWidget(self.orders_list)

        row_tools = QHBoxLayout()
        self.btn_add_row = QPushButton("行追加")
        self.btn_remove_row = QPushButton("選択行削除")
        row_tools.addWidget(self.btn_add_row)
        row_tools.addWidget(self.btn_remove_row)
        row_tools.addStretch(1)
        v.addLayout(row_tools)

        run_row = QHBoxLayout()

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

        hint = QLabel("行追加で複数注文に対応。損切/利確は差額入力（符号は内部で自動付与）")
        hint.setObjectName("muted")
        v.addWidget(hint)

        self.order_error_label = QLabel("")
        self.order_error_label.setObjectName("error")
        self.order_error_label.setWordWrap(True)
        v.addWidget(self.order_error_label)

        tool = QHBoxLayout()
        self.btn_clear = QPushButton("クリア")
        self.btn_clear.setObjectName("danger")
        tool.addWidget(self.btn_clear)
        tool.addStretch(1)

        self.btn_submit = QPushButton("送信（DB保存）")
        self.btn_submit.setObjectName("primary")  # 青
        tool.addWidget(self.btn_submit)
        v.addLayout(tool)

        self._add_order_row()
        self._update_order_field_visibility()
        self._validate_order_form()
        return g

    def _wire_ui_events(self):
        self.btn_api_save.clicked.connect(self.request_save_api.emit)
        self.btn_api_load.clicked.connect(self.request_load_api.emit)

        self.btn_clear.clicked.connect(self.request_clear_orders.emit)
        self.btn_submit.clicked.connect(self.request_submit_orders.emit)

        self.order_run_mode.currentIndexChanged.connect(self._handle_run_mode_change)

        self.btn_add_row.clicked.connect(self._add_order_row)
        self.btn_remove_row.clicked.connect(self._remove_selected_rows)

        self.order_run_mode.currentIndexChanged.connect(self._validate_order_form)
        self.order_scheduled_at.dateTimeChanged.connect(self._validate_order_form)

    def _build_form_row(self, label_text: str, widget: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        label = QLabel(label_text)
        label.setMinimumWidth(140)
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return row

    def _build_order_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(8)

        spacer = QLabel("")
        spacer.setFixedWidth(24)
        layout.addWidget(spacer)

        def add_label(text: str, width: int | None = None, stretch: int = 0):
            label = QLabel(text)
            label.setObjectName("muted")
            if width is not None:
                label.setFixedWidth(width)
            layout.addWidget(label, stretch)

        add_label("銘柄コード", stretch=1)
        add_label("信用/現物", width=90)
        add_label("売買", width=70)
        add_label("成行/指値", width=90)
        add_label("指値価格", width=110)
        add_label("損切差額", width=110)
        add_label("利確差額", width=110)
        return header

    def _build_order_row_widget(self) -> QWidget:
        row = QWidget()
        row.setObjectName("orderRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        row.select_box = QCheckBox()
        row.select_box.setFixedWidth(24)
        layout.addWidget(row.select_box)

        row.symbol_input = QLineEdit()
        row.symbol_input.textChanged.connect(self._validate_order_form)
        layout.addWidget(row.symbol_input, 1)

        row.product_input = QComboBox()
        row.product_input.addItem("現物", "cash")
        row.product_input.addItem("信用", "margin")
        row.product_input.currentIndexChanged.connect(self._validate_order_form)
        row.product_input.setFixedWidth(90)
        layout.addWidget(row.product_input)

        row.side_input = QComboBox()
        row.side_input.addItem("買", "buy")
        row.side_input.addItem("売", "sell")
        row.side_input.currentIndexChanged.connect(self._validate_order_form)
        row.side_input.setFixedWidth(70)
        layout.addWidget(row.side_input)

        row.entry_type_input = QComboBox()
        row.entry_type_input.addItem("成行", "market")
        row.entry_type_input.addItem("指値", "limit")
        row.entry_type_input.currentIndexChanged.connect(
            lambda _=None, row_widget=row: self._handle_entry_type_change(row_widget)
        )
        row.entry_type_input.setFixedWidth(90)
        layout.addWidget(row.entry_type_input)

        row.limit_price_input = QSpinBox()
        row.limit_price_input.setRange(1, 1_000_000_000)
        row.limit_price_input.setValue(1)
        row.limit_price_input.setSuffix(" 円")
        row.limit_price_input.valueChanged.connect(self._validate_order_form)
        row.limit_price_input.setEnabled(False)
        row.limit_price_input.setFixedWidth(110)
        layout.addWidget(row.limit_price_input)

        row.sl_diff_input = QSpinBox()
        row.sl_diff_input.setRange(1, 1_000_000_000)
        row.sl_diff_input.setValue(1)
        row.sl_diff_input.setSuffix(" 円")
        row.sl_diff_input.valueChanged.connect(self._validate_order_form)
        row.sl_diff_input.setFixedWidth(110)
        layout.addWidget(row.sl_diff_input)

        row.tp_diff_input = QSpinBox()
        row.tp_diff_input.setRange(1, 1_000_000_000)
        row.tp_diff_input.setValue(1)
        row.tp_diff_input.setSuffix(" 円")
        row.tp_diff_input.valueChanged.connect(self._validate_order_form)
        row.tp_diff_input.setFixedWidth(110)
        layout.addWidget(row.tp_diff_input)

        return row

    def _iter_order_row_widgets(self):
        for index in range(self.orders_list.count()):
            item = self.orders_list.item(index)
            widget = self.orders_list.itemWidget(item)
            if widget is not None:
                yield widget

    def _update_order_field_visibility(self):
        is_scheduled = self.order_run_mode.currentData() == "scheduled"
        self.schedule_row.setVisible(is_scheduled)

    def _handle_entry_type_change(self, row_widget: QWidget):
        self._set_row_limit_state(row_widget)
        self._validate_order_form()

    def _handle_run_mode_change(self):
        self._update_order_field_visibility()
        self._validate_order_form()

    def _add_order_row(self):
        row_widget = self._build_order_row_widget()
        item = QListWidgetItem()
        item.setSizeHint(row_widget.sizeHint())
        self.orders_list.addItem(item)
        self.orders_list.setItemWidget(item, row_widget)
        self._set_row_limit_state(row_widget)
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

    def _set_row_limit_state(self, row_widget: QWidget):
        entry_type = row_widget.entry_type_input.currentData()
        row_widget.limit_price_input.setEnabled(entry_type == "limit")

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
                "exchange": 9,
                "product": row_widget.product_input.currentData(),
                "side": side,
                "qty": 100,
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

        return errors

    def _validate_order_form(self):
        errors = self.get_order_validation_errors()
        if errors:
            self.order_error_label.setText(" / ".join(errors))
            self.btn_submit.setEnabled(False)
        else:
            self.order_error_label.setText("")
            self.btn_submit.setEnabled(True)


    def toast(self, title: str, message: str, error: bool = False):
        self.status_label.setText(message)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical if error else QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()
