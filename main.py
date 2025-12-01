import customtkinter as ctk
from tkinter import filedialog, Menu
import threading
import sys
import os
import json
from datetime import datetime
from core import IntegrityMonitor
import pystray
from PIL import Image, ImageDraw

ctk.set_appearance_mode("Dark")

CONFIG_FILE = "config.json"

# --- ПАЛІТРА КОЛЬОРІВ ---
COLOR_BG_MAIN = "#05202b"      
COLOR_BG_SIDEBAR = "#03161e"   
COLOR_TEXT_MAIN = "#d8dcd2"    
COLOR_TEXT_ACCENT = "#d8c2bc"  
COLOR_PRIMARY = "#1b4942"      
COLOR_SECONDARY = "#97aea8"    
COLOR_HOVER = "#2c6e63"        
COLOR_DANGER = "#4a1818"       
COLOR_DANGER_HOVER = "#6b2626" 

class TextRedirector(object):
    def __init__(self, text_widget):
        self.text_widget = text_widget
    def write(self, str_data):
        if str_data.strip():
            timestamp = datetime.now().strftime("[%H:%M:%S] ")
            output = timestamp + str_data
        else:
            output = str_data
        self.text_widget.configure(state="normal")
        self.text_widget.insert("end", output)
        self.text_widget.see("end")
        self.text_widget.configure(state="disabled")
    def flush(self): pass

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Makhniei") 
        self.settings = self.load_settings()
        
        w = self.settings.get("win_width", 1100)
        h = self.settings.get("win_height", 650)
        self.geometry(f"{w}x{h}")
        self.minsize(900, 550) # Трохи збільшив мінімальну ширину для комфорту
        self.configure(fg_color=COLOR_BG_MAIN)

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # --- НАЛАШТУВАННЯ СІТКИ (RESIZING) ---
        self.grid_columnconfigure(0, weight=0) # Сайдбар фіксований
        self.grid_columnconfigure(1, weight=1) # Центр розтягується
        self.grid_columnconfigure(2, weight=1) # !!! ПРАВА ПАНЕЛЬ ТЕПЕР ТЕЖ РОЗТЯГУЄТЬСЯ !!!
        self.grid_rowconfigure(0, weight=1)

        self.monitor = None
        self.targets_list = [] 
        self.is_drawer_open = False
        self.tray_icon = None 
        self.is_protection_active = False
        
        self.icon_red = self.create_status_icon("red")
        self.icon_green = self.create_status_icon("green")

        self.setup_sidebar()
        self.setup_center_area()
        self.setup_settings_area()
        self.setup_right_drawer()

        if "scaling" in self.settings:
            ctk.set_widget_scaling(self.settings["scaling"])

        self.show_console_view()
        sys.stdout = TextRedirector(self.console_log)

        threading.Thread(target=self.init_tray_icon, daemon=True).start()

    def create_status_icon(self, color):
        width, height = 64, 64
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0)) 
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, 56, 56), fill=color)
        return image

    def init_tray_icon(self):
        menu = (
            pystray.MenuItem('Відкрити', self.show_window_from_tray),
            pystray.MenuItem('Вихід', self.quit_app)
        )
        self.tray_icon = pystray.Icon("Makhniei", self.icon_red, "Makhniei Security", menu)
        self.tray_icon.run()

    def update_tray_status(self, is_active):
        if self.tray_icon:
            if is_active: self.tray_icon.icon = self.icon_green
            else: self.tray_icon.icon = self.icon_red

    def show_window_from_tray(self, icon, item):
        self.after(0, self.deiconify)

    def on_closing(self):
        self.save_window_geometry()
        if self.settings.get("minimize_to_tray", False):
            self.withdraw() 
        else:
            self.quit_app(None, None)

    def quit_app(self, icon=None, item=None):
        self.save_window_geometry()
        self.save_settings()
        if self.monitor: self.monitor.stop_monitoring()
        if self.tray_icon: self.tray_icon.stop()
        self.destroy()
        sys.exit()

    def save_window_geometry(self):
        self.settings["win_width"] = self.winfo_width()
        self.settings["win_height"] = self.winfo_height()

    def load_settings(self):
        default = {
            "save_logs": True, 
            "scaling": 1.0, 
            "alert_email": "", 
            "minimize_to_tray": False,
            "auto_restore": True,
            "win_width": 1100,
            "win_height": 650
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    return {**default, **data} 
            except: return default
        return default

    def save_settings(self):
        self.settings["alert_email"] = self.entry_email.get()
        self.settings["minimize_to_tray"] = self.switch_tray_var.get()
        self.settings["auto_restore"] = self.switch_restore_var.get()
        self.settings["win_width"] = self.winfo_width()
        self.settings["win_height"] = self.winfo_height()
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.settings, f)
        if self.monitor:
            self.monitor.update_settings(
                self.settings["save_logs"], 
                self.settings["alert_email"],
                self.settings["auto_restore"]
            )

    def setup_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=COLOR_BG_SIDEBAR)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(7, weight=1)

        self.logo = ctk.CTkLabel(self.sidebar, text="MAKHNIEI", 
                                 font=ctk.CTkFont(size=24, weight="bold"),
                                 text_color=COLOR_TEXT_ACCENT)
        self.logo.grid(row=0, column=0, padx=20, pady=(30, 20))

        btn_args = {
            "fg_color": "transparent",
            "text_color": COLOR_TEXT_MAIN,
            "hover_color": COLOR_PRIMARY,
            "border_width": 1,
            "border_color": COLOR_SECONDARY,
            "height": 35,
            "corner_radius": 10,
            "anchor": "center"
        }

        self.btn_dashboard = ctk.CTkButton(self.sidebar, text="Головна", command=self.show_console_view, **btn_args)
        self.btn_dashboard.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        ctk.CTkLabel(self.sidebar, text="ОБРАТИ", text_color=COLOR_SECONDARY, font=("Arial", 12, "bold")).grid(row=2, column=0, pady=(15, 5))

        self.select_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.select_frame.grid(row=3, column=0, padx=15, pady=0, sticky="ew")
        self.select_frame.grid_columnconfigure(0, weight=1)
        self.select_frame.grid_columnconfigure(1, weight=1)

        self.btn_folder = ctk.CTkButton(self.select_frame, text="Папку", command=self.select_folder_mode, 
                                        **btn_args, width=80)
        self.btn_folder.grid(row=0, column=0, padx=(0, 5), sticky="ew")

        self.btn_file = ctk.CTkButton(self.select_frame, text="Файл", command=self.select_file_mode, 
                                      **btn_args, width=80)
        self.btn_file.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        ctk.CTkLabel(self.sidebar, text="──────────────", text_color=COLOR_SECONDARY).grid(row=4, column=0, pady=10)

        self.btn_toggle = ctk.CTkButton(self.sidebar, text="ЗАПУСТИТИ", 
                                       fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER,
                                       text_color=COLOR_TEXT_MAIN, corner_radius=20,
                                       state="disabled", command=self.toggle_protection,
                                       height=40, font=("Arial", 14, "bold"))
        self.btn_toggle.grid(row=5, column=0, padx=20, pady=10, sticky="ew")

        self.btn_restore = ctk.CTkButton(self.sidebar, text="ВІДНОВИТИ ФАЙЛИ", 
                                         fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER, 
                                         text_color="white", corner_radius=20,
                                         state="disabled", command=self.manual_restore)
        self.btn_restore.grid(row=6, column=0, padx=20, pady=5, sticky="ew")

        self.btn_settings = ctk.CTkButton(self.sidebar, text="Налаштування", command=self.show_settings_view, **btn_args)
        self.btn_settings.grid(row=8, column=0, padx=20, pady=5, sticky="ew")
        
        self.btn_exit = ctk.CTkButton(self.sidebar, text="Вихід", fg_color="transparent", 
                                      text_color=COLOR_SECONDARY, hover_color=COLOR_DANGER,
                                      corner_radius=10,
                                      command=self.quit_app, height=30)
        self.btn_exit.grid(row=9, column=0, padx=20, pady=(5, 20))

    def setup_center_area(self):
        self.center_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.center_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.center_frame.grid_rowconfigure(1, weight=1)
        self.center_frame.grid_columnconfigure(0, weight=1)

        self.top_bar = ctk.CTkFrame(self.center_frame, height=40, fg_color="transparent")
        self.top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        self.lbl_status = ctk.CTkLabel(self.top_bar, text="ОЧІКУВАННЯ", 
                                       font=("Arial", 16, "bold"), text_color=COLOR_SECONDARY)
        self.lbl_status.pack(side="left")

        self.btn_burger = ctk.CTkButton(self.top_bar, text="Список файлів", width=120, 
                                        fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER,
                                        text_color=COLOR_TEXT_MAIN, corner_radius=15,
                                        command=self.toggle_drawer)
        self.btn_burger.pack(side="right")

        self.console_log = ctk.CTkTextbox(self.center_frame, font=("Consolas", 13), 
                                          fg_color=COLOR_BG_SIDEBAR, 
                                          text_color=COLOR_SECONDARY,
                                          corner_radius=10,
                                          border_width=1, border_color=COLOR_PRIMARY)
        self.console_log.grid(row=1, column=0, sticky="nsew")
        self.console_log.insert("0.0", "Система готова до роботи...\n")

    def setup_settings_area(self):
        self.settings_frame = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")
        
        ctk.CTkLabel(self.settings_frame, text="Налаштування програми", 
                     font=("Arial", 24, "bold"), text_color=COLOR_TEXT_ACCENT).pack(pady=20, anchor="w", padx=20)

        ctk.CTkLabel(self.settings_frame, text="Звіт (Логи):", 
                     font=("Arial", 14, "bold"), text_color=COLOR_TEXT_MAIN).pack(anchor="w", padx=20, pady=(10,0))
        
        self.switch_logs_var = ctk.BooleanVar(value=self.settings["save_logs"])
        self.switch_logs = ctk.CTkSwitch(self.settings_frame, text="Зберігати звіти у файл (security_audit.json)", 
                                         variable=self.switch_logs_var, command=self.on_setting_change, 
                                         font=("Arial", 14), 
                                         progress_color=COLOR_PRIMARY, 
                                         text_color=COLOR_TEXT_MAIN)
        self.switch_logs.pack(pady=10, anchor="w", padx=20)

        self.switch_restore_var = ctk.BooleanVar(value=self.settings.get("auto_restore", True))
        self.switch_restore = ctk.CTkSwitch(self.settings_frame, text="Автоматичне відновлення файлів при атаці", 
                                         variable=self.switch_restore_var, command=self.save_settings, 
                                         font=("Arial", 14), 
                                         progress_color=COLOR_PRIMARY, 
                                         text_color=COLOR_TEXT_MAIN)
        self.switch_restore.pack(pady=10, anchor="w", padx=20)

        ctk.CTkLabel(self.settings_frame, text="Фоновий режим:", 
                     font=("Arial", 14, "bold"), text_color=COLOR_TEXT_MAIN).pack(anchor="w", padx=20, pady=(10,0))
        
        self.switch_tray_var = ctk.BooleanVar(value=self.settings.get("minimize_to_tray", False))
        self.switch_tray = ctk.CTkSwitch(self.settings_frame, text="Згортати в трей при закритті вікна", 
                                         variable=self.switch_tray_var, command=self.save_settings, 
                                         font=("Arial", 14), 
                                         progress_color=COLOR_PRIMARY, 
                                         text_color=COLOR_TEXT_MAIN)
        self.switch_tray.pack(pady=10, anchor="w", padx=20)

        ctk.CTkLabel(self.settings_frame, text="Email для сповіщень:", 
                     font=("Arial", 14, "bold"), text_color=COLOR_TEXT_MAIN).pack(anchor="w", padx=20, pady=(20,0))
        
        self.entry_email = ctk.CTkEntry(self.settings_frame, width=300, placeholder_text="example@gmail.com")
        self.entry_email.insert(0, self.settings.get("alert_email", ""))
        self.entry_email.pack(anchor="w", padx=20, pady=5)
        
        self.btn_save_email = ctk.CTkButton(self.settings_frame, text="Зберегти Email", 
                                            fg_color=COLOR_PRIMARY, width=150, corner_radius=10,
                                            command=self.save_settings)
        self.btn_save_email.pack(anchor="w", padx=20, pady=5)

        self.btn_clear_logs = ctk.CTkButton(self.settings_frame, text="Видалити історію логів", 
                                            fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER, 
                                            corner_radius=10,
                                            command=self.clear_logs_ui)
        self.btn_clear_logs.pack(anchor="w", padx=20, pady=(30, 5))

        ctk.CTkLabel(self.settings_frame, text="Зовнішній вигляд (Масштаб):", 
                     font=("Arial", 14, "bold"), text_color=COLOR_TEXT_MAIN).pack(anchor="w", padx=20, pady=(30,0))
        
        self.slider_scaling = ctk.CTkSlider(self.settings_frame, from_=0.8, to=1.3, number_of_steps=5, 
                                            command=self.update_scale_label,
                                            button_color=COLOR_TEXT_ACCENT,
                                            progress_color=COLOR_PRIMARY)
        self.slider_scaling.set(self.settings.get("scaling", 1.0))
        self.slider_scaling.pack(anchor="w", padx=20, pady=10)
        
        self.slider_scaling.bind("<ButtonRelease-1>", self.apply_scaling)
        
        self.lbl_scaling_val = ctk.CTkLabel(self.settings_frame, text=f"Масштаб: {int(self.slider_scaling.get()*100)}%",
                                            text_color=COLOR_SECONDARY)
        self.lbl_scaling_val.pack(anchor="w", padx=20)

    def update_scale_label(self, value):
        self.lbl_scaling_val.configure(text=f"Масштаб: {int(value*100)}%")

    def apply_scaling(self, event):
        new_scale = round(self.slider_scaling.get(), 1)
        self.settings["scaling"] = new_scale
        ctk.set_widget_scaling(new_scale)
        self.save_settings()

    def clear_logs_ui(self):
        if self.monitor: self.monitor.clear_audit_logs()
        else:
            if os.path.exists("security_audit.json"): os.remove("security_audit.json")
        self.console_log.configure(state="normal")
        self.console_log.delete("0.0", "end")
        self.console_log.insert("0.0", "[System] Логи успішно очищено.\n")
        self.console_log.configure(state="disabled")

    def show_console_view(self):
        self.settings_frame.grid_forget()
        self.center_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.btn_dashboard.configure(fg_color=COLOR_BG_MAIN) 
        self.btn_settings.configure(fg_color="transparent")

    def show_settings_view(self):
        self.center_frame.grid_forget()
        self.settings_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=20)
        self.btn_dashboard.configure(fg_color="transparent")
        self.btn_settings.configure(fg_color=COLOR_BG_MAIN)

    def on_setting_change(self):
        self.settings["save_logs"] = self.switch_logs_var.get()
        self.save_settings()

    def setup_right_drawer(self):
        self.drawer_frame = ctk.CTkFrame(self, width=280, corner_radius=0, fg_color=COLOR_BG_SIDEBAR)
        self.drawer_frame.grid(row=0, column=2, sticky="nsew")
        
        ctk.CTkLabel(self.drawer_frame, text="ФАЙЛИ ПІД ЗАХИСТОМ", 
                     font=("Arial", 14, "bold"), text_color=COLOR_TEXT_ACCENT).pack(pady=15)
        
        self.file_list_scroll = ctk.CTkScrollableFrame(self.drawer_frame, fg_color="transparent")
        self.file_list_scroll.pack(fill="both", expand=True, padx=5, pady=5)

    def toggle_drawer(self):
        if self.is_drawer_open:
            self.drawer_frame.grid_forget()
            self.btn_burger.configure(fg_color=COLOR_PRIMARY)
        else:
            self.drawer_frame.grid(row=0, column=2, sticky="nsew")
            self.btn_burger.configure(fg_color=COLOR_HOVER)
        self.is_drawer_open = not self.is_drawer_open

    def select_folder_mode(self):
        path = filedialog.askdirectory()
        if path:
            self.add_target_to_list(path)

    def select_file_mode(self):
        paths = filedialog.askopenfilenames()
        if paths:
            for p in paths:
                self.add_target_to_list(p)

    def add_target_to_list(self, path):
        path = os.path.abspath(path)
        if path not in self.targets_list:
            self.targets_list.append(path)
            if not self.monitor:
                self.update_file_list_ui_manual()
            self.btn_toggle.configure(state="normal")
            
            if self.monitor:
                self.monitor.add_target(path)
                self.after(500, self.refresh_file_list_safe)

    def remove_target_from_list(self, path):
        if path in self.targets_list:
            self.targets_list.remove(path)
            if not self.monitor:
                self.update_file_list_ui_manual()
            else:
                self.monitor.remove_target(path)
                self.after(500, self.refresh_file_list_safe)
            
            if not self.targets_list and not self.monitor:
                self.btn_toggle.configure(state="disabled")

    def open_location(self, path):
        folder = os.path.dirname(path)
        if os.path.exists(folder):
            os.startfile(folder)

    def open_file(self, path):
        if os.path.exists(path):
            os.startfile(path)

    def update_file_list_ui_manual(self):
        if self.state() == 'withdrawn' or self.state() == 'iconic': return
        for widget in self.file_list_scroll.winfo_children(): widget.destroy()
        if not self.targets_list:
            ctk.CTkLabel(self.file_list_scroll, text="Список порожній", text_color="gray").pack(pady=10)
            return
        for path in self.targets_list:
            self.create_file_item(path, is_preview=True)

    def create_file_item(self, path, is_preview=False, display_name=None):
        # ЯКЩО ЦЕ ПРОСТО ШЛЯХ (наприклад, C:\Folder), ПОКАЗУЄМО ЙОГО БЕЗ ЗМІН
        if display_name:
            name = display_name
        else:
            name = os.path.basename(path)
            
        is_dir = os.path.isdir(path)
        icon = "[DIR]" if is_dir else "[FILE]"
        
        item_frame = ctk.CTkFrame(self.file_list_scroll, fg_color="transparent")
        item_frame.pack(fill="x", pady=2)
        
        btn = ctk.CTkButton(item_frame, text=f"{icon} {name}", 
                            fg_color="transparent", hover_color=COLOR_HOVER,
                            anchor="w", text_color=COLOR_TEXT_MAIN,
                            height=25)
        btn.pack(fill="x", padx=5)

        menu = Menu(self, tearoff=0)
        menu.add_command(label="Відкрити файл", command=lambda p=path: self.open_file(p))
        menu.add_command(label="Відкрити папку", command=lambda p=path: self.open_location(p))
        menu.add_separator()
        menu.add_command(label="Прибрати із захисту", command=lambda p=path: self.remove_target_from_list(p))

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        btn.bind("<Button-3>", show_menu)

    def toggle_protection(self):
        if not self.targets_list: return
        if self.is_protection_active:
            self.stop_protection()
        else:
            self.start_protection()

    def start_protection(self):
        self.is_protection_active = True
        self.btn_toggle.configure(text="ЗУПИНИТИ", fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER)
        self.btn_restore.configure(state="normal")
        self.lbl_status.configure(text="ЗАХИСТ АКТИВНО", text_color="#2ecc71")
        self.update_tray_status(is_active=True)
        self.show_console_view()
        threading.Thread(target=self.run_backend, daemon=True).start()

    def stop_protection(self):
        if self.monitor: self.monitor.stop_monitoring()
        self.is_protection_active = False
        self.btn_toggle.configure(text="ЗАПУСТИТИ", fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER)
        self.btn_restore.configure(state="disabled")
        self.lbl_status.configure(text="ЗУПИНЕНО", text_color=COLOR_DANGER_HOVER)
        self.update_tray_status(is_active=False)
        
        for widget in self.file_list_scroll.winfo_children(): widget.destroy()
        ctk.CTkLabel(self.file_list_scroll, text="Список порожній", text_color="gray").pack(pady=10)

    def manual_restore(self):
        if self.monitor:
            print("[UI] Запит на ручне відновлення...")
            threading.Thread(target=self.monitor.force_restore_all, daemon=True).start()

    def run_backend(self):
        logging_enabled = self.settings.get("save_logs", True)
        alert_email = self.settings.get("alert_email", "")
        auto_restore = self.settings.get("auto_restore", True)
        
        self.monitor = IntegrityMonitor(self.targets_list, 
                                        ui_callback=self.refresh_file_list_safe, 
                                        logging_enabled=logging_enabled,
                                        alert_email=alert_email,
                                        auto_restore=auto_restore)
        files = self.monitor.scan_and_save_baseline() 
        self.after(0, lambda: self.update_file_list_ui_live(files))
        self.monitor.start_monitoring()

    def refresh_file_list_safe(self):
        self.after(0, self._refresh_logic)

    def _refresh_logic(self):
        if self.monitor and self.monitor.storage:
            try:
                current_files = list(self.monitor.storage.get_all_files().keys())
                self.update_file_list_ui_live(current_files)
            except: pass

    def update_file_list_ui_live(self, files):
        if self.state() == 'withdrawn' or self.state() == 'iconic':
            return

        for widget in self.file_list_scroll.winfo_children():
            widget.destroy()

        clean_files = [f for f in files if os.path.basename(f).lower() != "desktop.ini"]
        clean_files.sort()
        
        header = ctk.CTkLabel(self.file_list_scroll, text=f"Всього об'єктів: {len(clean_files)}", text_color="gray")
        header.pack(pady=5)

        if not clean_files:
            ctk.CTkLabel(self.file_list_scroll, text="Файлів немає").pack()
        else:
            for f in clean_files:
                display_name = os.path.basename(f)
                
                # !!! ВИПРАВЛЕННЯ: ПРИМУСОВО ТІЛЬКИ ІМ'Я ФАЙЛУ !!!
                # (Я прибрав логіку з relpath, бо ви просили тільки ім'я)
                display_name = os.path.basename(f) 
                
                self.create_file_item(f, display_name=display_name)

if __name__ == "__main__":
    app = App()
    app.mainloop()