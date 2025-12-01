import time
import hashlib
import os
import stat
import math
import json
import shutil
import gzip
import threading
import ctypes
import smtplib 
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import Counter
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from storage import SecureStorage
from concurrent.futures import ThreadPoolExecutor

# --- НАЛАШТУВАННЯ UKR.NET ---
SENDER_EMAIL = os.getenv("EMAIL_USER", "your_email@ukr.net")
SENDER_PASSWORD = os.getenv("EMAIL_PASS", "your_app_password")
SMTP_SERVER = "smtp.ukr.net"
SMTP_PORT = 465 

kernel32 = ctypes.windll.kernel32
OPEN_EXISTING = 3
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
GENERIC_READ = 0x80000000
FILE_ATTRIBUTE_NORMAL = 0x80

class WindowsFileTracker(threading.Thread):
    def __init__(self, filepath, callback):
        super().__init__(daemon=True)
        self.filepath = filepath
        self.callback = callback
        self.running = False
        self.handle = None

    def _open_handle(self):
        self.handle = kernel32.CreateFileW(
            self.filepath, GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
        )
        return self.handle != -1

    def _get_current_path(self):
        if not self.handle or self.handle == -1: return None
        buf = ctypes.create_unicode_buffer(1024)
        res = kernel32.GetFinalPathNameByHandleW(self.handle, buf, 1024, 0)
        if res == 0: return None
        path = buf.value
        if path.startswith("\\\\?\\"): path = path[4:]
        return path

    def run(self):
        if not self._open_handle(): return
        self.running = True
        current_known_path = self.filepath
        while self.running:
            time.sleep(1.0)
            real_path = self._get_current_path()
            if real_path and os.path.normcase(real_path) != os.path.normcase(current_known_path):
                self.callback(current_known_path, real_path)
                current_known_path = real_path
                self.filepath = real_path
        kernel32.CloseHandle(self.handle)

    def stop(self):
        self.running = False

