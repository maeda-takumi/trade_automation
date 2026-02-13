from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QStackedWidget, QFrame, QToolButton, QLabel, QHBoxLayout, QMessageBox

from style import APP_QSS
from ui.pages.settings_page import SettingsPage
from ui.pages.trade_order_page import TradeOrderPage

# DEFAULT_EXCHANGE_CODE = 27

class MainWindow(QMainWindow):
    request_save_api = Signal()
    request_load_api = Signal()
    request_submit_orders = Signal()
    request_clear_orders = Signal()
    request_symbol_lookup = Signal(str, object)

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

        self.page_settings = SettingsPage()
        self.page_trading = TradeOrderPage(self.request_symbol_lookup)

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

        nav_defs = [
            ("設定", "img/option.png", 0),
            ("取引注文", "img/kabu.png", 1),
        ]

        self.nav_buttons: list[tuple[int, QToolButton]] = []
        for label, icon_path, page_index in nav_defs:
            btn = QToolButton()
            btn.setObjectName("navBtn")
            btn.setText(label)
            btn.setIcon(QIcon(icon_path))
            btn.setIconSize(QSize(26, 26))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=page_index: self.switch_page(i))
            self.nav_buttons.append((page_index, btn))
            row.addWidget(btn)

        row.addStretch(1)

        # 右側に小さく説明ラベル（任意）
        hint = QLabel("上のボタンで画面を切り替え")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        row.addWidget(hint)

        return card

    def switch_page(self, index: int):
        self.pages.setCurrentIndex(index)
        for page_index, btn in self.nav_buttons:
            btn.setProperty("active", page_index == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _wire_ui_events(self):
        self.page_settings.btn_api_save.clicked.connect(self.request_save_api.emit)
        self.page_settings.btn_api_load.clicked.connect(self.request_load_api.emit)
        self.page_trading.wire_events(self.request_clear_orders.emit, self.request_submit_orders.emit)

    @property
    def api_name(self):
        return self.page_settings.api_name
    
    @property
    def api_base_url(self):
        return self.page_settings.api_base_url
    
    @property
    def api_password(self):
        return self.page_settings.api_password
    
    @property
    def api_active(self):
        return self.page_settings.api_active

    def clear_orders(self):
        self.page_trading.clear_orders()

    def get_order_validation_errors(self) -> list[str]:
        return self.page_trading.get_order_validation_errors()

    def get_orders_payload(self):
        return self.page_trading.get_orders_payload()
    
    def set_symbol_name(self, row_widget: QWidget, name: str):
        self.page_trading.set_symbol_name(row_widget, name)

    def set_symbol_price(self, row_widget: QWidget, price_text: str):
        self.page_trading.set_symbol_price(row_widget, price_text)

    def set_execution_status(self, target: str, entry: str, tp: str, sl: str):
        self.page_trading.set_execution_status(target, entry, tp, sl)

    def set_open_order_cards(self, items: list[dict]):
        # 取引注文画面では状況確認カードを表示しないため no-op
        _ = items
        
    def toast(self, title: str, message: str, error: bool = False):
        self.status_label.setText(message)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical if error else QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()
