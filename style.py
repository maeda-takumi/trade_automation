# style.py

# White main + Blue accent (tweak freely)
ACCENT = "#2563EB"      # blue
ACCENT_HOVER = "#1D4ED8"
BORDER = "#E5E7EB"
TEXT = "#111827"
MUTED = "#6B7280"
BG = "#FFFFFF"
CARD = "#FFFFFF"
FIELD_BG = "#F9FAFB"
DANGER = "#DC2626"

APP_QSS = f"""
/* ===== Base ===== */
QWidget {{
  background: {BG};
  color: {TEXT};
  font-size: 13px;
}}

QMainWindow::separator {{
  background: {BORDER};
  width: 1px;
  height: 1px;
}}

QLabel {{
  color: {TEXT};
}}
QLabel#muted {{
  color: {MUTED};
}}
QLabel#error {{
  color: {DANGER};
}}

/* ===== Status badge ===== */
QLabel#statusBadge {{
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 2px 10px;
  font-weight: 700;
  min-width: 56px;
}}
QLabel#statusBadge[variant="neutral"] {{
  background: #F3F4F6;
  color: #4B5563;
  border-color: #E5E7EB;
}}
QLabel#statusBadge[variant="info"] {{
  background: rgba(37, 99, 235, 0.14);
  color: #1D4ED8;
  border-color: rgba(37, 99, 235, 0.35);
}}
QLabel#statusBadge[variant="warning"] {{
  background: rgba(245, 158, 11, 0.16);
  color: #B45309;
  border-color: rgba(245, 158, 11, 0.35);
}}
QLabel#statusBadge[variant="success"] {{
  background: rgba(16, 185, 129, 0.14);
  color: #047857;
  border-color: rgba(16, 185, 129, 0.34);
}}
QLabel#statusBadge[variant="danger"] {{
  background: rgba(220, 38, 38, 0.14);
  color: #B91C1C;
  border-color: rgba(220, 38, 38, 0.34);
}}
/* ===== Card-like GroupBox ===== */
QGroupBox {{
  background: {CARD};
  border: 1px solid {BORDER};
  border-radius: 12px;
  margin-top: 14px;
  padding: 14px;
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  left: 14px;
  top: -10px;
  padding: 0 8px;
  background: {BG};
  color: {TEXT};
  font-weight: 700;
}}

/* ===== Inputs ===== */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
  background: {FIELD_BG};
  border: 1px solid {BORDER};
  border-radius: 1px;
  padding: 0px;
  selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
  border: 1px solid {ACCENT};
  background: {BG};
}}
QComboBox {{
  padding-right: 26px; /* room for arrow */
}}
QComboBox::drop-down {{
  border: none;
  width: 24px;
}}
QComboBox::down-arrow {{
  image: none; /* keep minimal; platform arrow */
}}

QCheckBox {{
  spacing: 8px;
}}
QCheckBox::indicator {{
  width: 18px;
  height: 18px;
  border-radius: 6px;
  border: 1px solid {BORDER};
  background: {BG};
}}
QCheckBox::indicator:checked {{
  background: {ACCENT};
  border: 1px solid {ACCENT};
}}

/* ===== Buttons ===== */
QPushButton {{
  border-radius: 10px;
  padding: 9px 12px;
  border: 1px solid {BORDER};
  background: {BG};
}}
QPushButton:hover {{
  background: #F3F4F6;
}}
QPushButton:pressed {{
  background: #EDEFF3;
}}

QPushButton#primary {{
  background: {ACCENT};
  border: 1px solid {ACCENT};
  color: white;
  font-weight: 700;
}}
QPushButton#primary:hover {{
  background: {ACCENT_HOVER};
  border: 1px solid {ACCENT_HOVER};
}}
QPushButton#danger {{
  background: {DANGER};
  border: 1px solid {DANGER};
  color: white;
  font-weight: 700;
}}
QPushButton:disabled {{
  opacity: 0.5;
}}

/* ===== Table ===== */
QTableWidget {{
  background: {BG};
  border: 1px solid {BORDER};
  border-radius: 12px;
  gridline-color: {BORDER};
}}
QHeaderView::section {{
  background: {BG};
  border: none;
  border-bottom: 1px solid {BORDER};
  padding: 10px 8px;
  font-weight: 700;
  color: {TEXT};
}}
QTableWidget::item {{
}}
QTableWidget::item:selected {{
  background: rgba(37, 99, 235, 0.15);
  color: {TEXT};
}}

/* ===== Order list ===== */
QListWidget#orderList {{
  background: {BG};
  border: 1px solid {BORDER};
  border-radius: 12px;
  padding: 4px;
}}
QListWidget::item {{
  border: none;
}}
QListWidget::item:selected {{
  background: rgba(37, 99, 235, 0.08);
}}
QWidget#orderRow {{
  background: {CARD};
  border: 1px solid {BORDER};
  border-radius: 10px;
}}

/* ===== Status label ===== */
QLabel#status {{
  background: {FIELD_BG};
  border: 1px solid {BORDER};
  border-radius: 12px;
  padding: 10px 12px;
  color: {MUTED};
}}
/* ===== Top nav card ===== */
QFrame#topNavCard {{
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 14px;
}}

/* ===== Icon buttons ===== */
QToolButton#navBtn {{
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  padding: 10px 12px;
  min-height: 56px;
  min-width: 56px;
  max-width: 56px;
  font-weight: 700;
}}
QToolButton#navBtn:hover {{
  background: #F9FAFB;
}}
QToolButton#navBtn[active="true"] {{
  border: 2px solid #2563EB;
  background: rgba(37, 99, 235, 0.08);
}}

/* icon above text */
QToolButton#navBtn {{
  qproperty-toolButtonStyle: ToolButtonTextUnderIcon;
}}

QFrame#statusCard {{
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
}}
"""