class IntegrityMonitor:
    # !!! ДОДАНО АРГУМЕНТ auto_restore !!!
    def __init__(self, targets, ui_callback=None, logging_enabled=True, alert_email="", auto_restore=True):
        self.ui_callback = ui_callback
        self.logging_enabled = logging_enabled
        self.alert_email = alert_email
        self.auto_restore = auto_restore # Флаг авто-відновлення
        
        self.targets = set(os.path.abspath(p) for p in targets)
        
        self.storage = SecureStorage()
        self.observer = Observer()
        self.audit_log = "security_audit.json"
        self.running = False
        self.trackers = [] 
        
        self.is_maintenance_mode = False 
        
        self.backup_dir = os.path.join(os.getcwd(), ".shadow_copies")
        if not os.path.exists(self.backup_dir): os.makedirs(self.backup_dir)
        self.worker_pool = ThreadPoolExecutor(max_workers=4)
        self.db_lock = threading.Lock()

    # !!! ОНОВЛЕННЯ НАЛАШТУВАНЬ !!!
    def update_settings(self, logging_enabled, alert_email, auto_restore):
        self.logging_enabled = logging_enabled
        self.alert_email = alert_email
        self.auto_restore = auto_restore
        print(f"Налаштування оновлено. Авто-відновлення: {auto_restore}")

    def force_restore_all(self):
        print("[Core] Увімкнено режим обслуговування (ігнорування подій).")
        self.is_maintenance_mode = True
        time.sleep(0.5) 
        
        all_files = self.storage.get_all_files().keys()
        count = 0
        try:
            for filepath in all_files:
                # Тут ігноруємо прапорець auto_restore, бо це ручне відновлення
                if self.restore_from_backup(filepath, manual=True):
                    count += 1
                    print(f"[Core] Відновлено: {os.path.basename(filepath)}")
        finally:
            self.is_maintenance_mode = False
            print("[Core] Режим обслуговування вимкнено. Охорона активна.")
        return count

    def add_target(self, path):
        path = os.path.abspath(path)
        self.targets.add(path)
        h = self.calculate_hash(path)
        if h: 
            self.safe_db_update(path, h)
            self.create_backup(path)
            print(f"[Core] Додано нову ціль: {path}")

    def remove_target(self, path):
        path = os.path.abspath(path)
        if path in self.targets:
            self.targets.remove(path)
            self.safe_db_delete(path)
            print(f"[Core] Ціль видалено з моніторингу: {path}")

    def send_email_alert_thread(self, filename, threat_type):
        if not self.alert_email or "@" not in self.alert_email: return 
        threading.Thread(target=self._send_email_logic, args=(filename, threat_type), daemon=True).start()

    def _send_email_logic(self, filename, threat_type):
        try:
            msg = MIMEMultipart()
            msg['From'] = SENDER_EMAIL
            msg['To'] = self.alert_email
            msg['Subject'] = f"ТРИВОГА: {os.path.basename(filename)}"
            body = f"СИСТЕМА БЕЗПЕКИ MAKHNIEI\nЗАГРОЗА: {threat_type}\nФайл: {filename}\nЧас: {datetime.now()}"
            msg.attach(MIMEText(body, 'plain'))
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
            server.quit()
            print(f"[Email] Лист надіслано.")
        except Exception as e: print(f"[Email Error] {e}")

    def on_file_moved_externally(self, old_path, new_path):
        if self.is_maintenance_mode: return

        if old_path in self.targets:
            self.targets.remove(old_path)
            self.targets.add(new_path)
            self.safe_db_delete(old_path)
            h = self.calculate_hash(new_path)
            if h:
                self.safe_db_update(new_path, h)
                self.create_backup(new_path)
            self.log_incident(f"Переміщення (Tracker): {old_path} -> {new_path}", "MOVED")
            print(f"[Info] Файл переміщено: {old_path} -> {new_path}")
            if self.ui_callback: self.ui_callback()

    def clear_audit_logs(self):
        try:
            if os.path.exists(self.audit_log): os.remove(self.audit_log)
            return True
        except: return False

    def create_backup(self, filepath):
        try:
            path_hash = hashlib.md5(filepath.encode()).hexdigest()
            backup_path = os.path.join(self.backup_dir, path_hash + ".gz")
            with open(filepath, 'rb') as f_in:
                with gzip.open(backup_path, 'wb', compresslevel=1) as f_out: 
                    shutil.copyfileobj(f_in, f_out)
            return True
        except: return False

    def safe_db_update(self, path, hash_sum):
        with self.db_lock: self.storage.add_or_update_file(path, hash_sum)

    def safe_db_delete(self, path):
        with self.db_lock: self.storage.delete_file(path)

    def calculate_entropy(self, filepath):
        try:
            with open(filepath, 'rb') as f: data = f.read()
            if not data: return 0.0
            entropy = 0; length = len(data); counter = Counter(data)
            for count in counter.values(): p_x = count / length; entropy += - p_x * math.log2(p_x)
            return entropy
        except: return 0.0

    def verify_signature(self, filepath):
        signatures = {'.png': b'\x89\x50\x4E\x47', '.jpg': b'\xFF\xD8\xFF', '.zip': b'\x50\x4B\x03\x04'}
        ext = os.path.splitext(filepath)[1].lower()
        required = signatures.get(ext)
        if not required: return True
        try:
            with open(filepath, 'rb') as f: return f.read(len(required)) == required
        except: return False

    def calculate_hash(self, filepath):
        sha256 = hashlib.sha256()
        try:
            with open(filepath, 'rb') as f:
                while True:
                    data = f.read(65536)
                    if not data: break
                    sha256.update(data)
            return sha256.hexdigest()
        except: return None

    def make_file_writable(self, filepath):
        try: os.chmod(filepath, stat.S_IWRITE)
        except: pass

    def make_file_readonly(self, filepath):
        try: os.chmod(filepath, stat.S_IREAD)
        except: pass

    def restore_from_backup(self, filepath, manual=False):
        path_hash = hashlib.md5(filepath.encode()).hexdigest()
        backup_path = os.path.join(self.backup_dir, path_hash + ".gz")
        if not os.path.exists(backup_path): return False

        if manual:
            # Для ручного відновлення не пишемо про блокування
            pass
        else:
            print(f"!!! СПРОБА ВІДНОВЛЕННЯ !!!")

        try:
            with gzip.open(backup_path, 'rb') as f_in: clean_data = f_in.read()
        except: return False

        try:
            self.make_file_writable(filepath)
            for attempt in range(20): 
                try:
                    with open(filepath, 'wb') as f_out:
                        f_out.write(clean_data)
                        f_out.flush()
                        os.fsync(f_out.fileno()) 
                        
                        if not manual:
                            print(f"Дані відновлено. Утримую блокування...")
                            self.send_email_alert_thread(filepath, "Ransomware / Модифікація")
                            time.sleep(2.0) 
                        else:
                            # При ручному відновленні не тримаємо довго
                            pass

                    self.make_file_readonly(filepath)
                    
                    if not self.is_maintenance_mode:
                        self.log_incident(f"Відновлено: {filepath}", "RECOVERY")
                    return True
                except PermissionError: time.sleep(0.05)
            return False
        except: return False

    def log_incident(self, message, risk_level):
        if not self.logging_enabled: return
        entry = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "risk": risk_level, "details": message}
        try:
            if os.path.exists(self.audit_log):
                with open(self.audit_log, "r", encoding="utf-8") as f: logs = json.load(f)
            else: logs = []
            logs.append(entry)
            with open(self.audit_log, "w", encoding="utf-8") as f: json.dump(logs, f, indent=4, ensure_ascii=False)
        except: pass

    def _process_single_file_init(self, filepath):
        if not os.path.exists(filepath): return None
        try:
            self.make_file_writable(filepath)
            h = self.calculate_hash(filepath)
            if h:
                self.create_backup(filepath)
                return (filepath, h)
        except: return None
        return None

    def scan_and_save_baseline(self):
        print(f"--- Ініціалізація системи захисту ---")
        self.storage.clear_database()
        
        all_files_to_scan = []
        for target in self.targets:
            if os.path.isfile(target):
                all_files_to_scan.append(target)
            elif os.path.isdir(target):
                for root, dirs, files in os.walk(target):
                    for file in files:
                        all_files_to_scan.append(os.path.join(root, file))

        batch_results = []
        valid_paths = []
        
        with ThreadPoolExecutor() as executor:
            results = list(executor.map(self._process_single_file_init, all_files_to_scan))
        
        for res in results:
            if res:
                path, h = res
                batch_results.append((path, h))
                valid_paths.append(path)
                self.log_incident(f"Файл під захистом: {path}", "INIT")

        self.storage.save_batch(batch_results)
        print(f"[Core] Взято під захист: {len(valid_paths)} об'єктів.")
        return valid_paths

    def process_new_file_async(self, filepath):
        if self.is_maintenance_mode: return

        for i in range(10):
            if not os.path.exists(filepath): return
            try:
                with open(filepath, "rb") as f: pass
                break
            except PermissionError: time.sleep(0.2)
        if not os.path.exists(filepath): return
        try:
            new_hash = self.calculate_hash(filepath)
            if new_hash:
                self.safe_db_update(filepath, new_hash)
                self.create_backup(filepath)
                self.log_incident(f"Новий файл: {filepath}", "CREATED")
                print(f"[Info] Новий файл захищено: {os.path.basename(filepath)}")
                if self.ui_callback: self.ui_callback()
        except: pass

    def start_monitoring(self):
        watch_dirs = set()
        for target in self.targets:
            if os.path.isfile(target):
                watch_dirs.add(os.path.dirname(target))
            elif os.path.isdir(target):
                watch_dirs.add(target)
        
        if not watch_dirs:
            print("Немає шляхів для моніторингу.")
            return

        self.start_observer_logic(watch_dirs)
        
        for target in self.targets:
            if os.path.isfile(target):
                t = WindowsFileTracker(target, self.on_file_moved_externally)
                self.trackers.append(t)
                t.start()

        self.running = True
        print(f"[*] Моніторинг запущено.")
        try:
            while self.running: time.sleep(1)
        except KeyboardInterrupt: self.stop_monitoring()

    def start_observer_logic(self, watch_dirs):
        self.observer = Observer()
        event_handler = EventHandler(self)
        try:
            for wd in watch_dirs:
                if os.path.exists(wd):
                    self.observer.schedule(event_handler, wd, recursive=True)
            self.observer.start()
        except: pass

    def stop_monitoring(self):
        self.running = False
        self.worker_pool.shutdown(wait=False)
        for t in self.trackers:
            t.stop()
            t.join()
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join()

