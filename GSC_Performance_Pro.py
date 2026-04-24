import sys
import os
import requests
import json
import webbrowser
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QThread, Signal, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QColor, QFont, QCursor, QTextCursor, QPalette, QLinearGradient
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QFrame, QMessageBox, QMenu,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QCompleter, QFileDialog, QGraphicsDropShadowEffect
)

# --- PDF Desteği ---
try:
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors
except ImportError:
    pass

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- Konfigürasyon ---
APP_TITLE = "EBS GSC ELITE - ULTIMATE DARK"
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
            try: creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            except: creds = None
        if creds and creds.expired and creds.refresh_token:
            try: creds.refresh(Request())
            except: creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w", encoding="utf-8") as token: token.write(creds.to_json())
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
            if rows: return {"clicks": rows[0].get("clicks", 0), "impressions": rows[0].get("impressions", 0), "ctr": rows[0].get("ctr", 0), "position": rows[0].get("position", 0)}
        except: pass
        return {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}

class InspectionWorker(QThread):
    row_result = Signal(dict)
    progress_changed = Signal(int, int)
    finished_summary = Signal(dict)
    log_signal = Signal(str)

    def __init__(self, gsc_client, site_url, urls, mode):
        super().__init__()
        self.gsc_client, self.site_url, self.urls, self.mode = gsc_client, site_url, urls, mode
        self._is_running = True

    def stop(self): self._is_running = False

    def run(self):
        total, indexed = len(self.urls), 0
        for i, url in enumerate(self.urls, start=1):
            if not self._is_running: break
            try:
                raw_inspect = self.gsc_client.inspect_url(self.site_url, url)
                idx = raw_inspect.get("inspectionResult", {}).get("indexStatusResult", {})
                status = "BAŞARILI" if "indexed" in idx.get("verdict", "").lower() or "allowed" in idx.get("verdict", "").lower() else "OLUMSUZ"
                last_crawl = idx.get("lastCrawlTime", "-")
                if last_crawl != "-":
                    try: last_crawl = datetime.fromisoformat(last_crawl.replace("Z", "+00:00")).strftime("%d.%m.%Y")
                    except: pass
                
                data = {"url": url, "status": status, "last_crawl": last_crawl, "coverage": idx.get("coverageState", "-")}
                if self.mode == "COMPARE":
                    p6, p12, p16 = [self.gsc_client.get_performance(self.site_url, url, d) for d in [180, 365, 480]]
                    data.update({"compare": True, "c6": f"{int(p6['clicks'])}/{int(p6['impressions'])}", "c12": f"{int(p12['clicks'])}/{int(p12['impressions'])}", "c16": f"{int(p16['clicks'])}/{int(p16['impressions'])}"})
                else:
                    p = self.gsc_client.get_performance(self.site_url, url, self.mode)
                    data.update({"compare": False, "clicks": int(p['clicks']), "imps": int(p['impressions']), "ctr": f"{p['ctr']*100:.2f}%", "pos": f"{p['position']:.1f}"})
                
                if status == "BAŞARILI": indexed += 1
                self.row_result.emit(data)
            except: self.row_result.emit({"url": url, "status": "HATA", "compare": (self.mode == "COMPARE")})
            self.progress_changed.emit(i, total)
        self.finished_summary.emit({"total": total, "indexed": indexed})

