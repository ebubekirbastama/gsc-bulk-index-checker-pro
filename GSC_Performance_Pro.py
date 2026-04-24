import sys
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QCursor
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QFrame, QMessageBox,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QCompleter, QGraphicsDropShadowEffect
)

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- Konfigürasyon ---
APP_TITLE = "EBS GSC Elit Performans & İndeks Denetleyici"
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

class GSCClient:
    def __init__(self):
        self.creds = None
        self.service = None

    def login(self):
        creds = None
        if os.path.exists(TOKEN_FILE):
            try:
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            except Exception: creds = None

        if creds and creds.expired and creds.refresh_token:
            try: creds.refresh(Request())
            except Exception: creds = None

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

    def list_properties(self):
        result = self.service.sites().list().execute()
        return [i["siteUrl"] for i in result.get("siteEntry", [])]

    def inspect_url(self, site_url, inspection_url):
        body = {"inspectionUrl": inspection_url, "siteUrl": site_url, "languageCode": "tr-TR"}
        return self.service.urlInspection().index().inspect(body=body).execute()

    def get_performance(self, site_url, page_url, days=7):
        end_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days + 3)).strftime("%Y-%m-%d")
        body = {
            "startDate": start_date, "endDate": end_date, "dimensions": ["page"],
            "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]}],
            "dataState": "final" 
        }
        try:
            res = self.service.searchanalytics().query(siteUrl=site_url, body=body).execute()
            rows = res.get("rows", [])
            if rows:
                return {"clicks": rows[0].get("clicks", 0), "impressions": rows[0].get("impressions", 0),
                        "ctr": rows[0].get("ctr", 0), "position": rows[0].get("position", 0)}
        except: pass
        return {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}

class InspectionWorker(QThread):
    row_result = Signal(dict)
    progress_changed = Signal(int, int)
    finished_summary = Signal(dict)

    def __init__(self, gsc_client, site_url, urls, mode):
        super().__init__()
        self.gsc_client, self.site_url, self.urls, self.mode = gsc_client, site_url, urls, mode

    def run(self):
        total = len(self.urls)
        indexed = 0
        for i, url in enumerate(self.urls, start=1):
            try:
                raw_inspect = self.gsc_client.inspect_url(self.site_url, url)
                idx = raw_inspect.get("inspectionResult", {}).get("indexStatusResult", {})
                verdict = idx.get("verdict", "").lower()
                status = "BAŞARILI" if "indexed" in verdict or "allowed" in verdict else "OLUMSUZ"
                
                last_crawl = idx.get("lastCrawlTime", "-")
                if last_crawl != "-":
                    try: last_crawl = datetime.fromisoformat(last_crawl.replace("Z", "+00:00")).strftime("%d.%m.%Y")
                    except: pass

                data = {"url": url, "status": status, "last_crawl": last_crawl, "coverage": idx.get("coverageState", "-")}

                if self.mode == "COMPARE":
                    p6 = self.gsc_client.get_performance(self.site_url, url, 180)
                    p12 = self.gsc_client.get_performance(self.site_url, url, 365)
                    p16 = self.gsc_client.get_performance(self.site_url, url, 480)
                    data.update({"compare": True, "c6": f"{int(p6['clicks'])} / {int(p6['impressions'])}",
                                 "c12": f"{int(p12['clicks'])} / {int(p12['impressions'])}",
                                 "c16": f"{int(p16['clicks'])} / {int(p16['impressions'])}"})
                else:
                    p = self.gsc_client.get_performance(self.site_url, url, self.mode)
                    data.update({"compare": False, "clicks": int(p['clicks']), "imps": int(p['impressions']),
                                 "ctr": f"{p['ctr']*100:.2f}%", "pos": f"{p['position']:.1f}"})

                if status == "BAŞARILI": indexed += 1
                self.row_result.emit(data)
            except:
                self.row_result.emit({"url": url, "status": "HATA", "compare": self.mode == "COMPARE"})
            
            self.progress_changed.emit(i, total)
        self.finished_summary.emit({"total": total, "indexed": indexed})

