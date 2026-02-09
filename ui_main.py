# ui_main.py
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize, QDateTime
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLineEdit, QCheckBox, QPushButton,
    QComboBox, QSpinBox, QMessageBox, QLabel,
    QStackedWidget, QFrame, QToolButton, QDateTimeEdit
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

        form = QVBoxLayout()
        form.setSpacing(10)

        self.order_symbol = QLineEdit()
        form.addWidget(self._build_form_row("銘柄コード", self.order_symbol))

        self.order_product = QComboBox()
        self.order_product.addItem("現物", "cash")
        self.order_product.addItem("信用", "margin")
        form.addWidget(self._build_form_row("信用/現物", self.order_product))

        self.order_side = QComboBox()
        self.order_side.addItem("買", "buy")
        self.order_side.addItem("売", "sell")
        form.addWidget(self._build_form_row("売買", self.order_side))

        self.order_entry_type = QComboBox()
        self.order_entry_type.addItem("成行", "market")
        self.order_entry_type.addItem("指値", "limit")
        form.addWidget(self._build_form_row("成行/指値", self.order_entry_type))

        self.order_limit_price = QSpinBox()
        self.order_limit_price.setRange(1, 1_000_000_000)
        self.order_limit_price.setValue(1)
        self.order_limit_price.setSuffix(" 円")
        self.limit_price_row = self._build_form_row("指値価格", self.order_limit_price)
        form.addWidget(self.limit_price_row)

        self.order_sl_diff = QSpinBox()
        self.order_sl_diff.setRange(1, 1_000_000_000)
        self.order_sl_diff.setValue(1)
        self.order_sl_diff.setSuffix(" 円")
        form.addWidget(self._build_form_row("損切差額", self.order_sl_diff))

        self.order_tp_diff = QSpinBox()
        self.order_tp_diff.setRange(1, 1_000_000_000)
        self.order_tp_diff.setValue(1)
        self.order_tp_diff.setSuffix(" 円")
        form.addWidget(self._build_form_row("利確差額", self.order_tp_diff))

        self.order_run_mode = QComboBox()
        self.order_run_mode.addItem("即時実行", "immediate")
        self.order_run_mode.addItem("予約実行", "scheduled")
        form.addWidget(self._build_form_row("実行方式", self.order_run_mode))

        self.order_scheduled_at = QDateTimeEdit()
        self.order_scheduled_at.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.order_scheduled_at.setDateTime(QDateTime.currentDateTime())
        self.order_scheduled_at.setCalendarPopup(True)
        self.schedule_row = self._build_form_row("実行日時", self.order_scheduled_at)
        form.addWidget(self.schedule_row)

        hint = QLabel("損切/利確は差額入力（符号は内部で自動付与）")
        hint.setObjectName("muted")
        form.addWidget(hint)

        v.addLayout(form)

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

        self._update_order_field_visibility()
        self._validate_order_form()
        return g

    def _wire_ui_events(self):
        self.btn_api_save.clicked.connect(self.request_save_api.emit)
        self.btn_api_load.clicked.connect(self.request_load_api.emit)

        self.btn_clear.clicked.connect(self.request_clear_orders.emit)
        self.btn_submit.clicked.connect(self.request_submit_orders.emit)

        self.order_entry_type.currentIndexChanged.connect(self._handle_entry_type_change)
        self.order_run_mode.currentIndexChanged.connect(self._handle_run_mode_change)

        self.order_symbol.textChanged.connect(self._validate_order_form)
        self.order_product.currentIndexChanged.connect(self._validate_order_form)
        self.order_side.currentIndexChanged.connect(self._validate_order_form)
        self.order_entry_type.currentIndexChanged.connect(self._validate_order_form)
        self.order_limit_price.valueChanged.connect(self._validate_order_form)
        self.order_sl_diff.valueChanged.connect(self._validate_order_form)
        self.order_tp_diff.valueChanged.connect(self._validate_order_form)
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

    def _update_order_field_visibility(self):
        is_limit = self.order_entry_type.currentData() == "limit"
        self.limit_price_row.setVisible(is_limit)

        is_scheduled = self.order_run_mode.currentData() == "scheduled"
        self.schedule_row.setVisible(is_scheduled)

    def _handle_entry_type_change(self):
        self._update_order_field_visibility()
        self._validate_order_form()

    def _handle_run_mode_change(self):
        self._update_order_field_visibility()
        self._validate_order_form()

    def clear_orders(self):
        self.order_symbol.clear()
        self.order_product.setCurrentIndex(0)
        self.order_side.setCurrentIndex(0)
        self.order_entry_type.setCurrentIndex(0)
        self.order_limit_price.setValue(1)
        self.order_sl_diff.setValue(1)
        self.order_tp_diff.setValue(1)
        self.order_run_mode.setCurrentIndex(0)
        self.order_scheduled_at.setDateTime(QDateTime.currentDateTime())
        self._update_order_field_visibility()
        self._validate_order_form()

    def get_orders_payload(self):
        symbol = self.order_symbol.text().strip()
        if not symbol:
            return []

        entry_type = self.order_entry_type.currentData()
        side = self.order_side.currentData()
        tp_diff = int(self.order_tp_diff.value())
        sl_diff = int(self.order_sl_diff.value())

        if side == "buy":
            tp_signed = tp_diff
            sl_signed = -sl_diff
        else:
            tp_signed = -tp_diff
            sl_signed = sl_diff

        return [{
            "symbol": symbol,
            "exchange": 9,
            "product": self.order_product.currentData(),
            "side": side,
            "qty": 100,
            "entry_type": entry_type,
            "entry_price": int(self.order_limit_price.value()) if entry_type == "limit" else None,
            "tp_price": float(tp_signed),
            "sl_trigger_price": float(sl_signed),
            "batch_name": "手動バッチ",
            "memo": "",
            "run_mode": self.order_run_mode.currentData(),
            "scheduled_at": self.order_scheduled_at.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
        }]

    def get_order_validation_errors(self) -> list[str]:
        errors = []
        if not self.order_symbol.text().strip():
            errors.append("銘柄コードを入力してください。")

        if self.order_entry_type.currentData() == "limit":
            if self.order_limit_price.value() < 1:
                errors.append("指値価格は1円以上で指定してください。")

        if self.order_sl_diff.value() < 1:
            errors.append("損切差額は1円以上で指定してください。")
        if self.order_tp_diff.value() < 1:
            errors.append("利確差額は1円以上で指定してください。")

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
