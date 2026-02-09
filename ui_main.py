# ui_main.py
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLineEdit, QCheckBox, QPushButton, QTableWidget, QTableWidgetItem,
    QComboBox, QSpinBox, QDoubleSpinBox, QMessageBox, QLabel,
    QStackedWidget, QFrame, QToolButton
)

from style import APP_QSS


class MainWindow(QMainWindow):
    # ロジック側が拾うためのシグナル
    request_save_api = Signal()
    request_load_api = Signal()
    request_submit_orders = Signal()
    request_clear_orders = Signal()
    request_add_row = Signal()
    request_remove_selected_rows = Signal()

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
        g = QGroupBox("注文設定（複数同時）")
        v = QVBoxLayout(g)

        tool = QHBoxLayout()
        self.btn_add_row = QPushButton("行追加")
        self.btn_remove_rows = QPushButton("選択行削除")
        self.btn_clear = QPushButton("全クリア")
        self.btn_clear.setObjectName("danger")  # 任意：赤
        tool.addWidget(self.btn_add_row)
        tool.addWidget(self.btn_remove_rows)
        tool.addWidget(self.btn_clear)
        tool.addStretch(1)

        self.btn_submit = QPushButton("送信（DB保存）")
        self.btn_submit.setObjectName("primary")  # 青
        tool.addWidget(self.btn_submit)
        v.addLayout(tool)

        self.orders_table = QTableWidget(0, 11)
        self.orders_table.setHorizontalHeaderLabels([
            "銘柄", "市場", "商品", "売買", "数量",
            "成行/指値", "指値価格", "利確(TP)価格",
            "逆指値トリガー", "バッチ名", "メモ"
        ])
        self.orders_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.orders_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.orders_table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.orders_table, 1)

        # 初期1行
        self._insert_default_row()

        return g

    def _wire_ui_events(self):
        self.btn_api_save.clicked.connect(self.request_save_api.emit)
        self.btn_api_load.clicked.connect(self.request_load_api.emit)

        self.btn_add_row.clicked.connect(self.request_add_row.emit)
        self.btn_remove_rows.clicked.connect(self.request_remove_selected_rows.emit)
        self.btn_clear.clicked.connect(self.request_clear_orders.emit)
        self.btn_submit.clicked.connect(self.request_submit_orders.emit)

    # --- 以下、テーブル関連は既存のまま（省略せず必要なら移植） ---
    def _insert_default_row(self):
        self.add_order_row(
            symbol="9432",
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

    def add_order_row(self, symbol, exchange, product, side, qty, entry_type, entry_price, tp_price, sl_trigger, batch_name, memo):
        r = self.orders_table.rowCount()
        self.orders_table.insertRow(r)

        self.orders_table.setItem(r, 0, QTableWidgetItem(symbol))

        exchange_cb = QComboBox()
        exchange_cb.addItems(["1", "3", "5"])
        exchange_cb.setCurrentText(str(exchange))
        self.orders_table.setCellWidget(r, 1, exchange_cb)

        product_cb = QComboBox()
        product_cb.addItems(["cash", "margin"])
        product_cb.setCurrentText(product)
        self.orders_table.setCellWidget(r, 2, product_cb)

        side_cb = QComboBox()
        side_cb.addItems(["buy", "sell"])
        side_cb.setCurrentText(side)
        self.orders_table.setCellWidget(r, 3, side_cb)

        qty_sp = QSpinBox()
        qty_sp.setRange(1, 10_000_000)
        qty_sp.setValue(int(qty))
        self.orders_table.setCellWidget(r, 4, qty_sp)

        et_cb = QComboBox()
        et_cb.addItems(["market", "limit"])
        et_cb.setCurrentText(entry_type)
        self.orders_table.setCellWidget(r, 5, et_cb)

        ep_sp = QDoubleSpinBox()
        ep_sp.setRange(0, 1_000_000_000)
        ep_sp.setDecimals(2)
        ep_sp.setValue(float(entry_price))
        self.orders_table.setCellWidget(r, 6, ep_sp)

        tp_sp = QDoubleSpinBox()
        tp_sp.setRange(0, 1_000_000_000)
        tp_sp.setDecimals(2)
        tp_sp.setValue(float(tp_price))
        self.orders_table.setCellWidget(r, 7, tp_sp)

        sl_sp = QDoubleSpinBox()
        sl_sp.setRange(0, 1_000_000_000)
        sl_sp.setDecimals(2)
        sl_sp.setValue(float(sl_trigger))
        self.orders_table.setCellWidget(r, 8, sl_sp)

        self.orders_table.setItem(r, 9, QTableWidgetItem(batch_name))
        self.orders_table.setItem(r, 10, QTableWidgetItem(memo))

    def remove_selected_rows(self):
        rows = sorted({idx.row() for idx in self.orders_table.selectionModel().selectedRows()}, reverse=True)
        for r in rows:
            self.orders_table.removeRow(r)

    def clear_orders(self):
        self.orders_table.setRowCount(0)

    def get_orders_payload(self):
        payload = []
        for r in range(self.orders_table.rowCount()):
            symbol_item = self.orders_table.item(r, 0)
            batch_item = self.orders_table.item(r, 9)
            memo_item = self.orders_table.item(r, 10)

            symbol = (symbol_item.text().strip() if symbol_item else "")
            batch_name = (batch_item.text().strip() if batch_item else "手動バッチ")
            memo = (memo_item.text().strip() if memo_item else "")

            exchange = self._cell_combo(r, 1)
            product = self._cell_combo(r, 2)
            side = self._cell_combo(r, 3)
            entry_type = self._cell_combo(r, 5)

            qty = self._cell_spin_int(r, 4)
            entry_price = self._cell_spin_float(r, 6)
            tp_price = self._cell_spin_float(r, 7)
            sl_trigger = self._cell_spin_float(r, 8)

            payload.append({
                "symbol": symbol,
                "exchange": int(exchange),
                "product": product,
                "side": side,
                "qty": int(qty),
                "entry_type": entry_type,
                "entry_price": float(entry_price),
                "tp_price": float(tp_price),
                "sl_trigger_price": float(sl_trigger),
                "batch_name": batch_name,
                "memo": memo,
            })
        return payload

    def _cell_combo(self, row, col):
        w = self.orders_table.cellWidget(row, col)
        return w.currentText() if isinstance(w, QComboBox) else ""

    def _cell_spin_int(self, row, col):
        w = self.orders_table.cellWidget(row, col)
        return int(w.value()) if isinstance(w, QSpinBox) else 0

    def _cell_spin_float(self, row, col):
        w = self.orders_table.cellWidget(row, col)
        return float(w.value()) if isinstance(w, QDoubleSpinBox) else 0.0

    def toast(self, title: str, message: str, error: bool = False):
        self.status_label.setText(message)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical if error else QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()