class ModernCard(QFrame):
    def __init__(self, title, value="0", color="#FFD700"):
        super().__init__()
        self.setObjectName("ModernCard")
        self.setFrameShape(QFrame.StyledPanel)
        
        layout = QVBoxLayout(self)
        self.v_lbl = QLabel(value)
        self.v_lbl.setStyleSheet(f"font-size: 28px; font-weight: 800; color: {color};")
        self.v_lbl.setAlignment(Qt.AlignCenter)
        
        self.t_lbl = QLabel(title.upper())
        self.t_lbl.setStyleSheet("font-size: 11px; font-weight: bold; color: #999; letter-spacing: 1px;")
        self.t_lbl.setAlignment(Qt.AlignCenter)
        
        layout.addWidget(self.v_lbl)
        layout.addWidget(self.t_lbl)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 100))
        self.setGraphicsEffect(shadow)

    def set_value(self, v):
        self.v_lbl.setText(str(v))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 850)
        self.gsc = GSCClient()
        self.build_ui()
        self.apply_styles()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.root = QVBoxLayout(central)
        self.root.setContentsMargins(25, 25, 25, 25)
        self.root.setSpacing(20)

        # --- Header Section ---
        header = QHBoxLayout()
        title_container = QVBoxLayout()
        main_title = QLabel("EBS GSC <font color='#FFD700'>ELİT</font>")
        main_title.setStyleSheet("font-size: 26px; font-weight: 900; color: #FFF;")
        sub_title = QLabel("Search Console Performans ve İndeks Denetleyici")
        sub_title.setStyleSheet("color: #777; font-size: 13px;")
        title_container.addWidget(main_title)
        title_container.addWidget(sub_title)
        
        header.addLayout(title_container, 1)

        self.time_combo = QComboBox()
        self.time_combo.addItems(["24 Saat", "7 Gün", "28 Gün", "6 Ay", "12 Ay", "16 Ay", "KARŞILAŞTIRMALI (6-12-16)"])
        self.time_combo.setFixedWidth(220)
        
        self.login_btn = QPushButton("Hesabı Bağla")
        self.login_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.login_btn.clicked.connect(self.handle_login)
        
        header.addWidget(self.time_combo)
        header.addWidget(self.login_btn)
        self.root.addLayout(header)

        # --- Control Panel ---
        control_card = QFrame()
        control_card.setObjectName("MainControl")
        c_lay = QVBoxLayout(control_card)
        
        prop_h = QHBoxLayout()
        prop_h.addWidget(QLabel("Mülk Seçimi:"))
        self.prop_combo = QComboBox()
        self.prop_combo.setEditable(True)
        self.prop_combo.lineEdit().setPlaceholderText("Domain adını arayın veya seçin...")
        prop_h.addWidget(self.prop_combo, 1)
        
        self.sitemap_btn = QPushButton("Sitemap Çek")
        self.sitemap_btn.setObjectName("SitemapBtn")
        self.sitemap_btn.clicked.connect(self.fetch_sitemap)
        prop_h.addWidget(self.sitemap_btn)
        c_lay.addLayout(prop_h)

        self.url_text = QTextEdit()
        self.url_text.setPlaceholderText("Analiz edilecek URL listesini buraya yapıştırın...")
        self.url_text.setMaximumHeight(150)
        c_lay.addWidget(self.url_text)
        
        self.check_btn = QPushButton("ANALİZİ BAŞLAT")
        self.check_btn.setObjectName("ActionBtn")
        self.check_btn.setEnabled(False)
        self.check_btn.setFixedHeight(45)
        self.check_btn.clicked.connect(self.start_check)
        c_lay.addWidget(self.check_btn)
        
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        c_lay.addWidget(self.progress)
        
        self.root.addWidget(control_card)

        # --- Stats Section ---
        stats_h = QHBoxLayout()
        self.c_total = ModernCard("Analiz Edilen", "0", "#00BFFF")
        self.c_idx = ModernCard("İndekslenen", "0", "#32CD32")
        stats_h.addWidget(self.c_total)
        stats_h.addWidget(self.c_idx)
        self.root.addLayout(stats_h)

        # --- Table Section ---
        self.table = QTableWidget(0, 8)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.root.addWidget(self.table, 1)

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0B0B0E; }
            QWidget { color: #E0E0E0; font-family: 'Inter', 'Segoe UI', sans-serif; }
            
            QFrame#MainControl { 
                background-color: #15151A; 
                border-radius: 15px; 
                padding: 15px;
                border: 1px solid #222;
            }
            
            QFrame#ModernCard { 
                background-color: #15151A; 
                border-radius: 15px; 
                padding: 10px;
                border: 1px solid #222;
            }

            /* Input Alanları */
            QComboBox, QTextEdit, QLineEdit {
                background-color: #1C1C24;
                border: 1px solid #333;
                border-radius: 8px;
                padding: 8px;
                color: #FFF;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #1C1C24; selection-background-color: #2E2E38; }

            /* Butonlar */
            QPushButton {
                background-color: #252530;
                border: 1px solid #3d3d4d;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: bold;
                transition: all 0.3s ease;
            }
            QPushButton:hover { background-color: #323242; border-color: #505060; }
            
            QPushButton#ActionBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1DB954, stop:1 #191414);
                color: white;
                border: none;
                font-size: 15px;
            }
            QPushButton#ActionBtn:hover { background: #1ED760; }
            QPushButton#ActionBtn:disabled { background: #222; color: #555; }

            QPushButton#SitemapBtn { background-color: #3949AB; border: none; color: white; }
            QPushButton#SitemapBtn:hover { background-color: #4759D1; }

            /* Tablo */
            QTableWidget {
                background-color: #15151A;
                border: 1px solid #222;
                border-radius: 12px;
                gridline-color: transparent;
                selection-background-color: #22222E;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #15151A;
                color: #FFD700;
                padding: 12px;
                border: none;
                font-weight: bold;
                text-transform: uppercase;
                font-size: 11px;
            }
            
            /* Progress Bar */
            QProgressBar {
                background-color: #1C1C24;
                border-radius: 4px;
                height: 6px;
            }
            QProgressBar::chunk {
                background-color: #FFD700;
                border-radius: 4px;
            }
            
            QScrollBar:vertical { background: #0B0B0E; width: 8px; }
            QScrollBar::handle:vertical { background: #333; border-radius: 4px; }
        """)

    def handle_login(self):
        try:
            self.gsc.login()
            props = self.gsc.list_properties()
            self.prop_combo.clear()
            self.prop_combo.addItems(props)
            comp = QCompleter(props)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            self.prop_combo.setCompleter(comp)
            self.check_btn.setEnabled(True)
        except Exception as e: QMessageBox.critical(self, "Hata", str(e))

    def fetch_sitemap(self):
        site = self.prop_combo.currentText()
        if not site: return
        s_url = (site if site.endswith('/') else site + '/') + "sitemap.xml"
        try:
            r = requests.get(s_url, timeout=10)
            root = ET.fromstring(r.content)
            urls = [l.text for l in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
            self.url_text.setPlainText("\n".join(urls))
        except: QMessageBox.warning(self, "Hata", "Sitemap çekilemedi. Manuel giriş yapın.")

    def start_check(self):
        urls = [x.strip() for x in self.url_text.toPlainText().splitlines() if x.strip()]
        if not urls: return
        
        idx = self.time_combo.currentIndex()
        if idx == 6: # Compare
            mode = "COMPARE"
            headers = ["URL", "Durum", "6 Ay (T/G)", "12 Ay (T/G)", "16 Ay (T/G)", "Tarama", "Kapsam"]
        else:
            days_map = {0: 1, 1: 7, 2: 28, 3: 180, 4: 365, 5: 480}
            mode = days_map[idx]
            headers = ["URL", "Durum", "Tık", "Gös", "CTR", "Poz", "Tarama", "Kapsam"]
        
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(0)
        self.check_btn.setEnabled(False)
        
        self.worker = InspectionWorker(self.gsc, self.prop_combo.currentText(), urls, mode)
        self.worker.row_result.connect(self.add_row)
        self.worker.progress_changed.connect(lambda c, t: self.progress.setValue(int(c/t*100)))
        self.worker.finished_summary.connect(self.finish_check)
        self.worker.start()

    def finish_check(self, s):
        self.c_total.set_value(s["total"])
        self.c_idx.set_value(s["indexed"])
        self.check_btn.setEnabled(True)
        self.progress.setValue(100)

    def add_row(self, d):
        r = self.table.rowCount()
        self.table.insertRow(r)
        
        # URL item - tool tip eklendi
        u_item = QTableWidgetItem(d["url"])
        u_item.setToolTip(d["url"])
        self.table.setItem(r, 0, u_item)
        
        st = QTableWidgetItem(d["status"])
        if d["status"] == "BAŞARILI": st.setForeground(QColor("#32CD32"))
        elif d["status"] == "OLUMSUZ": st.setForeground(QColor("#FF4500"))
        st.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.table.setItem(r, 1, st)
        
        if d.get("compare"):
            self.table.setItem(r, 2, QTableWidgetItem(d["c6"]))
            self.table.setItem(r, 3, QTableWidgetItem(d["c12"]))
            self.table.setItem(r, 4, QTableWidgetItem(d["c16"]))
            self.table.setItem(r, 5, QTableWidgetItem(d["last_crawl"]))
            self.table.setItem(r, 6, QTableWidgetItem(d["coverage"]))
        else:
            self.table.setItem(r, 2, QTableWidgetItem(str(d["clicks"])))
            self.table.setItem(r, 3, QTableWidgetItem(str(d["imps"])))
            self.table.setItem(r, 4, QTableWidgetItem(d["ctr"]))
            self.table.setItem(r, 5, QTableWidgetItem(d["pos"]))
            self.table.setItem(r, 6, QTableWidgetItem(d["last_crawl"]))
            self.table.setItem(r, 7, QTableWidgetItem(d["coverage"]))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Global Font Ayarı
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
