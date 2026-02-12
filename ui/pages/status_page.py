from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QHBoxLayout, QScrollArea, QGridLayout

class StatusPage(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        self.title = QLabel("状況確認（未決済注文）")
        self.title.setStyleSheet("font-weight: 700; font-size: 15px;")
        title_row.addWidget(self.title)

        self.summary = QLabel("0 件")
        self.summary.setObjectName("muted")
        self.summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        title_row.addWidget(self.summary, 1)
        layout.addLayout(title_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        self.cards_layout = QGridLayout(container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(12)
        self.cards_layout.setVerticalSpacing(12)
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)

        self.empty_label = QLabel("未決済注文はありません。")
        self.empty_label.setObjectName("muted")
        layout.addWidget(self.empty_label)

    def set_cards(self, items: list[dict]):
        while self.cards_layout.count() > 0:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.summary.setText(f"{len(items)} 件")
        self.empty_label.setVisible(len(items) == 0)

        columns = 3
        for index, data in enumerate(items):
            card = self._build_card(data)
            row = index // columns
            col = index % columns
            self.cards_layout.addWidget(card, row, col)

    def _build_card(self, data: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("statusCard")
        card.setFixedWidth(340)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        header = QLabel(f"#{data.get('id')} | {data.get('symbol','-')} | {data.get('side_label','-')}")
        header.setStyleSheet("font-weight: 700;")
        v.addWidget(header)

        qty_badge = QLabel(f"{data.get('qty','-')}株")
        qty_badge.setObjectName("statusBadge")
        v.addWidget(qty_badge)

        item_status = QLabel(f"状態: {data.get('item_status_label', '-') }")
        item_status.setObjectName("statusBadge")
        v.addWidget(item_status)

        order_status = QLabel(f"注文: {data.get('entry_status_label', '-') } | 利確: {data.get('tp_status_label', '-') } | 損切: {data.get('sl_status_label', '-') }")
        order_status.setWordWrap(True)
        v.addWidget(order_status)

        v.addWidget(QLabel(f"約定数量: {data.get('entry_filled_qty', 0)} | クローズ数量: {data.get('closed_qty', 0)}"))

        last_error = (data.get("last_error") or "").strip()
        if last_error:
            err_label = QLabel(f"エラー: {last_error.splitlines()[0]}")
            err_label.setObjectName("error")
            err_label.setWordWrap(True)
            v.addWidget(err_label)

        return card