class ModernCard(QFrame):
    def __init__(self, title, value="0", color="#FFD700"):
        super().__init__()
        self.setObjectName("ModernCard")
        layout = QVBoxLayout(self)
        self.v_lbl = QLabel(value); self.v_lbl.setStyleSheet(f"font-size: 32px; font-weight: 900; color: {color};")
        self.t_lbl = QLabel(title.upper()); self.t_lbl.setStyleSheet("font-size: 11px; font-weight: 800; color: #666; letter-spacing: 2px;")
        self.v_lbl.setAlignment(Qt.AlignCenter); self.t_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.v_lbl); layout.addWidget(self.t_lbl)
        
    def set_value(self, v): self.v_lbl.setText(str(v))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE); self.resize(1300, 950)
        self.gsc = GSCClient(); self.worker = None
        self.build_ui(); self.apply_styles()

    def build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        self.root = QVBoxLayout(central); self.root.setContentsMargins(30, 30, 30, 30); self.root.setSpacing(25)

        # --- Header ---
        header = QHBoxLayout()
        title_v = QVBoxLayout()
        title_main = QLabel("EBS GSC <font color='#FFD700'>ELITE</font>")
        title_main.setStyleSheet("font-size: 30px; font-weight: 900; color: #FFFFFF; letter-spacing: -1px;")
        title_v.addWidget(title_main)
        title_v.addWidget(QLabel("DIGITAL INTELLIGENCE & FORENSIC SEO", styleSheet="color: #444; font-size: 10px; font-weight: 800; letter-spacing: 3px;"))
        header.addLayout(title_v, 1)

        self.export_btn = QPushButton("DIŞARI AKTAR", objectName="ExportBtn")
        self.export_btn.clicked.connect(self.show_export_menu)
        header.addWidget(self.export_btn)

        self.login_btn = QPushButton("HESAP BAĞLA", objectName="LoginBtn")
        self.login_btn.clicked.connect(self.handle_login)
        header.addWidget(self.login_btn); self.root.addLayout(header)

        # --- Main Section ---
        mid = QHBoxLayout(); mid.setSpacing(25)
        
        # Sol Panel
        input_card = QFrame(objectName="MainControl"); i_lay = QVBoxLayout(input_card)
        i_lay.setSpacing(20)
        
        prop_h = QHBoxLayout()
        self.prop_combo = QComboBox(); self.prop_combo.setEditable(True)
        # Dropdown listesinin beyaz kalmasını önlemek için:
        self.prop_combo.view().setStyleSheet("background-color: #121214; color: white; selection-background-color: #252528;")
        self.prop_combo.lineEdit().setPlaceholderText("Domain seçin...")
        prop_h.addWidget(self.prop_combo, 1)
        self.sitemap_btn = QPushButton("SITEMAP ÇEK", objectName="BlueBtn")
        self.sitemap_btn.clicked.connect(self.fetch_sitemap)
        prop_h.addWidget(self.sitemap_btn); i_lay.addLayout(prop_h)

        self.url_text = QTextEdit(); self.url_text.setPlaceholderText("URL listesini buraya girin...")
        i_lay.addWidget(self.url_text)
        
        control_h = QHBoxLayout(); control_h.setSpacing(15)
        self.time_combo = QComboBox()
        self.time_combo.addItems(["24 Saat", "7 Gün", "28 Gün", "6 Ay", "12 Ay", "16 Ay", "KARŞILAŞTIRMALI"])
        self.time_combo.view().setStyleSheet("background-color: #121214; color: white; selection-background-color: #252528;")
        self.check_btn = QPushButton("ANALİZİ BAŞLAT", objectName="ActionBtn"); self.check_btn.setEnabled(False)
        self.check_btn.clicked.connect(self.start_check)
        self.stop_btn = QPushButton("DURDUR", objectName="StopBtn"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_check)
        
        control_h.addWidget(self.time_combo, 1)
        control_h.addWidget(self.check_btn, 2)
        control_h.addWidget(self.stop_btn, 1)
        i_lay.addLayout(control_h)
        
        self.progress = QProgressBar(); i_lay.addWidget(self.progress); mid.addWidget(input_card, 2)

        # Sağ Panel
        log_card = QFrame(objectName="LogControl"); l_lay = QVBoxLayout(log_card)
        l_lay.addWidget(QLabel("SYSTEM_LOGS", styleSheet="font-weight: 900; color: #1DB954; font-size: 9px; letter-spacing: 2px;"))
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("background: transparent; color: #1DB954; font-family: 'Consolas'; font-size: 11px; border: none;")
        l_lay.addWidget(self.log_box)
        mid.addWidget(log_card, 1); self.root.addLayout(mid)

        # --- Stats ---
        stats_h = QHBoxLayout(); stats_h.setSpacing(25)
        self.c_total = ModernCard("Taranan URL", "0", "#00B0FF")
        self.c_idx = ModernCard("İndeks Durumu", "0", "#1DB954")
        stats_h.addWidget(self.c_total); stats_h.addWidget(self.c_idx); self.root.addLayout(stats_h)

        # --- Table ---
        self.table = QTableWidget(0, 8); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemClicked.connect(self.handle_table_click)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.root.addWidget(self.table, 1)

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #050505; }
            QWidget { color: #E0E0E0; font-family: 'Segoe UI', sans-serif; }
            
            QFrame#MainControl { 
                background-color: #0C0C0E; 
                border-radius: 15px; 
                border: 1px solid #1A1A1C; 
            }
            QFrame#LogControl { 
                background-color: #070707; 
                border-radius: 15px; 
                border: 1px solid #1DB954; 
            }
            QFrame#ModernCard { 
                background-color: #0C0C0E; 
                border-radius: 15px; 
                border: 1px solid #1A1A1C; 
            }
            
            /* Girdiler ve Beyazlık Giderme */
            QComboBox, QTextEdit, QLineEdit { 
                background-color: #121214; 
                border: 1px solid #252528; 
                border-radius: 8px; 
                padding: 12px; 
                color: #FFFFFF;
                font-weight: 500;
            }
            
            /* ComboBox Açılır Liste Stili */
            QComboBox QAbstractItemView {
                background-color: #121214;
                color: white;
                selection-background-color: #FFD700;
                selection-color: black;
                border: 1px solid #252528;
                outline: 0px;
            }

            QComboBox:focus, QTextEdit:focus { border: 1px solid #FFD700; background-color: #161618; }
            
            /* Butonlar ve Hover Efektleri */
            QPushButton { 
                background-color: #1A1A1C; 
                border-radius: 8px; 
                padding: 12px 20px; 
                font-weight: 900; 
                color: #FFFFFF;
                border: 1px solid #252528;
                font-size: 11px;
            }
            QPushButton:hover { 
                background-color: #1DB954; 
                border-color: #1DB954; 
                color: #000;
                /* Parlama Efekti */
                border: 1px solid rgba(29, 185, 84, 0.5);
            }
            
            QPushButton#ActionBtn { background-color: #1DB954; color: #000; border: none; font-size: 13px; }
            QPushButton#ActionBtn:hover { background-color: #1ED760; box-shadow: 0 0 15px rgba(30, 215, 96, 0.4); }
            
            QPushButton#StopBtn { background-color: #E91E63; border: none; }
            QPushButton#StopBtn:hover { background-color: #FF4081; }
            
            QPushButton#ExportBtn { background-color: #FFD700; color: #000; border: none; }
            QPushButton#ExportBtn:hover { background-color: #FFEA00; }
            
            QPushButton#LoginBtn { border: 1px solid #FFD700; color: #FFD700; background: transparent; }
            QPushButton#LoginBtn:hover { background: #FFD700; color: #000; }
            
            /* Tablo */
            QTableWidget { 
                background-color: #0C0C0E; 
                gridline-color: #1A1A1C; 
                border-radius: 15px; 
                border: 1px solid #1A1A1C;
                outline: none;
            }
            QHeaderView::section { 
                background-color: #0C0C0E; 
                color: #FFD700; 
                border: none; 
                font-weight: 900; 
                padding: 15px;
                border-bottom: 2px solid #1A1A1C;
            }
            QTableWidget::item { padding: 12px; border-bottom: 1px solid #141416; }
            QTableWidget::item:selected { background-color: #1A1A1C; color: #FFD700; }
            
            /* Progress Bar */
            QProgressBar { background: #121214; border-radius: 6px; height: 10px; text-align: center; border: 1px solid #1A1A1C; }
            QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1DB954, stop:1 #1ED760); border-radius: 6px; }
            
            /* Scrollbar */
            QScrollBar:vertical { background: #050505; width: 10px; }
            QScrollBar::handle:vertical { background: #252528; border-radius: 5px; }
            QScrollBar::handle:vertical:hover { background: #333; }
        """)

    def add_log(self, msg): 
        t = datetime.now().strftime('%H:%M:%S')
        self.log_box.append(f"<span style='color:#333;'>[{t}]</span> <span style='color:#1DB954;'>{msg}</span>")
        self.log_box.moveCursor(QTextCursor.End)

    def handle_login(self):
        try:
            self.gsc.login(); props = self.gsc.list_properties()
            self.prop_combo.clear(); self.prop_combo.addItems(props)
            self.check_btn.setEnabled(True)
            self.add_log(f"BAĞLANTI_KURULDU: {len(props)} mülk aktif.")
        except Exception as e: self.add_log(f"ERİŞİM_REDDEDİLDİ: {e}")

    def fetch_sitemap(self):
        site = self.prop_combo.currentText()
        if not site: return
        s_url = (site if site.endswith('/') else site + '/') + "sitemap.xml"
        try:
            r = requests.get(s_url, timeout=10); root = ET.fromstring(r.content)
            urls = [l.text for l in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
            self.url_text.setPlainText("\n".join(urls))
            self.add_log(f"SİTEMAP_ÇEKİLDİ: {len(urls)} URL.")
        except: self.add_log("SİTEMAP_HATASI: Bağlantı veya XML hatası.")

    def start_check(self):
        urls = [x.strip() for x in self.url_text.toPlainText().splitlines() if x.strip()]
        if not urls: return
        idx = self.time_combo.currentIndex()
        mode = "COMPARE" if idx == 6 else {0:1, 1:7, 2:28, 3:180, 4:365, 5:480}[idx]
        headers = ["URL", "DURUM", "6 AY", "12 AY", "16 AY", "TARAMA", "KAPSAM"] if idx == 6 else ["URL", "DURUM", "TIK", "GÖS", "CTR", "POZ", "TARAMA", "KAPSAM"]
        self.table.setColumnCount(len(headers)); self.table.setHorizontalHeaderLabels(headers); self.table.setRowCount(0)
        self.check_btn.setEnabled(False); self.stop_btn.setEnabled(True); self.progress.setValue(0)
        self.worker = InspectionWorker(self.gsc, self.prop_combo.currentText(), urls, mode)
        self.worker.row_result.connect(self.add_row); self.worker.finished_summary.connect(self.finish_check); self.worker.progress_changed.connect(lambda c,t: self.progress.setValue(int(c/t*100)))
        self.worker.start(); self.add_log("ANALİZ_BAŞLATILDI...")

    def stop_check(self):
        if self.worker: self.worker.stop(); self.add_log("ANALİZ_DURDURULUYOR...")

    def finish_check(self, s):
        self.c_total.set_value(s["total"]); self.c_idx.set_value(s["indexed"])
        self.check_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        self.add_log("ANALİZ_TAMAMLANDI.")

    def add_row(self, d):
        r = self.table.rowCount(); self.table.insertRow(r)
        u_item = QTableWidgetItem(d["url"]); u_item.setForeground(QColor("#00B0FF"))
        f = u_item.font(); f.setUnderline(True); u_item.setFont(f)
        self.table.setItem(r, 0, u_item)
        
        st = QTableWidgetItem(d["status"])
        st.setForeground(QColor("#1DB954") if d["status"] == "BAŞARILI" else QColor("#E91E63"))
        st.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.table.setItem(r, 1, st)
        
        vals = [d.get("c6"), d.get("c12"), d.get("c16"), d.get("last_crawl"), d.get("coverage")] if d.get("compare") else [str(d.get("clicks",0)), str(d.get("imps",0)), d.get("ctr"), d.get("pos"), d.get("last_crawl"), d.get("coverage")]
        for i, v in enumerate(vals, 2): self.table.setItem(r, i, QTableWidgetItem(v))

    def handle_table_click(self, item):
        if item.column() == 0: webbrowser.open(item.text())

    def show_export_menu(self):
        """Dışa aktarma menüsünü ve temiz dosya filtrelerini hazırlar"""
        menu = QMenu(self)
        # Menü stili - Tamamen Dark
        menu.setStyleSheet("""
            QMenu { background-color: #121214; color: white; border: 1px solid #252528; }
            QMenu::item:selected { background-color: #FFD700; color: black; }
        """)
        
        # (Görünecek İsim, Kayıt Filtresi, Dosya Uzantısı)
        formats = [
            ("Excel (.xlsx)", "Excel Dosyası (*.xlsx)", ".xlsx"),
            ("CSV (.csv)", "CSV Dosyası (*.csv)", ".csv"),
            ("JSON (.json)", "JSON Dosyası (*.json)", ".json"),
            ("HTML (.html)", "HTML Dosyası (*.html)", ".html")
        ]
        
        for label, filter_str, ext in formats:
            # lambda içindeki varsayılan değerler döngü hatasını önler
            menu.addAction(label, lambda f=filter_str: self.export_data(f))
            
        menu.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))

    def export_data(self, filter_str):
        """PDF dışındaki formatların çalışmama sorununu çözer ve veriyi aktarır"""
        if self.table.rowCount() == 0:
            self.add_log("HATA: Aktarılacak veri bulunamadı.")
            return
        
        # Dosya kaydetme diyaloğu (Uzantı karmaşası burada çözüldü)
        path, _ = QFileDialog.getSaveFileName(self, "Raporu Kaydet", "", filter_str)
        
        if not path:
            return

        try:
            # Tablodaki verileri Pandas DataFrame'e aktar
            column_count = self.table.columnCount()
            row_count = self.table.rowCount()
            headers = [self.table.horizontalHeaderItem(i).text() for i in range(column_count)]
            
            table_data = []
            for r in range(row_count):
                row_dict = {}
                for c in range(column_count):
                    item = self.table.item(r, c)
                    row_dict[headers[c]] = item.text() if item else ""
                table_data.append(row_dict)
            
            df = pd.DataFrame(table_data)

            # Seçilen filtreye göre doğru Pandas fonksiyonunu çağır
            if ".xlsx" in filter_str:
                df.to_excel(path, index=False)
            elif ".csv" in filter_str:
                # Türkçe karakter sorunu olmaması için utf-8-sig
                df.to_csv(path, index=False, encoding='utf-8-sig')
            elif ".json" in filter_str:
                df.to_json(path, orient='records', force_ascii=False, indent=4)
            elif ".html" in filter_str:
                df.to_html(path, index=False)
            
            self.add_log(f"BAŞARILI: {os.path.basename(path)} kaydedildi.")
            
        except Exception as e:
            self.add_log(f"KRİTİK HATA: {str(e)}")
            QMessageBox.critical(self, "Dışa Aktarma Hatası", f"Dosya yazılamadı:\n{str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow(); w.show()
    sys.exit(app.exec())
