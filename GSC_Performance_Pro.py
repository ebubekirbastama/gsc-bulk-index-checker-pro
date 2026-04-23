import sys
import os
import json
import csv
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QThread, Signal, QStringListModel
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QFileDialog, QMessageBox,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QCompleter, QLineEdit
)

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

APP_TITLE = "EBS GSC Elit Performans & İndeks Denetleyici"
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
                raise FileNotFoundError(f"{CLIENT_SECRET_FILE} bulunamadı.")
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
        result = self.service.sites().list().execute()
        return [i["siteUrl"] for i in result.get("siteEntry", [])]

    def inspect_url(self, site_url, inspection_url):
        body = {"inspectionUrl": inspection_url, "siteUrl": site_url, "languageCode": "tr-TR"}
        return self.service.urlInspection().index().inspect(body=body).execute()

    def get_performance(self, site_url, page_url, days=7):
            """
            Web arayüzü (GSC Paneli) ile birebir eşleşme sağlayan performans sorgusu.
            'final' dataState ve 3 günlük tarih ofseti ile kesinleşmiş verileri çeker.
            """
            # GSC Paneli 'Kesinleşmiş' verileri genellikle 2-3 gün geriden gösterir.
            # Masaüstü paneliyle birebir eşleşme için bitiş tarihini 3 gün öncesine sabitliyoruz.
            end_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=days + 3)).strftime("%Y-%m-%d")
            
            body = {
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["page"],
                "dimensionFilterGroups": [{
                    "filters": [
                        {"dimension": "page", "operator": "equals", "expression": page_url}
                    ]
                }],
                # 'final' parametresi Google'ın yuvarlama yapmadan önceki kesinleşmiş rakamlarını getirir.
                "dataState": "final" 
            }
            
            try:
                res = self.service.searchanalytics().query(siteUrl=site_url, body=body).execute()
                rows = res.get("rows", [])
                
                if rows:
                    data = rows[0]
                    # API'den gelen float değerleri direkt kullanıyoruz
                    return {
                        "clicks": data.get("clicks", 0),
                        "impressions": data.get("impressions", 0), # Yuvarlanmamış ham veri
                        "ctr": data.get("ctr", 0),
                        "position": data.get("position", 0)
                    }
            except Exception as e:
                print(f"Performans verisi çekilirken hata: {e}")
                
            return {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}

# -----------------------------
# İşlemci İş Parçacığı
# -----------------------------
class InspectionWorker(QThread):
    row_result = Signal(dict)
    progress_changed = Signal(int, int)
    finished_summary = Signal(dict)

    def __init__(self, gsc_client, site_url, urls, days):
        super().__init__()
        self.gsc_client = gsc_client
        self.site_url = site_url
        self.urls = urls
        self.days = days

    def run(self):
        total = len(self.urls)
        indexed, not_indexed, errors = 0, 0, 0
        results = []

        for i, url in enumerate(self.urls, start=1):
            try:
                raw_inspect = self.gsc_client.inspect_url(self.site_url, url)
                parsed = self.parse_inspect(url, raw_inspect)

                perf = self.gsc_client.get_performance(self.site_url, url, self.days)
                parsed.update({
                    "clicks": int(perf.get("clicks", 0)),
                    "impressions": int(perf.get("impressions", 0)),
                    "ctr": f"{perf.get('ctr', 0)*100:.2f}%",
                    "pos": f"{perf.get('position', 0):.1f}"
                })

                if parsed["status"] == "BAŞARILI": indexed += 1
                elif parsed["status"] == "HATA": errors += 1
                else: not_indexed += 1

                results.append(parsed)
                self.row_result.emit(parsed)
            except Exception as e:
                err_row = {"url": url, "status": "HATA", "coverage": str(e), "clicks": 0, "impressions": 0, "ctr": "0%", "pos": "0", "last_crawl": "-"}
                self.row_result.emit(err_row)
                errors += 1

            self.progress_changed.emit(i, total)
        self.finished_summary.emit({"total": total, "indexed": indexed, "not_indexed": not_indexed, "errors": errors, "results": results})

    def parse_inspect(self, url, response):
        idx = response.get("inspectionResult", {}).get("indexStatusResult", {})
        verdict = idx.get("verdict", "").lower()
        status = "BAŞARILI" if "indexed" in verdict or "allowed" in verdict else "OLUMSUZ"
        if "error" in verdict: status = "HATA"
        
        last_crawl = idx.get("lastCrawlTime", "-")
        if last_crawl != "-":
            try: last_crawl = datetime.fromisoformat(last_crawl.replace("Z", "+00:00")).strftime("%d.%m.%Y")
            except: pass

        return {
            "url": url, "status": status, "coverage": idx.get("coverageState", "-"),
            "last_crawl": last_crawl, "indexing": idx.get("indexingState", "-")
        }

