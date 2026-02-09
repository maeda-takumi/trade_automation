# app.py
import sys
from PySide6.QtWidgets import QApplication
from ui_main import MainWindow
from logic import AppLogic

def main():
    app = QApplication(sys.argv)

    # メインウィンドウ
    window = MainWindow()

    # ロジック接続
    logic = AppLogic(window, db_path="data/kabus_trade.db")
    logic.bind()

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
