import sys
import os
import json
import csv
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QFileDialog, QMessageBox,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy, QProgressBar
)

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


APP_TITLE = "EBS GSC Elit İndeks Denetleyici"
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


# -----------------------------
# Google Search Console İstemcisi
# -----------------------------
class GSCClient:
    def __init__(self):
        self.creds = None
        self.service = None

    def login(self):
        creds = None

        if os.path.exists(TOKEN_FILE):
            try:
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            except Exception:
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(
                    f"{CLIENT_SECRET_FILE} bulunamadı. Lütfen Google OAuth JSON dosyasını ana dizine ekleyin."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

            with open(TOKEN_FILE, "w", encoding="utf-8") as token:
                token.write(creds.to_json())

        self.creds = creds
        self.service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
        return True

    def is_logged_in(self):
        return self.service is not None

    def list_properties(self):
        if not self.service:
            raise RuntimeError("Önce giriş yapılmalı.")

        result = self.service.sites().list().execute()
        entries = result.get("siteEntry", [])
        properties = []

        for item in entries:
            site_url = item.get("siteUrl", "")
            permission = item.get("permissionLevel", "")
            properties.append({
                "siteUrl": site_url,
                "permissionLevel": permission
            })

        return properties

    def inspect_url(self, site_url, inspection_url, language_code="tr-TR"):
        if not self.service:
            raise RuntimeError("Önce giriş yapılmalı.")

        body = {
            "inspectionUrl": inspection_url,
            "siteUrl": site_url,
            "languageCode": language_code
        }

        response = self.service.urlInspection().index().inspect(body=body).execute()
        return response


# -----------------------------
# İşlemci İş Parçacığı (Worker)
# -----------------------------
class InspectionWorker(QThread):
    row_result = Signal(dict)
    progress_changed = Signal(int, int)
    finished_summary = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, gsc_client, site_url, urls):
        super().__init__()
        self.gsc_client = gsc_client
        self.site_url = site_url
        self.urls = urls

    def run(self):
        results = []
        total = len(self.urls)

        indexed = 0
        not_indexed = 0
        errors = 0

        for i, url in enumerate(self.urls, start=1):
            try:
                raw = self.gsc_client.inspect_url(self.site_url, url)
                row = self.parse_result(url, raw)

                if row["status"] == "BAŞARILI":
                    indexed += 1
                elif row["status"] == "HATA":
                    errors += 1
                else:
                    not_indexed += 1

                results.append(row)
                self.row_result.emit(row)

            except Exception as e:
                row = {
                    "url": url,
                    "status": "HATA",
                    "coverage": f"API Hatası: {str(e)}",
                    "last_crawl": "-",
                    "indexing": "-",
                    "inspect_result": "-",
                    "canonical": "-",
                }
                errors += 1
                results.append(row)
                self.row_result.emit(row)

            self.progress_changed.emit(i, total)

        summary = {
            "total": total,
            "indexed": indexed,
            "not_indexed": not_indexed,
            "errors": errors,
            "results": results
        }
        self.finished_summary.emit(summary)

    @staticmethod
    def parse_result(url, response):
        inspection = response.get("inspectionResult", {})
        index_status = inspection.get("indexStatusResult", {})

        verdict = index_status.get("verdict", "")
        coverage = index_status.get("coverageState", "")
        indexing_state = index_status.get("indexingState", "")
        last_crawl = index_status.get("lastCrawlTime", "")
        google_canonical = index_status.get("googleCanonical", "")
        user_canonical = index_status.get("userCanonical", "")

        status = "BAŞARILI"

        coverage_text = (coverage or "").lower()
        indexing_text = (indexing_state or "").lower()
        verdict_text = (verdict or "").lower()

        if "error" in verdict_text:
            status = "HATA"
        elif "indexed" in coverage_text or "allowed" in indexing_text:
            status = "BAŞARILI"
        else:
            status = "OLUMSUZ"

        return {
            "url": url,
            "status": status,
            "coverage": coverage or "-",
            "last_crawl": InspectionWorker.format_date(last_crawl),
            "indexing": indexing_state or "-",
            "inspect_result": verdict or "-",
            "canonical": google_canonical or user_canonical or "-"
        }

    @staticmethod
    def format_date(dt_str):
        if not dt_str:
            return "-"
        try:
            dt_str = dt_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return dt_str