# -----------------------------
# UI Bileşenleri
# -----------------------------
class StatCard(QFrame):
    def __init__(self, title, value="0", color="#D4AF37"):
        super().__init__()
        self.setObjectName("StatCard")
        self.v_lbl = QLabel(value); self.v_lbl.setObjectName("StatValue"); self.v_lbl.setAlignment(Qt.AlignCenter); self.v_lbl.setStyleSheet(f"color: {color};")
        self.t_lbl = QLabel(title); self.t_lbl.setObjectName("StatTitle"); self.t_lbl.setAlignment(Qt.AlignCenter)
        l = QVBoxLayout(self); l.addWidget(self.v_lbl); l.addWidget(self.t_lbl)
    def set_value(self, v): self.v_lbl.setText(str(v))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE); self.resize(1300, 900)
        self.gsc = GSCClient(); self.current_results = []
        self.build_ui(); self.apply_styles()

    def build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        self.root = QVBoxLayout(central); self.root.setContentsMargins(20,20,20,20); self.root.setSpacing(15)

        # Header
        header = QFrame(); header.setObjectName("TopPanel"); h_lay = QHBoxLayout(header)
        title_v = QVBoxLayout(); title_v.addWidget(QLabel("EBS GSC ELİT ANALİZ", objectName="MainTitle"))
        title_v.addWidget(QLabel("URL İndeks, Performans ve Sitemap Denetleyici", objectName="SubTitle"))
        
        self.login_btn = QPushButton("Google Hesabı Bağla"); self.login_btn.clicked.connect(self.handle_login)
        self.time_combo = QComboBox()
        self.time_combo.addItems([
            "Son 24 Saat", "Son 7 Gün", "Son 28 Gün", "Son 3 Ay", 
            "Son 6 Ay", "Son 12 Ay", "Son 16 Ay"
        ])
        self.time_combo.setCurrentIndex(1)

        h_lay.addLayout(title_v, 1); h_lay.addWidget(self.time_combo); h_lay.addWidget(self.login_btn)
        self.root.addWidget(header)

        # Input Section
        input_card = QFrame(); input_card.setObjectName("Card"); i_lay = QVBoxLayout(input_card)
        
        # Mülk Seçimi (Aramalı)
        prop_h = QHBoxLayout()
        prop_h.addWidget(QLabel("Mülk (Domain) Ara/Seç:"), 0)
        self.prop_combo = QComboBox()
        self.prop_combo.setEditable(True)
        self.prop_combo.setInsertPolicy(QComboBox.NoInsert)
        self.prop_combo.lineEdit().setPlaceholderText("Domain adını yazın...")
        prop_h.addWidget(self.prop_combo, 1)
        
        self.fetch_sitemap_btn = QPushButton("Sitemap'ten Çek"); self.fetch_sitemap_btn.clicked.connect(self.fetch_sitemap_urls)
        self.fetch_sitemap_btn.setStyleSheet("background-color: #3949AB; color: white;")
        prop_h.addWidget(self.fetch_sitemap_btn)
        i_lay.addLayout(prop_h)

        self.url_text = QTextEdit(); self.url_text.setPlaceholderText("URL'leri buraya yapıştırın veya Sitemap butonunu kullanın..."); i_lay.addWidget(self.url_text)
        
        btn_row = QHBoxLayout()
        self.check_btn = QPushButton("ANALİZİ BAŞLAT", objectName="ActionBtn"); self.check_btn.setEnabled(False)
        self.check_btn.clicked.connect(self.start_check)
        btn_row.addStretch(); btn_row.addWidget(self.check_btn)
        i_lay.addLayout(btn_row)
        
        self.progress = QProgressBar(); i_lay.addWidget(self.progress)
        self.root.addWidget(input_card)

        # Stats & Table
        res_card = QFrame(); res_card.setObjectName("Card"); r_lay = QVBoxLayout(res_card)
        stats_h = QHBoxLayout()
        self.c_total = StatCard("TOPLAM"); self.c_idx = StatCard("İNDEKSLİ", color="#4CAF50")
        self.c_click = StatCard("TIKLAMA", color="#2196F3"); self.c_imp = StatCard("GÖSTERİM", color="#9C27B0")
        stats_h.addWidget(self.c_total); stats_h.addWidget(self.c_idx); stats_h.addWidget(self.c_click); stats_h.addWidget(self.c_imp)
        r_lay.addLayout(stats_h)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["URL", "Durum", "Tık", "Gös", "CTR", "Poz", "Tarama", "Kapsam"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        r_lay.addWidget(self.table)
        
        self.root.addWidget(res_card, 1)

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #0F0F0F; color: #EEE; font-family: 'Segoe UI', sans-serif; }
            QFrame#Card, QFrame#TopPanel { background-color: #1A1A1A; border: 1px solid #333; border-radius: 10px; padding: 10px; }
            QFrame#StatCard { background-color: #222; border-radius: 8px; min-width: 150px; }
            QLabel#MainTitle { font-size: 22px; font-weight: bold; color: #D4AF37; }
            QLabel#SubTitle { color: #888; font-size: 12px; }
            QLabel#StatValue { font-size: 26px; font-weight: bold; }
            QPushButton { background-color: #D4AF37; color: #000; font-weight: bold; border-radius: 5px; padding: 10px 18px; }
            QPushButton#ActionBtn { background-color: #2E7D32; color: white; min-width: 250px; font-size: 14px; }
            QPushButton:disabled { background-color: #444; color: #888; }
            QTextEdit, QComboBox, QLineEdit { background-color: #252525; border: 1px solid #444; border-radius: 4px; padding: 8px; color: #FFF; }
            QTableWidget { background-color: #1A1A1A; gridline-color: #333; border-radius: 5px; }
            QHeaderView::section { background-color: #222; color: #D4AF37; font-weight: bold; border: none; padding: 10px; }
            QProgressBar { border: 1px solid #333; border-radius: 5px; text-align: center; height: 15px; }
            QProgressBar::chunk { background-color: #D4AF37; }
        """)

    def handle_login(self):
        try:
            self.gsc.login()
            props = self.gsc.list_properties()
            self.prop_combo.clear()
            self.prop_combo.addItems(props)
            
            # Arama tamamlama özelliği
            completer = QCompleter(props)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.prop_combo.setCompleter(completer)
            
            self.check_btn.setEnabled(True)
            QMessageBox.information(self, "Başarılı", "Google Search Console bağlantısı doğrulandı.")
        except Exception as e: QMessageBox.critical(self, "Hata", f"Bağlantı Hatası: {str(e)}")

    def fetch_sitemap_urls(self):
        site_url = self.prop_combo.currentText()
        if not site_url:
            QMessageBox.warning(self, "Uyarı", "Lütfen önce bir mülk seçin.")
            return
        
        sitemap_url = site_url if site_url.endswith('/') else site_url + '/'
        sitemap_url += "sitemap.xml"
        
        try:
            response = requests.get(sitemap_url, timeout=10)
            response.raise_for_status()
            
            # XML Parse (Namespace handling)
            root = ET.fromstring(response.content)
            namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            
            urls = []
            for loc in root.findall('.//ns:loc', namespaces):
                urls.append(loc.text)
            
            if urls:
                self.url_text.clear()
                self.url_text.setPlainText("\n".join(urls))
                QMessageBox.information(self, "Başarılı", f"{len(urls)} adet URL sitemap'ten çekildi.")
            else:
                QMessageBox.warning(self, "Hata", "Sitemap bulundu ancak içinde URL tespit edilemedi.")
        except Exception as e:
            QMessageBox.critical(self, "Sitemap Hatası", f"Sitemap okunamadı (Site haritanızın {sitemap_url} olduğundan emin olun):\n{str(e)}")

    def start_check(self):
        urls = [x.strip() for x in self.url_text.toPlainText().splitlines() if x.strip()]
        if not urls: 
            QMessageBox.warning(self, "Uyarı", "Analiz edilecek URL bulunamadı.")
            return
        
        # Zaman aralığını eşle
        days_map = {0: 1, 1: 7, 2: 28, 3: 90, 4: 180, 5: 365, 6: 480}
        days = days_map.get(self.time_combo.currentIndex(), 7)

        self.table.setRowCount(0); self.current_results = []
        self.progress.setValue(0)
        self.check_btn.setEnabled(False)

        self.worker = InspectionWorker(self.gsc, self.prop_combo.currentText(), urls, days)
        self.worker.row_result.connect(self.add_row)
        self.worker.progress_changed.connect(lambda c, t: self.progress.setValue(int(c/t*100)))
        self.worker.finished_summary.connect(self.finish_ui)
        self.worker.start()

    def add_row(self, d):
        self.current_results.append(d)
        r = self.table.rowCount(); self.table.insertRow(r)
        
        url_item = QTableWidgetItem(d["url"])
        url_item.setToolTip(d["url"])
        self.table.setItem(r, 0, url_item)
        
        status_item = QTableWidgetItem(d["status"])
        if d["status"] == "BAŞARILI": status_item.setForeground(QColor("#4CAF50"))
        elif d["status"] == "OLUMSUZ": status_item.setForeground(QColor("#F44336"))
        
        self.table.setItem(r, 1, status_item)
        self.table.setItem(r, 2, QTableWidgetItem(str(d["clicks"])))
        self.table.setItem(r, 3, QTableWidgetItem(str(d["impressions"])))
        self.table.setItem(r, 4, QTableWidgetItem(d["ctr"]))
        self.table.setItem(r, 5, QTableWidgetItem(d["pos"]))
        self.table.setItem(r, 6, QTableWidgetItem(d["last_crawl"]))
        self.table.setItem(r, 7, QTableWidgetItem(d["coverage"]))

    def finish_ui(self, s):
        self.c_total.set_value(s["total"])
        self.c_idx.set_value(s["indexed"])
        total_clicks = sum(int(x["clicks"]) for x in s["results"])
        total_imps = sum(int(x["impressions"]) for x in s["results"])
        self.c_click.set_value(total_clicks)
        self.c_imp.set_value(total_imps)
        
        self.check_btn.setEnabled(True)
        QMessageBox.information(self, "Tamamlandı", "Tüm URL'ler kontrol edildi ve performans verileri işlendi.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion") # Karanlık tema ile daha uyumlu modern görünüm
    w = MainWindow(); w.show()
    sys.exit(app.exec())
