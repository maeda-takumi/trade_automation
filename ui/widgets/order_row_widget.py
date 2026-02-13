from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QCheckBox,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QPushButton,
)
class OrderRowWidget(QWidget):
    def __init__(self, on_validate, on_symbol_lookup, on_symbol_text_change):
        super().__init__()
        self._on_validate = on_validate
        self._on_symbol_lookup = on_symbol_lookup
        self._on_symbol_text_change = on_symbol_text_change

        self.setObjectName("orderRow")

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)


        self.select_box = QCheckBox()
        self.select_box.setFixedWidth(20)
        root.addWidget(self.select_box)

        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("コード")
        self.symbol_input.setFixedWidth(100)
        self.symbol_input.editingFinished.connect(lambda: self._on_symbol_lookup(self))
        self.symbol_input.textChanged.connect(self._handle_symbol_text_change)
        self.symbol_input.textChanged.connect(self._on_validate)
        root.addWidget(self.symbol_input)

        self.btn_lookup = QPushButton("検索")
        self.btn_lookup.clicked.connect(lambda: self._on_symbol_lookup(self))
        self.btn_lookup.setFixedWidth(56)
        root.addWidget(self.btn_lookup)

        self.symbol_name_label = QLineEdit()
        self.symbol_name_label.setReadOnly(True)
        self.symbol_name_label.setPlaceholderText("銘柄名")
        self.symbol_name_label.setFixedWidth(130)
        root.addWidget(self.symbol_name_label)

        self.current_price_label = QLineEdit()
        self.current_price_label.setReadOnly(True)
        self.current_price_label.setPlaceholderText("現在値")
        self.current_price_label.setFixedWidth(90)
        root.addWidget(self.current_price_label)

        self.side_input = QComboBox()
        self.side_input.addItem("買", "buy")
        self.side_input.addItem("売", "sell")
        self.side_input.currentIndexChanged.connect(self._on_validate)
        self.side_input.setFixedWidth(58)
        root.addWidget(self.side_input)

        self.qty_input = QSpinBox()
        self.qty_input.setRange(1, 1_000_000)
        self.qty_input.setValue(100)
        self.qty_input.valueChanged.connect(self._on_validate)
        self.qty_input.setPrefix("株")
        self.qty_input.setFixedWidth(84)
        root.addWidget(self.qty_input)

        self.product_input = QComboBox()
        self.product_input.addItem("現物", "cash")
        self.product_input.addItem("信用", "margin")
        self.product_input.currentIndexChanged.connect(self._on_validate)
        self.product_input.setFixedWidth(64)
        root.addWidget(self.product_input)

        self.exchange_input = QComboBox()
        self.exchange_input.addItem("SOR", 9)
        self.exchange_input.addItem("東証+", 27)
        self.exchange_input.addItem("東証", 1)
        self.exchange_input.addItem("名証", 3)
        self.exchange_input.addItem("福証", 5)
        self.exchange_input.addItem("札証", 6)
        self.exchange_input.setCurrentIndex(0)
        self.exchange_input.currentIndexChanged.connect(self._on_validate)
        self.exchange_input.setFixedWidth(72)
        root.addWidget(self.exchange_input)

        self.entry_type_input = QComboBox()
        self.entry_type_input.addItem("成行", "market")
        self.entry_type_input.addItem("指値", "limit")
        self.entry_type_input.currentIndexChanged.connect(self._handle_entry_type_change)
        self.entry_type_input.setFixedWidth(64)
        root.addWidget(self.entry_type_input)

        self.limit_price_input = QSpinBox()
        self.limit_price_input.setRange(1, 1_000_000_000)
        self.limit_price_input.setValue(1)
        self.limit_price_input.setPrefix("指")
        self.limit_price_input.valueChanged.connect(self._on_validate)
        self.limit_price_input.setEnabled(False)
        self.limit_price_input.setFixedWidth(92)
        root.addWidget(self.limit_price_input)

        self.sl_diff_input = QSpinBox()
        self.sl_diff_input.setRange(1, 1_000_000_000)
        self.sl_diff_input.setValue(1)
        self.sl_diff_input.setPrefix("SL")
        self.sl_diff_input.valueChanged.connect(self._on_validate)
        self.sl_diff_input.setFixedWidth(84)
        root.addWidget(self.sl_diff_input)

        self.tp_diff_input = QSpinBox()
        self.tp_diff_input.setRange(1, 1_000_000_000)
        self.tp_diff_input.setValue(1)
        self.tp_diff_input.setPrefix("TP")
        self.tp_diff_input.valueChanged.connect(self._on_validate)
        self.tp_diff_input.setFixedWidth(84)
        root.addWidget(self.tp_diff_input)

        root.addStretch(1)

    def _handle_entry_type_change(self):
        self.limit_price_input.setEnabled(self.entry_type_input.currentData() == "limit")
        self._on_validate()

    def _handle_symbol_text_change(self):
        self.set_symbol_name("")
        self.set_symbol_price("")
        self._on_symbol_text_change(self)

    def set_symbol_name(self, name: str):
        self.symbol_name_label.setText(name)

    def set_symbol_price(self, price_text: str):
        self.current_price_label.setText(price_text)