# -----------------------------
# Elit İstatistik Kartı
# -----------------------------
class StatCard(QFrame):
    def __init__(self, title, value="0", color="#D4AF37"): # Gold rengi varsayılan
        super().__init__()
        self.setObjectName("StatCard")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatValue")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet(f"color: {color};")

        self.title_label = QLabel(title)
        self.title_label.setObjectName("StatTitle")
        self.title_label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(5)
        layout.addWidget(self.value_label)
        layout.addWidget(self.title_label)

    def set_value(self, value):
        self.value_label.setText(str(value))


# -----------------------------
# Ana Pencere
# -----------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1240, 850)

        self.gsc = GSCClient()
        self.worker = None
        self.current_results = []

        self.build_ui()
        self.apply_styles()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        self.root_layout = QVBoxLayout(central)
        self.root_layout.setContentsMargins(25, 25, 25, 25)
        self.root_layout.setSpacing(20)

        # Üst Panel (Header)
        header = QFrame()
        header.setObjectName("TopPanel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(25, 20, 25, 20)

        header_left = QVBoxLayout()
        title = QLabel("GSC ELİT İNDEKS PANELİ")
        title.setObjectName("MainTitle")
        subtitle = QLabel("Gelişmiş URL Denetimi ve İndeksleme Analiz Aracı")
        subtitle.setObjectName("SubTitle")

        header_left.addWidget(title)
        header_left.addWidget(subtitle)

        header_right = QHBoxLayout()
        header_right.setSpacing(12)

        self.login_btn = QPushButton("Google Hesabını Bağla")
        self.login_btn.clicked.connect(self.handle_login)

        self.reload_properties_btn = QPushButton("Mülkleri Yenile")
        self.reload_properties_btn.setObjectName("SecondaryBtn")
        self.reload_properties_btn.clicked.connect(self.load_properties)
        self.reload_properties_btn.setEnabled(False)

        header_right.addWidget(self.login_btn)
        header_right.addWidget(self.reload_properties_btn)

        header_layout.addLayout(header_left, 1)
        header_layout.addLayout(header_right)
        self.root_layout.addWidget(header)

        # Giriş Bölümü
        input_card = QFrame()
        input_card.setObjectName("Card")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(20, 20, 20, 20)

        prop_row = QHBoxLayout()
        prop_label = QLabel("İncelenecek Mülk (Property):")
        prop_label.setObjectName("FieldLabel")
        self.property_combo = QComboBox()
        prop_row.addWidget(prop_label)
        prop_row.addWidget(self.property_combo, 1)
        input_layout.addLayout(prop_row)

        input_layout.addSpacing(10)
        self.url_label = QLabel("URL Listesi (Her satıra bir adet - Maks 1000)")
        self.url_label.setObjectName("FieldLabel")
        input_layout.addWidget(self.url_label)

        self.url_text = QTextEdit()
        self.url_text.setPlaceholderText("https://site.com/icerik-1/\nhttps://site.com/icerik-2/")
        self.url_text.textChanged.connect(self.update_url_count)
        input_layout.addWidget(self.url_text)

        bottom_row = QHBoxLayout()
        self.url_count_label = QLabel("0 URL girildi")
        self.url_count_label.setObjectName("InfoLabel")

        self.check_btn = QPushButton("ANALİZİ BAŞLAT")
        self.check_btn.setObjectName("ActionBtn")
        self.check_btn.clicked.connect(self.start_check)
        self.check_btn.setEnabled(False)

        bottom_row.addWidget(self.url_count_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.check_btn)
        input_layout.addLayout(bottom_row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        input_layout.addWidget(self.progress)

        self.root_layout.addWidget(input_card)

        # Sonuç Bölümü
        result_card = QFrame()
        result_card.setObjectName("Card")
        result_layout = QVBoxLayout(result_card)
        
        stats_grid = QGridLayout()
        self.total_card = StatCard("TOPLAM URL", "0", "#FFFFFF")
        self.indexed_card = StatCard("İNDEKSLENMİŞ", "0", "#4CAF50")
        self.not_indexed_card = StatCard("İNDEKS YOK", "0", "#F44336")
        self.errors_card = StatCard("HATA", "0", "#FFC107")

        stats_grid.addWidget(self.total_card, 0, 0)
        stats_grid.addWidget(self.indexed_card, 0, 1)
        stats_grid.addWidget(self.not_indexed_card, 0, 2)
        stats_grid.addWidget(self.errors_card, 0, 3)
        result_layout.addLayout(stats_grid)

        # Tablo
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "URL Adresi", "Durum", "Kapsam (Coverage)", "Son Tarama", "İndeksleme", "Sonuç"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        result_layout.addWidget(self.table)

        # Export Butonları
        export_row = QHBoxLayout()
        self.export_csv_btn = QPushButton("CSV Olarak Aktar")
        self.export_json_btn = QPushButton("JSON Olarak Aktar")
        self.export_csv_btn.setObjectName("SecondaryBtn")
        self.export_json_btn.setObjectName("SecondaryBtn")
        self.export_csv_btn.clicked.connect(self.export_csv)
        self.export_json_btn.clicked.connect(self.export_json)
        self.export_csv_btn.setEnabled(False)
        self.export_json_btn.setEnabled(False)

        export_row.addWidget(self.export_csv_btn)
        export_row.addWidget(self.export_json_btn)
        export_row.addStretch()
        result_layout.addLayout(export_row)

        self.root_layout.addWidget(result_card, 1)

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #121212;
                color: #E0E0E0;
                font-family: 'Segoe UI', Roboto, sans-serif;
            }

            QFrame#Card, QFrame#TopPanel {
                background-color: #1E1E1E;
                border: 1px solid #333333;
                border-radius: 12px;
            }

            QFrame#StatCard {
                background-color: #252525;
                border: 1px solid #3D3D3D;
                border-radius: 10px;
            }

            QLabel#MainTitle {
                font-size: 22px;
                font-weight: bold;
                color: #D4AF37;
                letter-spacing: 1px;
            }

            QLabel#SubTitle {
                font-size: 12px;
                color: #888888;
            }

            QLabel#FieldLabel {
                font-weight: bold;
                color: #BBBBBB;
            }

            QLabel#StatValue {
                font-size: 32px;
                font-weight: bold;
            }

            QLabel#StatTitle {
                font-size: 11px;
                color: #AAAAAA;
                text-transform: uppercase;
            }

            QPushButton {
                background-color: #D4AF37;
                color: #000000;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: bold;
                min-height: 18px;
            }

            QPushButton:hover {
                background-color: #F3CF55;
            }

            QPushButton#SecondaryBtn {
                background-color: #333333;
                color: #FFFFFF;
                border: 1px solid #444444;
            }

            QPushButton#SecondaryBtn:hover {
                background-color: #444444;
            }

            QPushButton#ActionBtn {
                background-color: #1F6A4D;
                color: white;
                font-size: 14px;
            }

            QPushButton:disabled {
                background-color: #2A2A2A;
                color: #555555;
            }

            QTextEdit, QComboBox {
                background-color: #252525;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 8px;
                color: #FFFFFF;
            }

            QTableWidget {
                background-color: #1E1E1E;
                gridline-color: #333333;
                border-radius: 8px;
                selection-background-color: #333333;
            }

            QHeaderView::section {
                background-color: #252525;
                color: #D4AF37;
                padding: 8px;
                border: none;
                font-weight: bold;
            }

            QProgressBar {
                border: 1px solid #333333;
                border-radius: 5px;
                background-color: #121212;
                text-align: center;
                height: 20px;
            }

            QProgressBar::chunk {
                background-color: #D4AF37;
            }
        """)

    def show_error(self, message):
        QMessageBox.critical(self, "Hata", message)

    def show_info(self, message):
        QMessageBox.information(self, "Bilgi", message)

    def update_url_count(self):
        urls = self.get_clean_urls()
        self.url_count_label.setText(f"{len(urls)} adet benzersiz URL algılandı")

    def get_clean_urls(self):
        lines = [x.strip() for x in self.url_text.toPlainText().splitlines() if x.strip()]
        unique = []
        seen = set()
        for line in lines:
            if line not in seen:
                seen.add(line)
                unique.append(line)
        return unique

    def handle_login(self):
        try:
            self.login_btn.setEnabled(False)
            self.login_btn.setText("Bağlanıyor...")
            QApplication.processEvents()

            self.gsc.login()
            self.load_properties()

            self.reload_properties_btn.setEnabled(True)
            self.check_btn.setEnabled(True)
            self.show_info("Google bağlantısı başarılı. Mülk listesi güncellendi.")
        except Exception as e:
            self.show_error(str(e))
        finally:
            self.login_btn.setEnabled(True)
            self.login_btn.setText("Google Hesabını Bağla")

    def load_properties(self):
        try:
            properties = self.gsc.list_properties()
            self.property_combo.clear()

            if not properties:
                self.show_error("Erişilebilir mülk bulunamadı.")
                return

            for item in properties:
                label = f'{item["siteUrl"]} ({item["permissionLevel"]})'
                self.property_combo.addItem(label, item["siteUrl"])

        except Exception as e:
            self.show_error(f"Mülk listesi yüklenemedi:\n{e}")

    def reset_results(self):
        self.current_results = []
        self.table.setRowCount(0)
        self.total_card.set_value(0)
        self.indexed_card.set_value(0)
        self.not_indexed_card.set_value(0)
        self.errors_card.set_value(0)
        self.progress.setValue(0)
        self.export_csv_btn.setEnabled(False)
        self.export_json_btn.setEnabled(False)

    def start_check(self):
        if not self.gsc.is_logged_in():
            self.show_error("Lütfen önce Google ile oturum açın.")
            return

        site_url = self.property_combo.currentData()
        if not site_url:
            self.show_error("Lütfen bir mülk seçin.")
            return

        urls = self.get_clean_urls()
        if not urls:
            self.show_error("Lütfen en az bir URL girin.")
            return

        if len(urls) > 1000:
            self.show_error("Google API limitleri nedeniyle tek seferde maks 1000 URL girilebilir.")
            return

        self.reset_results()
        self.check_btn.setEnabled(False)
        self.worker = InspectionWorker(self.gsc, site_url, urls)
        self.worker.row_result.connect(self.add_result_row)
        self.worker.progress_changed.connect(self.update_progress)
        self.worker.finished_summary.connect(self.finish_check)
        self.worker.error_signal.connect(self.show_error)
        self.worker.start()

    def add_result_row(self, row):
        self.current_results.append(row)
        r = self.table.rowCount()
        self.table.insertRow(r)

        self.table.setItem(r, 0, QTableWidgetItem(row["url"]))

        status_item = QTableWidgetItem(row["status"])
        if row["status"] == "BAŞARILI":
            status_item.setForeground(QColor("#4CAF50"))
        elif row["status"] == "OLUMSUZ":
            status_item.setForeground(QColor("#F44336"))
        else:
            status_item.setForeground(QColor("#FFC107"))

        self.table.setItem(r, 1, status_item)
        self.table.setItem(r, 2, QTableWidgetItem(row["coverage"]))
        self.table.setItem(r, 3, QTableWidgetItem(row["last_crawl"]))
        self.table.setItem(r, 4, QTableWidgetItem(row["indexing"]))
        self.table.setItem(r, 5, QTableWidgetItem(row["inspect_result"]))

    def update_progress(self, current, total):
        if total > 0:
            value = int((current / total) * 100)
            self.progress.setValue(value)
            self.progress.setFormat(f"İşleniyor: {current}/{total} (%p%)")

    def finish_check(self, summary):
        self.total_card.set_value(summary["total"])
        self.indexed_card.set_value(summary["indexed"])
        self.not_indexed_card.set_value(summary["not_indexed"])
        self.errors_card.set_value(summary["errors"])

        self.export_csv_btn.setEnabled(True)
        self.export_json_btn.setEnabled(True)
        self.check_btn.setEnabled(True)

        self.show_info(f"Analiz Tamamlandı!\n\nİndekslenen: {summary['indexed']}\nİndeks Almayan: {summary['not_indexed']}\nHatalı: {summary['errors']}")

    def export_csv(self):
        if not self.current_results: return
        file_path, _ = QFileDialog.getSaveFileName(self, "CSV Olarak Sakla", "gsc_analiz_sonuc.csv", "CSV Dosyası (*.csv)")
        if not file_path: return
        try:
            headers = ["url", "status", "coverage", "last_crawl", "indexing", "inspect_result", "canonical"]
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(self.current_results)
            self.show_info("Veriler CSV olarak başarıyla dışa aktarıldı.")
        except Exception as e:
            self.show_error(str(e))

    def export_json(self):
        if not self.current_results: return
        file_path, _ = QFileDialog.getSaveFileName(self, "JSON Olarak Sakla", "gsc_analiz_sonuc.json", "JSON Dosyası (*.json)")
        if not file_path: return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.current_results, f, ensure_ascii=False, indent=2)
            self.show_info("Veriler JSON olarak başarıyla dışa aktarıldı.")
        except Exception as e:
            self.show_error(str(e))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