class EventHandler(FileSystemEventHandler):
    def __init__(self, monitor_instance):
        self.monitor = monitor_instance
        self.cooldowns = {} 

    def _is_relevant(self, event_path):
        event_path = os.path.normcase(os.path.abspath(event_path))
        for target in self.monitor.targets:
            target_norm = os.path.normcase(target)
            if os.path.isfile(target):
                if event_path == target_norm: return True
            else:
                if event_path.startswith(target_norm): return True
        return False

    def on_moved(self, event):
        if self.monitor.is_maintenance_mode: return

        if event.is_directory: return
        if self._is_relevant(event.dest_path): 
             self.monitor.process_new_file_async(event.dest_path)
             self.monitor.log_incident(f"Переміщення: {event.src_path} -> {event.dest_path}", "MOVED")
             print(f"[Info] Переміщено: {os.path.basename(event.src_path)} -> {os.path.basename(event.dest_path)}")
        if self._is_relevant(event.src_path): self.monitor.safe_db_delete(event.src_path)
        if self.monitor.ui_callback: self.monitor.ui_callback()

    def on_created(self, event):
        if self.monitor.is_maintenance_mode: return

        if not event.is_directory and self._is_relevant(event.src_path):
            self.monitor.worker_pool.submit(self.monitor.process_new_file_async, event.src_path)

    def on_modified(self, event):
        if self.monitor.is_maintenance_mode: return

        if event.is_directory: return
        if not self._is_relevant(event.src_path): return
        
        filepath = event.src_path
        if not os.path.exists(filepath): return
        if time.time() - self.cooldowns.get(filepath, 0) < 2.0: return
        
        try:
            new_hash = self.monitor.calculate_hash(filepath)
            if not new_hash: return
            saved_files = self.monitor.storage.get_all_files() 
            old_hash = saved_files.get(filepath)
            
            if not old_hash:
                norm_fp = os.path.normcase(os.path.abspath(filepath))
                for db_p, db_h in saved_files.items():
                    if os.path.normcase(os.path.abspath(db_p)) == norm_fp:
                        old_hash = db_h
                        break
            
            if not old_hash:
                 self.monitor.safe_db_update(filepath, new_hash)
                 print(f"[Info] Файл оновлено в базі: {os.path.basename(filepath)}")
                 return

            if new_hash != old_hash:
                self.cooldowns[filepath] = time.time()
                print(f"\n[?] Виявлено зміну: {os.path.basename(filepath)}")
                
                # ... Перевірка типу атаки ...
                is_media = any(filepath.lower().endswith(x) for x in ['.jpg','.png','.zip','.pdf'])
                attack = False
                if is_media:
                    if not self.monitor.verify_signature(filepath): attack = True
                else:
                    if self.monitor.calculate_entropy(filepath) > 7.5: attack = True

                if attack:
                    print(f"!!! АТАКА !!!")
                    
                    # !!! ПЕРЕВІРКА НАЛАШТУВАННЯ auto_restore !!!
                    if self.monitor.auto_restore:
                        self.monitor.restore_from_backup(filepath)
                    else:
                        print(">> Авто-відновлення вимкнено. Тільки запис в лог.")
                        self.monitor.log_incident(f"АТАКА виявлена (без відновлення): {filepath}", "WARNING")
                    
                    self.cooldowns[filepath] = time.time()
                else:
                    print(">> Легітимна зміна. Бекап оновлено.")
                    self.monitor.create_backup(filepath)
                    self.monitor.safe_db_update(filepath, new_hash)
                    self.monitor.log_incident(f"Легітимна зміна: {filepath}", "MODIFIED")
        except: pass

    def on_deleted(self, event):
        if self.monitor.is_maintenance_mode: return

        if not event.is_directory and self._is_relevant(event.src_path):
            if self.monitor.target_type == "FILE": return 
            self.monitor.safe_db_delete(event.src_path)
            self.monitor.log_incident(f"Файл видалено: {event.src_path}", "DELETED")
            print(f"[Info] Файл видалено: {os.path.basename(event.src_path)}")
            if self.monitor.ui_callback: self.monitor.ui_callback()